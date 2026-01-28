"""Options flow to manage persons and threshold with notify service selector and help page."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from . import DOMAIN

HELP_TEXT = """
**Notifier — Aide rapide**

**But**  
Cette intégration reçoit des événements `NOTIFIER` et envoie des notifications aux appareils configurés.

**Format d'événement (exemple)**  
```yaml
event_type: NOTIFIER
event_data:
  action: send_to_thomas
  title: "Alerte"
  message: "Porte ouverte"
  tag: "porte_entrée"
  callback:
    - event: "ack"
      title: "J'ai vu"
  image_url: "https://..."
  click_url: "https://..."