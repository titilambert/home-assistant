"""HassProxy — shared proxy used by all remote integrations to talk to the Core gRPC server."""

from __future__ import annotations

from dataclasses import dataclass

import grpc.aio

from . import core_pb2, core_pb2_grpc


@dataclass
class HAConfig:
    """Subset of HA core config relevant to remote integrations."""

    latitude: float
    longitude: float
    elevation: float
    time_zone: str
    location_name: str


@dataclass
class IntegrationConfig:
    """Config entry data returned by the Core to the remote integration."""

    entry_id: str
    data: dict[str, str]


class StatesProxy:
    """Proxy for hass.states — routes async_set/get through gRPC."""

    def __init__(self, stub: core_pb2_grpc.CoreServiceStub) -> None:
        self._stub = stub

    async def async_set(
        self, entity_id: str, state: str, attributes: dict | None = None
    ) -> None:
        str_attrs = {k: str(v) for k, v in (attributes or {}).items()}
        await self._stub.SetState(
            core_pb2.SetStateRequest(
                entity_id=entity_id,
                state=state,
                attributes=str_attrs,
            )
        )

    async def async_get(self, entity_id: str):
        response = await self._stub.GetState(
            core_pb2.GetStateRequest(entity_id=entity_id)
        )
        return response if response.found else None


class HassProxy:
    """Minimal proxy for the hass object, shared by all remote integration processes."""

    def __init__(self, core_address: str = "localhost:50051") -> None:
        self._channel = grpc.aio.insecure_channel(core_address)
        self._stub = core_pb2_grpc.CoreServiceStub(self._channel)
        self.states = StatesProxy(self._stub)

    async def get_config(self) -> HAConfig:
        """Fetch HA core configuration from the Core gRPC server."""
        resp = await self._stub.GetConfig(core_pb2.GetConfigRequest())
        return HAConfig(
            latitude=resp.latitude,
            longitude=resp.longitude,
            elevation=resp.elevation,
            time_zone=resp.time_zone,
            location_name=resp.location_name,
        )

    async def get_integration_config(self, domain: str) -> IntegrationConfig:
        """Fetch integration config entry data (host, password, etc.) from Core."""
        resp = await self._stub.GetIntegrationConfig(
            core_pb2.GetIntegrationConfigRequest(domain=domain)
        )
        if not resp.found:
            raise RuntimeError(f"No config entry found for domain '{domain}' in HA")
        return IntegrationConfig(entry_id=resp.entry_id, data=dict(resp.data))

    async def register_integration(
        self,
        integration_id: str,
        domain: str,
        grpc_address: str,
        entity_ids: list[str],
        switch_entity_ids: list[str] | None = None,
    ) -> None:
        """Register this remote process with the Core so it can route service calls back."""
        await self._stub.RegisterIntegration(
            core_pb2.RegisterIntegrationRequest(
                integration_id=integration_id,
                domain=domain,
                grpc_address=grpc_address,
                entity_ids=entity_ids,
                switch_entity_ids=switch_entity_ids or [],
            )
        )

    async def close(self) -> None:
        await self._channel.close()
