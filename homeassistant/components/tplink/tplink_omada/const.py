"""Constants for the TP-Link Omada integration."""

DOMAIN = "tplink_omada"
PLATFORMS = ["sensor"]

CONF_DNSRESOLVE = "dns_resolve"
CONF_SSLVERIFY = "ssl_verify"
DEFAULT_TIMEOUT = 10
DEFAULT_SSLVERIFY = True
DEFAULT_DNSRESOLVE = False

API_LOGIN_PATH = "/api/v2/login"
API_ABOUT_PATH = "/api/v2/maintenance/controllerStatus"
API_FREQ_DISTRIBUTION_PATH = "/api/v2/sites/Default/dashboard/clientsFreqDistribution"
API_DEVICES_PATH = "/api/v2/sites/Default/devices"
API_CLIENTS_PATH = "/api/v2/sites/Default/clients"
API_INSIGHT_CLIENTS_PATH = "/api/v2/sites/Default/insight/clients"
API_SETTINGS_WLANS = "/api/v2/sites/Default/setting/wlans"
API_SETTINGS_SSIDS = "/api/v2/sites/Default/setting/wlans/{}/ssids"

SENSOR_DICT = {
    "totalClients": ["Connected clients", "clients", "mdi:account-group"],
}
SENSOR_LIST = list(SENSOR_DICT)


SENSOR_SSID_SETTINGS_DICT = {
    "band": "bands",
    "guestNetEnable": "guest_wlan",
    "vlanId": "vlan_id",
    "accessEnable": "acl_enabled",
    "name": "name",
}

SENSOR_AP_SETTINGS_DICT = {
    "model": "Model",
    "modelVersion": "Model Version",
    "version": "Version",
    "ip": "IP",
    "mac": "Mac Address",
    "name": "Name",
}
SENSOR_AP_STATS_DICT = {
    "clientNum": ["Connected clients", "clients", "mdi:account-group"],
    "clientNum2g": ["Connected 2G clients", "clients", "mdi:account-group"],
    "clientNum5g": ["Connected 5G clients", "clients", "mdi:account-group"],
    "needUpgrade": ["Need update", "", "mdi:update"],
    "download": ["Traffic received", "bits", "mdi:download"],
    "upload": ["Traffic sent", "bits", "mdi:upload"],
}

BAND_INFO = {
    1: "2.4GHz",
    2: "5GHz",
    3: "2.4GHz, 5GHz",
}
