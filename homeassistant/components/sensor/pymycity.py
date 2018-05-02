"""
Support for PyMyCity.

Get data from 'My Usage Page' page: https://client.ebox.ca/myusage

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/sensor.ebox/
"""
import logging
from datetime import timedelta

import requests
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_USERNAME, CONF_PASSWORD,
    CONF_NAME, CONF_MONITORED_VARIABLES)
from homeassistant.helpers.entity import Entity

# pylint: disable=import-error
REQUIREMENTS = []  # ['pyebox==0.1.0'] - disabled because it breaks pip10

_LOGGER = logging.getLogger(__name__)

GIGABITS = 'Gb'  # type: str
PRICE = 'CAD'  # type: str
DAYS = 'days'  # type: str
PERCENT = '%'  # type: str

DEFAULT_NAME = 'PyMyCity'

REQUESTS_TIMEOUT = 15
SCAN_INTERVAL = timedelta(minutes=5)



PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required("city"): cv.string,
    vol.Required("command"): cv.string,
    vol.Optional("params"): vol.All(),
    vol.Optional("unit"): cv.string,
    vol.Optional("attributes_name"): cv.string,
})


async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the PyMyCity sensor."""
    city = config.get("city")
    command = config.get("command")
    params = config.get("params")
    unit = config.get("unit")
    attributes_name = config.get("attributes_name")

    httpsession = hass.helpers.aiohttp_client.async_get_clientsession()

    name = config.get(CONF_NAME)
    pymycity_sensor = PyMyCitySensor(name, city, command, params, unit, attributes_name, httpsession)

    sensors = []
    sensors.append(pymycity_sensor)

    async_add_devices(sensors, True)


class PyMyCitySensor(Entity):
    """Implementation of a PyMyCity sensor."""

    def __init__(self, name, city_name, command, params, unit, attributes_name, httpsession):
        """Initialize the sensor."""
        self._state = None
        self._data = None
        # try to create a good name
        if name is None:
            tmp_text = []
            for param in params.values():
                tmp_text.append(str(param))
            name = "{}_{}_{}".format(city_name,
                                     command,
                                     " ".join(tmp_text))
        self._name = name
        from pymycity.cities import get_city_module
        self.city = get_city_module(city_name, None, httpsession)
        self.city_name = city_name
        self.command = command
        self.params = params
        self.attributes_name = attributes_name
        self._unit_of_measurement = unit


    @property
    def name(self):
        """Return the name of the sensor."""
        return "pymycity_{}".format(self._name)

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return self._unit_of_measurement

    @property
    def state_attributes(self):
        attrs = {}
        if self._data is None:
            return attrs
        for index, value in enumerate(self._data):
            if self.attributes_name:
                key = "{}_{}".format(self.attributes_name, index + 1)
            else:
                key = "{}_{}".format(self._name, index + 1)
            # Improve serialization
            attrs[key] = str(value)
        return attrs

    async def update(self):
        """Get the latest data from PyMyCity and update the state."""
        #if self.type in self.ebox_data.data:
        #    self._state = round(self.ebox_data.data[self.type], 2)
        try:
            results = await getattr(self.city, self.command)(**self.params)
        except Exception as exp:
            _LOGGER.error("Error on receive last PyMyCity data: %s", exp)
            return
        self._state = results[0]
        # We want attributes if we have only one result
        if len(results) > 1:
            self._data = results[1:]
