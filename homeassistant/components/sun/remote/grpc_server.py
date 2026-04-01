"""IntegrationGrpcServicer for the Sun integration.

The remote Sun process fetches HA location config via gRPC, then uses astral
to compute solar events and position locally, pushing states to Core on a
schedule that mirrors the existing local Sun entity update logic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

from astral import LocationInfo
from astral.location import Location
from astral.sun import azimuth, elevation, sun

from homeassistant.components.sun.remote import integration_pb2, integration_pb2_grpc
from homeassistant.grpc.hass_proxy import HassProxy
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Mirrors entity.py phase update intervals
_PHASE_UPDATES = {
    "night": timedelta(minutes=20),
    "astronomical_twilight": timedelta(minutes=8),
    "nautical_twilight": timedelta(minutes=4),
    "twilight": timedelta(minutes=2),
    "small_day": timedelta(minutes=2),
    "day": timedelta(minutes=4),
}

STATE_ABOVE_HORIZON = "above_horizon"
STATE_BELOW_HORIZON = "below_horizon"


def _get_phase(elev: float) -> str:
    if elev >= 10:
        return "day"
    if elev >= 0:
        return "small_day"
    if elev >= -6:
        return "twilight"
    if elev >= -12:
        return "nautical_twilight"
    if elev >= -18:
        return "astronomical_twilight"
    return "night"


class SunGrpcServicer(integration_pb2_grpc.IntegrationServiceServicer):
    """Computes solar position with astral and pushes states to Core via gRPC."""

    def __init__(self, core_address: str) -> None:
        self._core_address = core_address
        self._hass: HassProxy | None = None
        self._location: Location | None = None
        self._update_task: asyncio.Task | None = None

    async def Initialize(self, request, context):
        _LOGGER.info("Sun Initialize: %s", request.integration_id)
        self._hass = HassProxy(self._core_address)

        try:
            config = await self._hass.get_config()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to fetch HA config: %s", err)
            return integration_pb2.InitResponse(success=False, error=str(err))

        _LOGGER.info(
            "Got HA config: %s (lat=%.4f, lon=%.4f, tz=%s)",
            config.location_name,
            config.latitude,
            config.longitude,
            config.time_zone,
        )
        info = LocationInfo(
            name=config.location_name,
            region="",
            timezone=config.time_zone,
            latitude=config.latitude,
            longitude=config.longitude,
        )
        self._location = Location(info)

        entity_ids = [
            "sun.sun",
            "sensor.sun_next_dawn",
            "sensor.sun_next_dusk",
            "sensor.sun_next_midnight",
            "sensor.sun_next_noon",
            "sensor.sun_next_rising",
            "sensor.sun_next_setting",
            "sensor.sun_solar_elevation",
            "sensor.sun_solar_azimuth",
        ]
        return integration_pb2.InitResponse(success=True, entity_ids=entity_ids)

    async def Start(self, request, context):
        _LOGGER.info("Sun Start")
        self._update_task = asyncio.create_task(self._update_loop())
        return integration_pb2.StartResponse(success=True)

    async def Stop(self, request, context):
        _LOGGER.info("Sun Stop")
        if self._update_task:
            self._update_task.cancel()
        if self._hass:
            await self._hass.close()
        return integration_pb2.StopResponse(success=True)

    async def CallService(self, request, context):
        # Sun has no controllable services.
        return integration_pb2.CallServiceResponse(
            success=False, error="Sun integration has no callable services"
        )

    # ------------------------------------------------------------------

    async def _update_loop(self) -> None:
        _LOGGER.info("Sun update loop started")
        while True:
            try:
                delay = await self._push_states()
                await asyncio.sleep(delay.total_seconds())
            except asyncio.CancelledError:
                _LOGGER.info("Sun update loop cancelled")
                break
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Sun update loop error: %s", err)
                await asyncio.sleep(60)

    async def _push_states(self) -> timedelta:
        """Compute and push all sun states. Returns how long to sleep until next update."""
        assert self._location is not None
        assert self._hass is not None

        now: datetime = dt_util.utcnow()

        # Solar position
        elev = round(self._location.solar_elevation(now), 2)
        azim = round(self._location.solar_azimuth(now), 2)
        state = STATE_ABOVE_HORIZON if elev > -0.833 else STATE_BELOW_HORIZON
        phase = _get_phase(elev)

        # Next solar events (astral returns local-aware datetimes)
        s = sun(self._location.observer, date=now.date(), tzinfo=self._location.timezone)
        # Also compute next day in case today's events are already past
        s_next = sun(
            self._location.observer,
            date=(now + timedelta(days=1)).date(),
            tzinfo=self._location.timezone,
        )

        def _pick(key: str) -> datetime:
            """Return the next future occurrence of a solar event."""
            t = s[key].astimezone(dt_util.UTC)
            if t <= now:
                t = s_next[key].astimezone(dt_util.UTC)
            return t

        next_dawn = _pick("dawn")
        next_dusk = _pick("dusk")
        next_noon = _pick("noon")
        next_midnight = (now + timedelta(hours=12)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )  # approximate midnight
        next_rising = _pick("sunrise")
        next_setting = _pick("sunset")
        rising = next_noon < next_midnight

        # Push main sun.sun entity
        await self._hass.states.async_set(
            "sun.sun",
            state,
            {
                "azimuth": azim,
                "elevation": elev,
                "rising": rising,
                "next_dawn": next_dawn.isoformat(),
                "next_dusk": next_dusk.isoformat(),
                "next_midnight": next_midnight.isoformat(),
                "next_noon": next_noon.isoformat(),
                "next_rising": next_rising.isoformat(),
                "next_setting": next_setting.isoformat(),
                "source": "remote",
            },
        )

        # Push sensor entities
        sensors: dict[str, str] = {
            "sensor.sun_next_dawn": next_dawn.isoformat(),
            "sensor.sun_next_dusk": next_dusk.isoformat(),
            "sensor.sun_next_midnight": next_midnight.isoformat(),
            "sensor.sun_next_noon": next_noon.isoformat(),
            "sensor.sun_next_rising": next_rising.isoformat(),
            "sensor.sun_next_setting": next_setting.isoformat(),
            "sensor.sun_solar_elevation": str(elev),
            "sensor.sun_solar_azimuth": str(azim),
        }
        for entity_id, value in sensors.items():
            await self._hass.states.async_set(entity_id, value, {"source": "remote"})

        _LOGGER.debug("Sun states pushed (phase=%s, elev=%.2f°)", phase, elev)
        return _PHASE_UPDATES[phase]
