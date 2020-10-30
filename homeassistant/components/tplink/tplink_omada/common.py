"""Support for TP-Link Omada."""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import aiodns
from aiodns.error import DNSError
import async_timeout

from homeassistant import exceptions
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_DNSRESOLVE,
    CONF_SSLVERIFY,
    DOMAIN,
    API_ABOUT_PATH,
    API_LOGIN_PATH,
    API_FREQ_DISTRIBUTION_PATH,
    API_DEVICES_PATH,
    API_CLIENTS_PATH,
    API_INSIGHT_CLIENTS_PATH,
    API_SETTINGS_WLANS,
    API_SETTINGS_SSIDS,
    SENSOR_AP_SETTINGS_DICT,
    SENSOR_AP_STATS_DICT,
    SENSOR_DICT,
    SENSOR_SSID_SETTINGS_DICT,
    BAND_INFO,
)

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=30)


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""


async def login(base_url, username, password, timeout, httpsession, cookies):
    """Login to the Omada Controller.

    This function returns an token

    :note: host variable should includes scheme and port (if not standard)
           ie https://192.168.1.2:8043
    """
    # Get SessionID
    _LOGGER.debug("Logging in")
    with async_timeout.timeout(timeout):
        res = await httpsession.get(base_url, allow_redirects=False)
        cookies['TPEAP_SESSIONID'] = res.cookies['TPEAP_SESSIONID'].value
    # Login
    login_data = {"username": username, "password": password}
    with async_timeout.timeout(timeout):
        res = await httpsession.post(
            base_url + API_LOGIN_PATH,
            cookies=cookies,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            data=json.dumps(login_data),
        )
    res_json = await res.json()
    if res_json.get("msg") != "Log in successfully.":
        _LOGGER.exception(
            "Omada Controller didn't respond with JSON. "
            "Check if credentials are correct"
        )
        raise InvalidAuth(
            "Omada Controller didn't respond with JSON. "
            "Check if credentials are correct"
        )
    # Get token
    res_json = await res.json()
    token = res_json["result"]["token"]
    return base_url, token


class OmadaData:
    """Omada Client class."""

    def __init__(self, hass, entry):
        """Initialize the data object."""
        self._hass = hass
        self._entry = entry
        self.name = entry.data[CONF_NAME]
        self.host = entry.data[CONF_HOST]
        self.username = entry.data[CONF_USERNAME]
        self.password = entry.data[CONF_PASSWORD]
        self.timeout = entry.data[CONF_TIMEOUT]

        self.listeners = []
        self.devices: Dict[str, Any] = {}
        self._unsub_dispatcher = None

        self.cookies = {}
        self._headers = {"Content-Type": "application/json; charset=UTF-8"}
        self.dns_resolver = None
        if entry.data[CONF_DNSRESOLVE]:
            self.dns_resolver = aiodns.DNSResolver()
        verify_tls = entry.data[CONF_SSLVERIFY]
        self.httpsession = async_get_clientsession(hass, verify_tls)

        self.version = None
        self.available = True
        self.data = {}
        self.ssid_stats = {}
        self.ssid_attrs = {}
        self.access_points_stats = {}
        self.access_points_settings = {}
        self._token = None
        self._base_url = None

    async def setup(self) -> None:
        ret = await self.async_update()
        if ret is None:
            _LOGGER.exception("Failed to connect to Omada")
            return ConfigEntryNotReady

        self._unsub_dispatcher = async_track_time_interval(
            self._hass, self.async_update, SCAN_INTERVAL
        )

    async def _http_get_request(self, path, params=None, allow_redirects=False):
        if params is None:
            params = {"token": self._token}
        else:
            params["token"] = self._token
        with async_timeout.timeout(self.timeout):
            res = await self.httpsession.get(
                self._base_url + path,
                params=params,
                cookies=self.cookies,
                allow_redirects=allow_redirects,
                headers=self._headers,
            )
            res_json = await res.json()

        return res_json

    async def login(self):
        """Login to the Omada Controller."""
        logged = await login(
            self.host, self.username, self.password, self.timeout, self.httpsession, self.cookies,
        )
        if not isinstance(logged, tuple) or len(logged) != 2:
            _LOGGER.error("Unable to login to Omada Controller %s: %s", self.host, logged)
            return False
        self._base_url = logged[0]
        self._token = logged[1]

        return True

    async def fetch_version(self):
        """Get the current version of the Omada Controller."""
        res_json = await self._http_get_request(API_ABOUT_PATH)
        if res_json["errorCode"] != 0:
            _LOGGER.error("Error fetching version: %s", res_json["msg"])
            return
        self.version = res_json["result"]["controllerVersion"]

    async def fetch_global_stats(self):
        """Fetch the global statistics of the Omada Controller."""
        res_json = await self._http_get_request(API_FREQ_DISTRIBUTION_PATH)
        if res_json["errorCode"] != 0:
            _LOGGER.error("Error fetching global stats: %s", res_json["msg"])
            return

        for sensor_name in SENSOR_DICT:
            if sensor_name in res_json["result"]:
                self.data[sensor_name] = res_json["result"][sensor_name]

    async def fetch_ssid_stats(self):
        """Get statistics for each SSID."""
        for device in self.devices.values():
            if not device['active']:
                continue
            ssid = device['ssid']
            self.ssid_stats.setdefault(ssid, {'connected_clients': 0,
                                              'traffic_received': 0,
                                              'traffic_sent': 0,
                                              'activity_speed': 0,
                                              })
            self.ssid_stats[ssid]['connected_clients'] += 1
            self.ssid_stats[ssid]['traffic_received'] += device['trafficDown']
            self.ssid_stats[ssid]['traffic_sent'] += device['trafficUp']
            self.ssid_stats[ssid]['activity_speed'] += device['activity']

    async def fetch_ap_stats(self):
        """Get Access point stats."""
        # Get devices
        devices = await self._http_get_request(API_DEVICES_PATH)
        # TODO handle error

        for device in devices['result']:
            if device.get('type') != "ap":
                continue
            ap_mac = device['mac']
            for sensor_name in SENSOR_AP_STATS_DICT:
                if sensor_name not in device:
                    _LOGGER.warning("Sensor not find: %s", sensor_name)
                    continue
                self.access_points_stats.setdefault(ap_mac, {})
                self.access_points_stats[ap_mac][sensor_name] = device[
                    sensor_name
                ]
            for sensor_name in SENSOR_AP_SETTINGS_DICT:
                if sensor_name not in device:
                    _LOGGER.warning("Sensor not find: %s", sensor_name)
                    continue
                self.access_points_settings.setdefault(ap_mac, {})
                setting_name = SENSOR_AP_SETTINGS_DICT[sensor_name]
                self.access_points_settings[ap_mac][setting_name] = device[
                    sensor_name
                ]

    async def fetch_clients_list(self):
        """Get the list of the connected clients to the access points."""
        _LOGGER.debug("Loading wireless clients from Omada Controller...")
        new_device = False

        online_devices = await self._browse_client_page(API_CLIENTS_PATH)
        insight_devices = await self._browse_client_page(API_INSIGHT_CLIENTS_PATH)

        list_of_devices = {}
        for device in insight_devices:
            mac = device["mac"]
            if device["mac"] not in [d["mac"] for d in online_devices]:
                # This is an offline device
                device["active"] = False
            else:
                # This is an online device
                online_device = [d for d in online_devices if d["mac"] == device["mac"]][0]
                device.update(online_device)
                if self.dns_resolver and "ip" in device:
                    try:
                        result = await self.dns_resolver.gethostbyaddr(device["ip"])
                        device['hostName'] = result.name.split(".", 1)[0]
                    except DNSError:
                        _LOGGER.debug("Can not resolve %s", device["ip"])

                if self.devices.get(device["mac"]) is None:
                    # New device detected
                    new_device = True

            # Set default name from the mac address
            if 'hostName' not in device:
                device['hostName'] = device["mac"].replace("-", ":").lower()

            device['hostName'] = device['hostName'].lower()
            list_of_devices[device["mac"]] = device

        if _LOGGER.level <= logging.DEBUG:
            msgs = []
            for mac, data in list_of_devices.items():
                name = data['hostName']
                msgs.append(f"{mac}: {name}")
            _LOGGER.debug("\nDevice count: %s\n%s", len(msgs), "\n".join(msgs))

        self.devices = list_of_devices

        async_dispatcher_send(self._hass, self.signal_device_update)
        if new_device:
            async_dispatcher_send(self._hass, self.signal_device_new)

    async def fetch_ssid_attributes(self):
        """Get SSID attributes."""
        # Get sites
        res_json = await self._http_get_request(API_SETTINGS_WLANS)

        if res_json["errorCode"] != 0:
            _LOGGER.error("Error fetching sites: %s", res_json["msg"])
            return

        ssids = []
        for site in res_json["result"]["data"]:
            site_id = site["wlanId"]
            res_json = await self._http_get_request(API_SETTINGS_SSIDS.format(site_id))
            if res_json["errorCode"] != 0:
                _LOGGER.error("Error fetching sites: %s", res_json["msg"])
                return

            ssids.extend(res_json["result"]["data"])

        ssid_id_dict = {}

        for ssid in ssids:
            ssid_id_dict[ssid["name"]] = ssid

        for ssid_name, ssid_settings in ssid_id_dict.items():
            self.ssid_attrs[ssid_name] = {}
            for setting_id, setting_name in SENSOR_SSID_SETTINGS_DICT.items():
                if setting_id in ssid_settings:
                    value = ssid_settings[setting_id]
                    if setting_id == "band":
                        value = BAND_INFO[value]
                    self.ssid_attrs[ssid_name][setting_name] = value

    async def async_update(self, now: Optional[datetime] = None) -> None:
        """Get the latest data from the Omada Controller."""
        try:
            logged = await self.login()
            if not logged:
                self.available = False
                return
            # Fetch data
            await self.fetch_version()
            await self.fetch_global_stats()
            await self.fetch_ap_stats()
            await self.fetch_clients_list()
            await self.fetch_ssid_attributes()
            await self.fetch_ssid_stats()
            async_dispatcher_send(self._hass, self.signal_sensor_update)
            self.available = True
        except Exception as exp:  # pylint: disable=broad-except
            _LOGGER.error(
                "Unable to fetch data from Omada Controller %s. Error: %s",
                self.host,
                exp,
            )
            self.available = False
            return
        return True

    @property
    def signal_device_new(self) -> str:
        """Event specific per Omada entry to signal new device."""
        return f"{DOMAIN}-{self.host}-device-new"

    @property
    def signal_device_update(self) -> str:
        """Event specific per Omada entry to signal updates in devices."""
        return f"{DOMAIN}-{self.host}-device-update"

    @property
    def signal_sensor_update(self) -> str:
        """Event specific per Omada entry to signal updates in sensors."""
        return f"{DOMAIN}-{self.host}-sensor-update"

    async def _browse_client_page(self, path):
        """Browse client pages.

        It supports:
        * /api/v2/sites/Default/clients
        * /api/v2/sites/Default/insight/clients
        """
        list_of_devices = []
        current_page_size = 10
        current_page = 1
        total_rows = current_page_size + 1
        while (current_page - 1) * current_page_size <= total_rows:
            params = {"currentPage": current_page,
                      "currentPageSize": current_page_size,
                      "filters.active": "true"}
            res_json = await self._http_get_request(path, params)
            if res_json["errorCode"] != 0:
                _LOGGER.error("Error fetching client list: %s", res_json["msg"])
                return
            list_of_devices.extend(res_json["result"]["data"])

            current_page += 1
            total_rows = res_json["result"]["totalRows"]

        return list_of_devices
