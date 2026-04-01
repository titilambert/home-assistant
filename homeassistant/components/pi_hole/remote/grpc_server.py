"""IntegrationGrpcServicer — exposes Pi-hole lifecycle & service calls over gRPC."""

from __future__ import annotations

import asyncio
import logging

from hole import Hole
from hole.exceptions import HoleError

from homeassistant.components.pi_hole.remote import integration_pb2, integration_pb2_grpc
from homeassistant.components.pi_hole.remote.hass_proxy import HassProxy

_LOGGER = logging.getLogger(__name__)

# Sensor keys to expose as HA states (Pi-hole v6 API layout)
_SENSOR_KEYS: tuple[tuple[str, str, str], ...] = (
    # (entity_id_suffix, top-level key, nested key)
    ("ads_blocked_today", "queries", "blocked"),
    ("ads_percentage_today", "queries", "percent_blocked"),
    ("dns_queries_today", "queries", "total"),
    ("unique_domains", "queries", "unique_domains"),
    ("domains_being_blocked", "gravity", "domains_being_blocked"),
    ("queries_cached", "queries", "cached"),
    ("queries_forwarded", "queries", "forwarded"),
    ("unique_clients", "clients", "active"),
)


def _build_entity_states(
    entry_id: str, api: Hole, blocking: bool
) -> dict[str, dict]:
    """Build entity_id → {state, attributes} from the hole API data."""
    data = api.data or {}
    states: dict[str, dict] = {}

    for suffix, top, nested in _SENSOR_KEYS:
        value = data.get(top, {}).get(nested, 0)
        states[f"sensor.pi_hole_{entry_id}_{suffix}"] = {
            "state": str(value),
            "attributes": {"source": "remote"},
        }

    states[f"switch.pi_hole_{entry_id}"] = {
        "state": "on" if blocking else "off",
        "attributes": {"source": "remote"},
    }
    return states


class IntegrationGrpcServicer(integration_pb2_grpc.IntegrationServiceServicer):
    """Implements the IntegrationService gRPC server for Pi-hole."""

    def __init__(self, core_address: str) -> None:
        self._core_address = core_address
        self._hass: HassProxy | None = None
        self._api: Hole | None = None
        self._integration_id: str = ""
        self._update_task: asyncio.Task | None = None

    async def Initialize(self, request, context):
        config = dict(request.config)
        self._integration_id = request.integration_id
        _LOGGER.info("Initialize: %s  config=%s", self._integration_id, config)

        import aiohttp
        import ssl

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        session = aiohttp.ClientSession(connector=connector)

        self._api = Hole(
            host=config["host"],
            session=session,
            location=config.get("location", "admin"),
            tls=config.get("ssl", "false").lower() == "true",
            verify_tls=False,
            version=6,
            protocol=config.get("protocol", "https"),
            password=config.get("password", ""),
        )

        try:
            await self._api.authenticate()
            await self._api.get_data()
        except HoleError as err:
            _LOGGER.error("Pi-hole initialization failed: %s", err)
            return integration_pb2.InitResponse(success=False, error=str(err))

        self._hass = HassProxy(self._core_address)

        entity_ids = list(
            _build_entity_states(self._integration_id, self._api, blocking=True).keys()
        )
        _LOGGER.info("Initialized OK, entities: %s", entity_ids)
        return integration_pb2.InitResponse(success=True, entity_ids=entity_ids)

    async def Start(self, request, context):
        _LOGGER.info("Start: %s", request.integration_id)
        self._update_task = asyncio.create_task(self._update_loop())
        return integration_pb2.StartResponse(success=True)

    async def Stop(self, request, context):
        _LOGGER.info("Stop: %s", request.integration_id)
        if self._update_task:
            self._update_task.cancel()
        if self._hass:
            await self._hass.close()
        return integration_pb2.StopResponse(success=True)

    async def CallService(self, request, context):
        _LOGGER.info(
            "CallService: %s on %s", request.service, request.entity_id
        )
        if self._api is None:
            return integration_pb2.CallServiceResponse(
                success=False, error="Not initialized"
            )
        try:
            if request.service == "turn_on":
                await self._api.enable()
            elif request.service == "turn_off":
                await self._api.disable(True)
            else:
                return integration_pb2.CallServiceResponse(
                    success=False, error=f"Unknown service: {request.service}"
                )
            await self._push_states()
        except HoleError as err:
            return integration_pb2.CallServiceResponse(success=False, error=str(err))
        return integration_pb2.CallServiceResponse(success=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _update_loop(self) -> None:
        _LOGGER.info("Update loop started (30s interval)")
        while True:
            try:
                await self._push_states()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                _LOGGER.info("Update loop cancelled")
                break
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Update loop error: %s", err)
                await asyncio.sleep(30)

    async def _push_states(self) -> None:
        assert self._api is not None
        assert self._hass is not None

        await self._api.get_data()

        blocking_data = self._api.data.get("blocking", {})
        is_blocking = blocking_data if isinstance(blocking_data, bool) else (
            self._api.status == "enabled"  # type: ignore[union-attr]
        )

        states = _build_entity_states(self._integration_id, self._api, is_blocking)
        for entity_id, payload in states.items():
            await self._hass.states.async_set(
                entity_id, payload["state"], payload["attributes"]
            )
        _LOGGER.debug("Pushed %d states to Core", len(states))
