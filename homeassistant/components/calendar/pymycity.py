"""
Support for WebDav Calendar.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/calendar.caldav/
"""
from datetime import datetime, timedelta
import logging

import voluptuous as vol

from homeassistant.components.calendar import (
    PLATFORM_SCHEMA, AsyncCalendarEventDevice, EventData, DOMAIN)
from homeassistant.const import CONF_NAME
import homeassistant.helpers.config_validation as cv
from homeassistant.util import Throttle, dt

REQUIREMENTS = ['pymycity==0.2.6']


SUBDOMAIN = 'pymycity'

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE_ID = 'device_id'
CONF_CALENDARS = 'calendars'
CONF_CUSTOM_CALENDARS = 'custom_calendars'
CONF_CALENDAR = 'calendar'
CONF_SEARCH = 'search'

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=15)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_NAME): cv.string,
    vol.Required("city"): cv.string,
    vol.Required("command"): cv.string,
    vol.Optional("color"): cv.string,
    vol.Optional("params"): vol.All(),
    vol.Optional("reminder"): cv.string,
})
PERSISTENCE = '.pymycity.calendar.json'


async def async_setup_platform(hass, config, async_add_devices, disc_info=None):
    """Set up the WebDav Calendar platform."""
    device_data = {
        CONF_NAME:  config.get(CONF_NAME),
        CONF_DEVICE_ID: config.get(CONF_NAME),
        'city': config.get("city"),
        'command': config.get("command"),
        'color': config.get("color"),
        'params': config.get("params") if config.get("params") else {},
        'reminder': config.get("reminder"),
    }
    persistence_file = ".{}{}".format(config.get("city"), PERSISTENCE)
    hass.data[DOMAIN][config.get(CONF_NAME)] = EventData(hass, persistence_file)

    from pymycity.cities import get_city_module
    httpsession = hass.helpers.aiohttp_client.async_get_clientsession()
    calendar = get_city_module(config.get("city"), None, httpsession)

    pymycity_sensor = PyMyCityEventDevice(hass, device_data, calendar)

    sensors = []
    sensors.append(pymycity_sensor)

    async_add_devices(sensors, True)

    return


class PyMyCityEventDevice(AsyncCalendarEventDevice):
    """A device for getting the next Task from a WebDav Calendar."""

    def __init__(self, hass, device_data, calendar):
        """Create the WebDav Calendar Event Device."""
        httpsession = hass.helpers.aiohttp_client.async_get_clientsession()
        self.data = PyMyCityData(device_data['name'],
                                 device_data['city'],
                                 device_data['command'],
                                 device_data['params'],
                                 device_data['color'],
                                 hass,
                                 httpsession)
        self.reminder = device_data['reminder']
        super().__init__(hass, device_data)

    @property
    def device_state_attributes(self):
        """Return the device state attributes."""
        if self.data.event is None:
            # No tasks, we don't REALLY need to show anything.
            return {}

        attributes = super().device_state_attributes
        return attributes

    async def async_update(self):
        """Search for the next event."""
        ret = await self.data.async_update()
        if not self.data or not ret:
            # update cached, don't do anything
            return

        if not self.data.event:
            # we have no event to work on, make sure we're clean
            self.cleanup()
            return

        def _get_date(date):
            """Get the dateTime from date or dateTime as a local."""
            if 'date' in date:
                return dt.start_of_local_day(dt.dt.datetime.combine(
                    dt.parse_date(date['date']), dt.dt.time.min))
            return dt.as_local(dt.parse_datetime(date['dateTime']))

        start = _get_date(self.data.event['start'])
        end = _get_date(self.data.event['end'])

        summary = self.data.event.get('summary', '')

        # Get offset_time
        offset_time = dt.dt.timedelta()
        if self.reminder:
            offset_time = dt.parse_duration(self.reminder)
            if offset_time is None:
                _LOGGER.error("Bad reminder format")
                offset_time = dt.dt.timedelta()
            else:
                offset_time = -offset_time

        # cleanup the string so we don't have a bunch of double+ spaces
        self._cal_data['message'] = summary

        self._cal_data['offset_time'] = offset_time
        self._cal_data['location'] = self.data.event.get('location', '')
        self._cal_data['description'] = self.data.event.get('description', '')
        self._cal_data['url'] = self.data.event.get('url', '')
        self._cal_data['start'] = start
        self._cal_data['end'] = end
        self._cal_data['all_day'] = 'date' in self.data.event['start']


class PyMyCityData(object):
    """Implementation of a PyMyCity sensor."""

    def __init__(self, name, city_name, command, params, color, hass, httpsession):
        """Initialize the sensor."""
        self.hass = hass
        self._state = None
        self._all_data = None
        self.event = None
        self.color = color
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

        if name is None:
            tmp_text = []

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def async_update(self):
        """Get the latest data."""
        try:
            results = await getattr(self.city, self.command)(**self.params)
        # Improve this
        except Exception as exp:  # pylint: disable=W0703
            _LOGGER.error("Error on receive last PyMyCity data: %s", exp)
            return
        # If no matching event could be found
        if not results:
            _LOGGER.error(
                "No matching event found in the %d results for %s",
                len(results), self._name)
            self.event = None
            return True

        for event in results:
            self.hass.data[DOMAIN][self._name].async_add(
                title=event.title,
                start=self.get_hass_date(event.start),
                end=self.get_hass_date(self.get_end_date(event)),
                location=event.location,
                color=self.color,
                description=event.description,
                url=event.url,)

        event = results[0]

        # Populate the entity attributes with the event values
        self.event = {
            #"summary": event.title,
            "title": event.title,
            "start": self.get_hass_date(event.start),
            "end": self.get_hass_date(self.get_end_date(event)),
            "location": event.location,
            "description": event.description,
            "url": event.url,
        }
        return True

    @staticmethod
    def get_hass_date(obj):
        """Return if the event matches."""
        if isinstance(obj, datetime):
            return {"dateTime": obj.isoformat()}

        return {"date": obj.isoformat()}

    @staticmethod
    def get_end_date(obj):
        """Return the end datetime as determined by dtend or duration."""
        if hasattr(obj, "end") and obj.end is not None:
            enddate = obj.end

        elif hasattr(obj, "duration"):
            enddate = obj.start + obj.duration

        else:
            enddate = obj.start + timedelta(days=1)

        return enddate
