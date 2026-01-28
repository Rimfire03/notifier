"""Config flow for Notifier integration (UI)."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from . import DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("home_occupancy_sensor_id", default="binary_sensor.home_occupied"):
        selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
    vol.Required("proximity_threshold", default=1000): int,
})

class NotifierConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Notifier."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

        # Create entry with basic config; persons will be managed in options
        return self.async_create_entry(title="Notifier", data=user_input)