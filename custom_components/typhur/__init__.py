"""The Typhur integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import TyphurApiClient, TyphurAuthError, TyphurApiError
from .const import CONF_REGION, DOMAIN
from .coordinator import TyphurCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

type TyphurConfigEntry = ConfigEntry[TyphurCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: TyphurConfigEntry) -> bool:
    """Set up Typhur from a config entry."""
    api = TyphurApiClient(
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        region=entry.data[CONF_REGION],
    )

    coordinator = TyphurCoordinator(hass, api)

    try:
        await coordinator.async_setup()
    except ConfigEntryAuthFailed:
        raise
    except TyphurAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except TyphurApiError as err:
        raise ConfigEntryNotReady(str(err)) from err
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("Unexpected error setting up Typhur entry")
        raise ConfigEntryNotReady(str(err)) from err

    # Perform an initial data fetch so platforms have data before loading
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: TyphurConfigEntry) -> bool:
    """Unload a Typhur config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: TyphurConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
