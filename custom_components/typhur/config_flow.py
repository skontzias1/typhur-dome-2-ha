"""Config flow for the Typhur integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TyphurApiClient, TyphurAuthError, TyphurApiError
from .const import CONF_REGION, DEFAULT_REGION, DOMAIN, TYPHUR_API_BY_REGION

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_REGION, default=DEFAULT_REGION): vol.In(
            list(TYPHUR_API_BY_REGION.keys())
        ),
    }
)


class TyphurConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup UI for Typhur."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email    = user_input[CONF_EMAIL].strip().lower()
            password = user_input[CONF_PASSWORD]
            region   = user_input[CONF_REGION]

            # Prevent duplicate entries for the same account
            await self.async_set_unique_id(f"typhur_{email}_{region}")
            self._abort_if_unique_id_configured()

            client = TyphurApiClient(email, password, region)
            await client.async_init()
            try:
                await client.async_login()
                devices = await client.async_get_devices()
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    await client.async_close()
                    return self.async_create_entry(
                        title=f"Typhur ({email})",
                        data={
                            CONF_EMAIL: email,
                            CONF_PASSWORD: password,
                            CONF_REGION: region,
                        },
                    )
            except TyphurAuthError:
                errors["base"] = "invalid_auth"
            except TyphurApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Typhur setup")
                errors["base"] = "unknown"
            finally:
                await client.async_close()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
