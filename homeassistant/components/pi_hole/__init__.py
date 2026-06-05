"""The pi_hole component."""

from __future__ import annotations

import logging
from typing import Any, Literal

from hole import Hole, HoleV5, HoleV6
from hole.exceptions import HoleConnectionError, HoleError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_LOCATION,
    CONF_SSL,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_RUNTIME_MODE,
    CONF_STATISTICS_ONLY,
    CONF_WORKER_NAME,
    DOMAIN,
    RUNTIME_MODE_REMOTE,
)
from .coordinator import PiHoleConfigEntry, PiHoleData, PiHoleUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: PiHoleConfigEntry) -> bool:
    """Set up Pi-hole entry."""
    # Horizontal scaling: check if this entry should run in a remote worker
    from homeassistant.helpers.runtime_factory import (
        async_setup_remote,
        is_remote,
        set_runtime_mode,
    )

    # Sync runtime mode from entry.data to hass.data
    runtime_mode = entry.data.get(CONF_RUNTIME_MODE, "local")
    set_runtime_mode(hass, entry.entry_id, runtime_mode)

    if is_remote(hass, entry):
        from homeassistant.components.horizontal_scaling.const import (
            DATA_WORKER_REGISTRY,
        )

        worker_name = entry.data.get(CONF_WORKER_NAME)
        registry = hass.data.get(DATA_WORKER_REGISTRY)

        if registry and worker_name:
            worker = registry.get_worker(worker_name)
            if worker and worker.status == "running":
                return await async_setup_remote(
                    hass,
                    entry,
                    core_address="localhost:50051",
                    worker_address=worker.address,
                    worker=worker,
                )
            _LOGGER.error(
                "Worker '%s' is not available for entry %s",
                worker_name,
                entry.entry_id,
            )
            return False

        # Fallback: no worker registry or no worker name → old subprocess behaviour
        return await async_setup_remote(hass, entry)

    host = entry.data[CONF_HOST]

    # remove obsolet CONF_STATISTICS_ONLY from entry.data
    if CONF_STATISTICS_ONLY in entry.data:
        entry_data = entry.data.copy()
        entry_data.pop(CONF_STATISTICS_ONLY)
        hass.config_entries.async_update_entry(entry, data=entry_data)

    _LOGGER.debug("Setting up %s integration with host %s", DOMAIN, host)

    name_to_key = {
        "Core Update Available": "core_update_available",
        "Web Update Available": "web_update_available",
        "FTL Update Available": "ftl_update_available",
        "Status": "status",
        "Ads Blocked Today": "ads_blocked_today",
        "Ads Percentage Blocked Today": "ads_percentage_today",
        "Seen Clients": "clients_ever_seen",
        "DNS Queries Today": "dns_queries_today",
        "Domains Blocked": "domains_being_blocked",
        "DNS Queries Cached": "queries_cached",
        "DNS Queries Forwarded": "queries_forwarded",
        "DNS Unique Clients": "unique_clients",
        "DNS Unique Domains": "unique_domains",
    }

    @callback
    def update_unique_id(
        entity_entry: er.RegistryEntry,
    ) -> dict[str, str] | None:
        """Update unique ID of entity entry."""
        unique_id_parts = entity_entry.unique_id.split("/")
        if len(unique_id_parts) == 2 and unique_id_parts[1] in name_to_key:
            name = unique_id_parts[1]
            new_unique_id = entity_entry.unique_id.replace(name, name_to_key[name])
            _LOGGER.debug("Migrate %s to %s", entity_entry.unique_id, new_unique_id)
            return {"new_unique_id": new_unique_id}

        return None

    await er.async_migrate_entries(hass, entry.entry_id, update_unique_id)

    # Use cached API version from config flow if available to avoid a second
    # authentication request that could trigger Pi-hole's rate-limiter.
    if "api_version" in entry.data:
        version = entry.data["api_version"]
        _LOGGER.debug("Using cached Pi-hole API version %s for %s", version, host)
    else:
        _LOGGER.debug("Determining Pi-hole API version for %s", host)
        version = await determine_api_version(hass, dict(entry.data))
        _LOGGER.debug("Pi-hole API version determined: %s", version)

    # Once API version 5 is deprecated we should instantiate Hole directly
    api = api_by_version(hass, dict(entry.data), version)

    coordinator = PiHoleUpdateCoordinator(hass, api, entry)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = PiHoleData(api, coordinator, version)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Pi-hole entry."""
    from homeassistant.helpers.runtime_factory import async_teardown_remote, is_remote

    if is_remote(hass, entry):
        # In REMOTE mode the platforms run inside the worker process, not in Core.
        # Calling async_unload_platforms here would fail because the EntityComponents
        # for binary_sensor/sensor/switch/update were never registered in Core.
        # The ProcessExecutor is stopped via the async_on_unload callback registered
        # in async_setup_remote(), so we just need to clean up the executor reference.
        result = await async_teardown_remote(hass, entry)

        # Remove all states that were pushed by the remote worker so they don't
        # linger in the UI after the integration is removed.
        import homeassistant.helpers.entity_registry as er_module

        entity_registry = er_module.async_get(hass)
        remote_entities = er_module.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        )
        for er_entry in remote_entities:
            hass.states.async_remove(er_entry.entity_id)

        return result

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def api_by_version(
    hass: HomeAssistant,
    entry: dict[str, Any],
    version: int,
    password: str | None = None,
) -> HoleV5 | HoleV6:
    """Create a pi-hole API object by API version number. Once V5 is deprecated this function can be removed."""

    if password is None:
        password = entry.get(CONF_API_KEY, "")
    session = async_get_clientsession(hass, entry[CONF_VERIFY_SSL])
    hole_kwargs = {
        "host": entry[CONF_HOST],
        "session": session,
        "location": entry[CONF_LOCATION],
        "verify_tls": entry[CONF_VERIFY_SSL],
        "version": version,
    }
    if version == 5:
        hole_kwargs["tls"] = entry.get(CONF_SSL)
        hole_kwargs["api_token"] = password
    elif version == 6:
        hole_kwargs["protocol"] = "https" if entry.get(CONF_SSL) else "http"
        hole_kwargs["password"] = password

    return Hole(**hole_kwargs)


async def determine_api_version(
    hass: HomeAssistant, entry: dict[str, Any]
) -> Literal[5, 6]:
    """Determine the API version of the Pi-hole instance without requiring authentication.

    Neither API v5 or v6 provides an endpoint to check the version without authentication.
    Version 6 provides other enddpoints that do not require authentication, so we can use those to determine the version
    version 5 returns an empty list in response to unauthenticated requests.
    Because we are using endpoints that are not designed for this purpose, we should log liberally to help with debugging.
    """

    holeV6 = api_by_version(hass, entry, 6, password="wrong_password")
    try:
        await holeV6.authenticate()
    except HoleConnectionError as err:
        _LOGGER.error(
            "Unexpected error connecting to Pi-hole v6 API at %s: %s. Trying version 5 API",
            holeV6.base_url,
            err,
        )
    # Ideally python-hole would raise a specific exception for authentication failures
    except HoleError as ex_v6:
        if str(ex_v6) == "Authentication failed: Invalid password":
            _LOGGER.debug(
                "Success connecting to Pi-hole at %s without auth, API version is : %s",
                holeV6.base_url,
                6,
            )
            return 6
        _LOGGER.debug(
            "Connection to %s failed: %s, trying API version 5", holeV6.base_url, ex_v6
        )
    else:
        # It seems that occasionally the auth can succeed unexpectedly when there is a valid session
        _LOGGER.warning(
            "Authenticated with %s through v6 API, but succeeded with an incorrect password. This is a known bug",
            holeV6.base_url,
        )
        return 6
    holeV5 = api_by_version(hass, entry, 5, password="wrong_token")
    try:
        await holeV5.get_data()

    except HoleConnectionError as err:
        _LOGGER.error(
            "Failed to connect to Pi-hole v5 API at %s: %s", holeV5.base_url, err
        )
    else:
        # V5 API returns [] to unauthenticated requests
        if not holeV5.data:
            _LOGGER.debug(
                "Response '[]' from API without auth, pihole API version 5 probably detected at %s",
                holeV5.base_url,
            )
            return 5
        _LOGGER.debug(
            "Unexpected response from Pi-hole API at %s: %s",
            holeV5.base_url,
            str(holeV5.data),
        )
    _LOGGER.debug(
        "Could not determine pi-hole API version at: %s",
        holeV6.base_url,
    )
    raise HoleError("Could not determine Pi-hole API version")
