"""Notifier integration core (async) with config flow support."""

import asyncio
import logging
from typing import Any, Dict, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers import event as event_helper

_LOGGER = logging.getLogger(__name__)

DOMAIN = "notifier"
DEFAULT_THRESHOLD = 1000

async def async_setup(hass: HomeAssistant, config: Dict[str, Any]) -> bool:
    """Basic setup. We rely on config entries (UI)."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up notifier from a config entry created in the UI."""
    conf = entry.data
    options = entry.options

    home_sensor = conf.get("home_occupancy_sensor_id")
    threshold = conf.get("proximity_threshold", DEFAULT_THRESHOLD)
    persons = options.get("persons", conf.get("persons", []))

    manager = NotifierManager(hass, entry.entry_id, home_sensor, threshold, persons)
    hass.data[DOMAIN][entry.entry_id] = manager

    await manager.async_start()

    async def _on_stop(event):
        await manager.async_stop()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    manager: NotifierManager = hass.data[DOMAIN].pop(entry.entry_id, None)
    if manager:
        await manager.async_stop()
    return True

class NotifierManager:
    """Manager that handles events and notifications."""

    def __init__(self, hass: HomeAssistant, entry_id: str, home_sensor: str, threshold: int, persons: List[Dict[str, Any]]):
        self.hass = hass
        self.entry_id = entry_id
        self.home_sensor = home_sensor
        self.threshold = threshold
        self.persons = persons or []
        self.staged = []
        self.watchers = {}  # tag -> remove_callback
        self._listeners = []

    async def async_start(self):
        """Register event listeners and state trackers."""
        _LOGGER.debug("Starting NotifierManager for entry %s", self.entry_id)
        self.hass.bus.async_listen("NOTIFIER", self._handle_notifier_event)
        self.hass.bus.async_listen("NOTIFIER_DISCARD", self._handle_discard_event)
        self.hass.bus.async_listen("mobile_app_notification_action", self._handle_mobile_action)

        # Track home occupancy changes
        remove = event_helper.async_track_state_change(
            self.hass, self.home_sensor, self._home_state_changed
        )
        self._listeners.append(remove)
        _LOGGER.info("Notifier integration started (entry %s)", self.entry_id)

    async def async_stop(self):
        """Remove listeners and watchers."""
        _LOGGER.debug("Stopping NotifierManager for entry %s", self.entry_id)
        for remove in list(self._listeners):
            try:
                remove()
            except Exception:
                pass
        self._listeners.clear()

        for tag, remove in list(self.watchers.items()):
            try:
                remove()
            except Exception:
                pass
        self.watchers.clear()

    @callback
    def _home_state_changed(self, entity, old, new):
        """When home becomes occupied, send staged notifications."""
        if new == "on" and self.staged:
            _LOGGER.debug("Home occupied, sending staged notifications")
            while self.staged:
                data = self.staged.pop(0)
                asyncio.create_task(self._send_to_present(data))

    async def _handle_notifier_event(self, event):
        data = event.data or {}
        _LOGGER.debug("NOTIFIER event received: %s", data)
        action = data.get("action")
        tag = data.get("tag")

        if action:
            # Generic handlers
            if action == "send_to_all":
                await self._send_to_all(data)
            elif action == "send_to_present":
                await self._send_to_present(data)
            elif action == "send_to_absent":
                await self._send_to_absent(data)
            elif action == "send_to_nearest":
                await self._send_to_nearest(data)
            elif action == "send_when_present":
                await self._send_when_present(data)
            elif action.startswith("send_to_"):
                # send_to_<name>
                name = action[len("send_to_"):]
                for p in self.persons:
                    if p.get("name") == name:
                        await self._send_to_person(data, p)

        if data.get("persistent"):
            await self._call_notify_service("persistent_notification", {"title": data.get("title", ""), "message": data.get("message", "")}, domain="notify")

        # watchers "until"
        if "until" in data and tag:
            for watcher in data["until"]:
                entity_id = watcher.get("entity_id")
                new_state = str(watcher.get("new_state"))
                remove = event_helper.async_track_state_change(
                    self.hass, entity_id,
                    lambda e, o, n, kwargs=None: asyncio.create_task(self._until_callback(e, o, n, tag)),
                    to_state=new_state
                )
                self.watchers[tag] = remove
                _LOGGER.debug("Watcher added for tag %s on %s -> %s", tag, entity_id, new_state)

    async def _until_callback(self, entity, old, new, tag):
        _LOGGER.debug("Until watcher triggered for tag %s", tag)
        await self._clear_notifications(tag)

    async def _handle_discard_event(self, event):
        tag = (event.data or {}).get("tag")
        if tag:
            await self._clear_notifications(tag)

    async def _handle_mobile_action(self, event):
        data = event.data or {}
        tag = data.get("tag") or data.get("action_data", {}).get("tag")
        if tag:
            await self._clear_notifications(tag)

    async def _clear_notifications(self, tag):
        _LOGGER.info("Clearing notifications with tag %s", tag)
        for p in self.persons:
            svc = p.get("notification_service")
            if svc:
                if "/" in svc:
                    domain, service = svc.split("/", 1)
                else:
                    domain, service = "notify", svc
                await self._call_notify_service(service, {"message": "clear_notification"}, domain=domain)
        remove = self.watchers.pop(tag, None)
        if remove:
            try:
                remove()
            except Exception:
                pass

    async def _call_notify_service(self, service, data, domain="notify"):
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
            _LOGGER.debug("Called service %s.%s with %s", domain, service, data)
        except Exception as e:
            _LOGGER.error("Error calling service %s.%s: %s", domain, service, e)

    # --- send helpers ---
    async def _send_to_person(self, data, person):
        svc = person.get("notification_service")
        if not svc:
            return
        if "/" in svc:
            domain, service = svc.split("/", 1)
        else:
            domain, service = "notify", svc
        payload = self._build_payload(data)
        await self._call_notify_service(service, {"title": data.get("title", ""), "message": data.get("message", ""), **payload}, domain=domain)

    async def _send_to_all(self, data):
        for p in self.persons:
            await self._send_to_person(data, p)

    async def _send_to_present(self, data):
        for p in self.persons:
            state = self.hass.states.get(p.get("id"))
            home = state.state == "home" if state else False
            prox_state = self.hass.states.get(p.get("proximity_id"))
            prox = self._safe_float(prox_state.state) if prox_state else 9999
            if home or prox <= self.threshold:
                await self._send_to_person(data, p)

    async def _send_to_absent(self, data):
        for p in self.persons:
            state = self.hass.states.get(p.get("id"))
            home = state.state == "home" if state else False
            prox_state = self.hass.states.get(p.get("proximity_id"))
            prox = self._safe_float(prox_state.state) if prox_state else 9999
            if not home or prox > self.threshold:
                await self._send_to_person(data, p)

    async def _send_to_nearest(self, data):
        proximities = []
        for p in self.persons:
            prox_state = self.hass.states.get(p.get("proximity_id"))
            proximities.append(self._safe_float(prox_state.state) if prox_state else 9999)
        if not proximities:
            return
        minp = min(proximities)
        for p in self.persons:
            prox_state = self.hass.states.get(p.get("proximity_id"))
            prox = self._safe_float(prox_state.state) if prox_state else 9999
            if prox <= minp + self.threshold:
                await self._send_to_person(data, p)

    async def _send_when_present(self, data):
        home_state = self.hass.states.get(self.home_sensor)
        if home_state and home_state.state == "on":
            await self._send_to_present(data)
        else:
            self.staged.append(data)

    def _build_payload(self, data):
        payload = {}
        if "callback" in data:
            payload["actions"] = [{"action": cb.get("event"), "title": cb.get("title")} for cb in data.get("callback", [])]
        if "tag" in data:
            payload["tag"] = data.get("tag")
        if "image_url" in data:
            payload["image"] = data.get("image_url")
        if "click_url" in data:
            payload["clickAction"] = data.get("click_url")
        if "color" in data:
            payload["color"] = data.get("color")
        return payload

    def _safe_float(self, value):
        try:
            return float(value)
        except Exception:
            return 9999