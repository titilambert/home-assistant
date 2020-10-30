"""Support Device tracking for TP-Link Omada Controller."""
import logging
from typing import Dict
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.components.device_tracker import SOURCE_TYPE_ROUTER
from homeassistant.components.device_tracker.config_entry import ScannerEntity
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN as OMADA_DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities) -> None:
    """Validate the configuration and return a DD-WRT scanner."""
    omada = hass.data[OMADA_DOMAIN][entry.entry_id]
    tracked = set()

    @callback
    def update_omada():
        """Update the values of the router."""
        add_entities(omada, async_add_entities, tracked)

    omada.listeners.append(
        async_dispatcher_connect(hass, omada.signal_device_new, update_omada)
    )

    update_omada()


@callback
def add_entities(omada, async_add_entities, tracked):
    """Add new tracker entities from the omada."""
    new_tracked = []

    for mac, device in omada.devices.items():
        if mac in tracked:
            continue

        new_tracked.append(TplinkOmadaDevice(omada, device))
        tracked.add(mac)

    if new_tracked:
        async_add_entities(new_tracked, True)


DEVICE_TYPE = {
    'unknown': 'mdi:help-network',
}


class TplinkOmadaDevice(ScannerEntity):
    """This class queries a TP-Link Omada Controller."""

    def __init__(self, omada, device):
        """Initialize the scanner."""
        self._omada = omada
        self._name = device['hostName']
        self._mac = device['mac']
        self._icon = DEVICE_TYPE.get(device.get('deviceType'), 'mdi:help-network')
        self._active = False
        self._attrs = {}

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._mac

    @property
    def name(self) -> str:
        """Return the name."""
        return self._name

    @property
    def is_connected(self):
        """Return true if the device is connected to the network."""
        return self._active

    @property
    def source_type(self) -> str:
        """Return the source type."""
        return SOURCE_TYPE_ROUTER

    @property
    def icon(self) -> str:
        """Return the icon."""
        return self._icon

    @property
    def device_state_attributes(self) -> Dict[str, any]:
        """Return the attributes."""
        return self._attrs

    @property
    def device_info(self) -> Dict[str, any]:
        """Return the device information."""
        return {
            "connections": {(CONNECTION_NETWORK_MAC, self._mac)},
            "identifiers": {(OMADA_DOMAIN, self.unique_id)},
            "name": self.name,
            # "manufacturer": self._manufacturer,
        }

    @property
    def should_poll(self) -> bool:
        """No polling needed."""
        return False

    @callback
    def async_update_state(self) -> None:
        """Update the Omada device."""
        device = self._omada.devices.get(self._mac, {})
        # {'mac': 'B8:27:EB:CB:2A:AD', 'name': 'zarbi-t', 'hostName': 'raspberrypi', 'deviceType': 'unknown', 'ip': '192.168.15.6', 'connectType': 1, 'connectDevType': 'ap', 'wireless': True, 'ssid': 'pokemonland_nomap', 'signalLevel': 55, 'signalRank': 3, 'wifiMode': 3, 'apName': 'phyllali', 'apMac': 'B0-BE-76-86-07-10', 'radioId': 1, 'channel': 36, 'rxRate': 150000, 'txRate': 150000, 'powerSave': False, 'rssi': -68, 'activity': 507, 'trafficDown': 144657535, 'trafficUp': 398627760, 'uptime': 91529, 'lastSeen': 1603859352923, 'authStatus': 0, 'guest': False, 'active': True, 'manager': False, 'downPacket': 294343, 'upPacket': 506349}
        self._name = device['hostName']
        self._active = device.get("active", False)
        self._attrs = {
            "ssid": device.get('ssid'),
            "uptime": device.get('uptime'),
            "ap_name": device.get('apName'),
            "mac": self._mac.replace("-", ":"),
            "ip": device.get('ip'),
            "activity_speed": device.get("activity"),
            "last_time_seen": datetime.fromtimestamp(device["lastSeen"] / 1000),
        }

        self._attrs = {k: v for k, v in self._attrs.items() if v is not None}

    @callback
    def async_on_demand_update(self):
        """Update state."""
        self.async_update_state()
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Register state update callback."""
        self.async_update_state()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._omada.signal_device_update,
                self.async_on_demand_update,
            )
        )
