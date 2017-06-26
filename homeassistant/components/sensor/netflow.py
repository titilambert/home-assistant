"""
Support for NetFlow.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/sensor.netflow/
"""
from datetime import timedelta
import logging

import socket, struct
from socket import inet_ntoa

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_USERNAME, CONF_PASSWORD,
    CONF_NAME, CONF_MONITORED_VARIABLES)
from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle
import homeassistant.helpers.config_validation as cv


#https://github.com/a10networks/tps-scripts/blob/master/netFlow_v5_parser.py
#http://blog.devicenull.org/2013/09/04/python-netflow-v5-parser.html

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'Netflow'

REQUESTS_TIMEOUT = 15
MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=5)


SIZE_OF_HEADER = 24
SIZE_OF_RECORD = 48

PROTOCOLS = ["udp", "tcp"]
DEFAULT_PORT = 2055

CONF_MONITORED_TRAFFIC = "monitored_traffic"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_MONITORED_TRAFFIC):
        vol.All(cv.ensure_list, {"name": c.string,
                                 "saddr": c.string,
                                 "daddr": c.string,
                                 "sport": c.port,
                                 "pport": c.port,
                                 "protocol": vol.In(PROTOCOLS),
                                 "packet_count": c.positive_int,
                                 "bytes_count": c.positive_int,
                                 "deltatime": c.positive_int,
                                 }),
    vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
})


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Netflow sensor."""

    try:
        netflow_server = NetFlowServer(port)
    except Exception as error:
        _LOGGER.error("Error stating server: %s", error)
        return False

    name = config.get(CONF_NAME)

    sensors = []
    for variable in config[CONF_MONITORED_VARIABLES]:
        sensors.append(EBoxSensor(ebox_data, variable, name))

    add_devices(sensors, True)



class NetFlowServer(object):

    def __init__(self, port):
        self.port = port

    def listen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('0.0.0.0', self.port))

        while True:
            buf, addr = sock.recvfrom(1500)

            (version, count) = struct.unpack('!HH',buf[0:4])
            if version != 5:
                print("Not NetFlow v5!")
                continue

            # It's pretty unlikely you'll ever see more then 1000 records in a 1500 byte UDP packet
            if count <= 0 or count >= 1000:
                print("Invalid count %s" % count)
                continue

            uptime = socket.ntohl(struct.unpack('I',buf[4:8])[0])
            epochseconds = socket.ntohl(struct.unpack('I',buf[8:12])[0])

            for i in range(0, count):
                try:
                    base = SIZE_OF_HEADER+(i*SIZE_OF_RECORD)

                    data = struct.unpack('!IIIIHH',buf[base+16:base+36])

                    nfdata = {}
                    nfdata['saddr'] = inet_ntoa(buf[base+0:base+4])
                    nfdata['daddr'] = inet_ntoa(buf[base+4:base+8])
                    nfdata['pcount'] = data[0]
                    nfdata['bcount'] = data[1]
                    nfdata['stime'] = data[2]
                    nfdata['etime'] = data[3]
                    nfdata['sport'] = data[4]
                    nfdata['dport'] = data[5]
                    nfdata['protocol'] = ord(buf[base+38])
                except:
                    print("DDDD")
                    continue

            # Do something with the netflow record..
        #    if nfdata['saddr'] == "192.168.2.124" or nfdata['daddr'] == "192.168.2.124":
            print("%s:%s -> %s:%s # %s - %s" % (nfdata['saddr'],nfdata['sport'],nfdata['daddr'],nfdata['dport'], nfdata['pcount'], nfdata['bcount']))
