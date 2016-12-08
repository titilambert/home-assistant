"""
Support for interface with an Aquos TV.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.aquostv/
"""
import logging

import voluptuous as vol

from homeassistant.components.media_player import (
    SUPPORT_TURN_ON, SUPPORT_TURN_OFF,
    SUPPORT_VOLUME_MUTE, SUPPORT_VOLUME_STEP,
    SUPPORT_VOLUME_SET, MediaPlayerDevice, PLATFORM_SCHEMA)

from homeassistant.const import (
    CONF_HOST, CONF_NAME, STATE_OFF, STATE_ON, STATE_UNKNOWN,
    CONF_PORT, CONF_USERNAME, CONF_PASSWORD)

import homeassistant.helpers.config_validation as cv

REQUIREMENTS = ['sharp-aquos-rc==0.2']

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'Sharp Aquos TV'
DEFAULT_PORT = 10002
DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = 'password'

SUPPORT_SHARPTV = SUPPORT_VOLUME_STEP | \
    SUPPORT_VOLUME_SET | SUPPORT_VOLUME_MUTE | \
    SUPPORT_TURN_OFF

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
    vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
    vol.Optional('power_on_enabled', default=False): cv.boolean,
})


# pylint: disable=unused-argument
def setup_platform(hass, config, add_devices, discovery_info=None):
    """Setup the Sharp Aquos TV platform."""
    import sharp_aquos_rc

    name = config.get(CONF_NAME)
    port = config.get(CONF_PORT)
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    power_on_enabled = config.get('power_on_enabled')

    if discovery_info:
        _LOGGER.debug('%s', discovery_info)
        vals = discovery_info.split(':')
        if len(vals) > 1:
            port = vals[1]

        host = vals[0]
        remote = sharp_aquos_rc.TV(host,
                                   port,
                                   username,
                                   password)
        add_devices([SharpAquosTVDevice(name, remote, power_on_enabled)])
        return True

    host = config.get(CONF_HOST)
    remote = sharp_aquos_rc.TV(host,
                               port,
                               username,
                               password)

    add_devices([SharpAquosTVDevice(name, remote, power_on_enabled)])
    return True


# pylint: disable=abstract-method
class SharpAquosTVDevice(MediaPlayerDevice):
    """Representation of a Aquos TV."""

    # pylint: disable=too-many-public-methods
    def __init__(self, name, remote, power_on_enabled=False):
        """Initialize the aquos device."""
        global SUPPORT_SHARPTV
        self._power_on_enabled = power_on_enabled
        if self._power_on_enabled:
            SUPPORT_SHARPTV = SUPPORT_SHARPTV | SUPPORT_TURN_ON
        # Save a reference to the imported class
        self._name = name
        # Assume that the TV is not muted
        self._muted = False
        self._state = STATE_UNKNOWN
        self._remote = remote
        self._volume = 0

    def update(self):
        """Retrieve the latest data."""
        # According to the documentation:
        # http://files.sharpusa.com/Downloads/ForHome/
        # HomeEntertainment/LCDTVs/Manuals/tel_man_LC40_46_52_60LE830U.pdf
        # Page 58 - Communication conditions for IP
        # The connection could be lost (but not only after 3 minutes),
        # so we need to the remote commands to be sure about states
        retry = 3
        while retry > 0:
            try:
                if self._remote.power() == 1:
                    self._state = STATE_ON
                else:
                    self._state = STATE_OFF
                # Set TV to be able to remotely power on
                if self._power_on_enabled:
                    self._remote.power_on_command_settings(2)
                else:
                    self._remote.power_on_command_settings(0)
                # Get mute state
                if self._remote.mute() == 2:
                    self._muted = False
                else:
                    self._muted = True
                # Get volume
                self._volume = self._remote.volume() / 60
                break
            except OSError:
                retry -= 1
                if retry == 0:
                    self._state = STATE_OFF

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the state of the device."""
        return self._state

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._muted

    @property
    def supported_media_commands(self):
        """Flag of media commands that are supported."""
        return SUPPORT_SHARPTV

    def turn_off(self):
        """Turn off tvplayer."""
        self._remote.power(0)

    def volume_up(self):
        """Volume up the media player."""
        self._remote.volume(int(self._volume * 60) + 2)

    def volume_down(self):
        """Volume down media player."""
        self._remote.volume(int(self._volume * 60) - 2)

    def set_volume_level(self, level):
        """Set Volume media player."""
        self._remote.volume(int(level * 60))

    def mute_volume(self, mute):
        """Send mute command."""
        self._remote.mute(0)

    def turn_on(self):
        """Turn the media player on."""
        # The connection could be lost (but not only after 3 minutes),
        # so we need to retry the remote command to be sure about states
        retry = 3
        while retry > 0:
            try:
                self._remote.power(1)
                break
            except OSError as exp:
                retry -= 1
                if retry == 0:
                    raise exp
