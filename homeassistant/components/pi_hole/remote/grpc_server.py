"""IntegrationGrpcServicer for the Pi-hole integration (REMOTE mode)."""

from __future__ import annotations

import asyncio
import logging

from hole import Hole
from hole.exceptions import HoleError

from homeassistant.grpc import integration_pb2, integration_pb2_grpc
from homeassistant.grpc.hass_proxy import HassProxy

_LOGGER = logging.getLogger(__name__)

_SENSOR_KEYS: tuple[tuple[str, str, str], ...] = (
    ("ads_blocked_today", "queries", "blocked"),
    ("ads_percentage_today", "queries", "percent_blocked"),
    ("dns_queries_today", "queries", "total"),
    ("unique_domains", "queries", "unique_domains"),
    ("domains_being_blocked", "gravity", "domains_being_blocked"),
    ("queries_cached", "queries", "cached"),
    ("queries_forwarded", "queries", "forwarded"),
    ("unique_clients", "clients", "active"),
)


def _build_entity_states(entry_id: str, api: Hole, blocking: bool) -> dict[str, dict]:
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
    """Pi-hole remote integration gRPC server."""

    def __init__(self, core_address: str, own_grpc_address: str) -> None:
        self._core_address = core_address
        self._own_grpc_address = own_grpc_address
        self._hass: HassProxy | None = None
        self._api: Hole | None = None
        self._entry_id: str = ""
        self._update_task: asyncio.Task | None = None

    async def Initialize(self, request, context):
        _LOGGER.info("Initialize: %s", request.integration_id)

        # TODO FOR CLAUDE: why we don't reuse the code from the local mode ? in __init__.py
        self._hass = HassProxy(self._core_address)

        # Fetch config from Core — no local config needed
        try:
            cfg = await self._hass.get_integration_config("pi_hole")
        except RuntimeError as err:
            return integration_pb2.InitResponse(success=False, error=str(err))

        self._entry_id = cfg.entry_id
        data = cfg.data

        _LOGGER.info(
            "Got config from Core: host=%s entry_id=%s",
            data.get("host"),
            self._entry_id,
        )

        import ssl

        import aiohttp

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        session = aiohttp.ClientSession(connector=connector)

        use_ssl = data.get("ssl", "False").lower() == "true"
        self._api = Hole(
            host=data["host"],
            session=session,
            location=data.get("location", "admin"),
            verify_tls=False,
            version=6,
            protocol="https" if use_ssl else "http",
            password=data.get("api_key", ""),
        )

        try:
            await self._api.authenticate()
            await self._api.get_data()
        except HoleError as err:
            _LOGGER.error("Pi-hole init failed: %s", err)
            return integration_pb2.InitResponse(success=False, error=str(err))

        entity_ids = list(_build_entity_states(self._entry_id, self._api, True).keys())
        switch_entity_ids = [f"switch.pi_hole_{self._entry_id}"]

        # Register with Core so it can route service calls back to us
        await self._hass.register_integration(
            integration_id=self._entry_id,
            domain="pi_hole",
            grpc_address=self._own_grpc_address,
            entity_ids=entity_ids,
            switch_entity_ids=switch_entity_ids,
        )
        _LOGGER.info("Registered with Core. Entities: %s", entity_ids)

        return integration_pb2.InitResponse(success=True, entity_ids=entity_ids)

    async def Start(self, request, context):
        self._update_task = asyncio.create_task(self._update_loop())
        return integration_pb2.StartResponse(success=True)

    async def Stop(self, request, context):
        if self._update_task:
            self._update_task.cancel()
        if self._hass:
            await self._hass.close()
        return integration_pb2.StopResponse(success=True)

    async def CallService(self, request, context):
        _LOGGER.info("CallService: %s on %s", request.service, request.entity_id)
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

    async def _update_loop(self) -> None:
        # TODO FOR CLAUDE: why we don't reuse the code from the local mode ? in coordinator.py
        _LOGGER.info("Update loop started (30s interval)")
        while True:
            try:
                await self._push_states()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Update loop error: %s", err)
                await asyncio.sleep(30)

    async def _push_states(self) -> None:
        assert self._api is not None and self._hass is not None
        await self._api.get_data()
        blocking_raw = self._api.data.get("blocking", {})
        is_blocking = (
            blocking_raw
            if isinstance(blocking_raw, bool)
            else self._api.status == "enabled"
        )
        for entity_id, payload in _build_entity_states(
            self._entry_id, self._api, is_blocking
        ).items():
            await self._hass.states.async_set(
                entity_id, payload["state"], payload["attributes"]
            )
        _LOGGER.debug("Pushed states to Core (blocking=%s)", is_blocking)
