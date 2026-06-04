"""Typhur DataUpdateCoordinator — orchestrates auth, MQTT, and REST fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MqttCredentials, TyphurApiClient, TyphurAuthError, TyphurApiError, TyphurDevice
from .const import (
    CERT_REFRESH_INTERVAL,
    DOMAIN,
    FALLBACK_POLL_INTERVAL,
    MQTT_INITIAL_RECONNECT_DELAY,
    MQTT_MAX_RECONNECT_DELAY,
    TOKEN_REFRESH_INTERVAL,
)
from .mqtt_client import TyphurMqttClient

_LOGGER = logging.getLogger(__name__)


class TyphurDeviceState:
    """Holds the current parsed state for a single Typhur device."""

    def __init__(self, device: TyphurDevice) -> None:
        self.device = device
        self.raw: dict[str, Any] = device.last_state.copy()
        self.last_updated: float = time.monotonic()

    def update(self, cmd_data: dict[str, Any]) -> None:
        self.raw = cmd_data
        self.last_updated = time.monotonic()

    # ── Convenience accessors ─────────────────────────────────────────────

    @property
    def global_status(self) -> str:
        return self.raw.get("globalStatus", "offline")

    @property
    def cooking_state(self) -> int | None:
        return self.raw.get("cookingState")

    @property
    def cur_temperature_f(self) -> float | None:
        v = self.raw.get("curTemperature")
        return v / 10.0 if v is not None else None

    @property
    def cur_down_temperature_f(self) -> float | None:
        v = self.raw.get("curDownTemperature")
        return v / 10.0 if v is not None else None

    @property
    def target_temperature_f(self) -> float | None:
        params = self.raw.get("setParams") or []
        if params:
            v = params[0].get("setTemperature")
            return v / 10.0 if v is not None else None
        return None

    @property
    def set_time(self) -> int | None:
        params = self.raw.get("setParams") or []
        return params[0].get("setTime") if params else None

    @property
    def cooking_mode(self) -> int | None:
        params = self.raw.get("setParams") or []
        return params[0].get("cookingMode") if params else None

    @property
    def remaining_time(self) -> int | None:
        return self.raw.get("curRemainingTime")

    @property
    def elapsed_time(self) -> int | None:
        return self.raw.get("curCookSec")

    @property
    def preheat_remaining(self) -> int | None:
        return self.raw.get("curPreheatRemainingTime")

    @property
    def basket_in(self) -> bool:
        return self.raw.get("curBasketState", 0) == 0

    @property
    def fan_speed(self) -> int | None:
        v = self.raw.get("curFanSpeed")
        return v if v is not None and v >= 0 else None

    @property
    def error_code(self) -> int:
        return self.raw.get("errorCode", 0)

    @property
    def cooking_stage(self) -> int | None:
        return self.raw.get("cookingStage")

    @property
    def cooking_stage_count(self) -> int | None:
        return self.raw.get("cookingStageNum")

    @property
    def is_actively_cooking(self) -> bool:
        """True only when the heating element is running (not paused/done)."""
        return self.global_status == "cooking" and self.cooking_state == 3

    @property
    def is_in_cook_session(self) -> bool:
        """True for the entire cook session including paused and done states."""
        return self.global_status == "cooking"

    # Keep old name as alias so existing references still work
    @property
    def is_cooking(self) -> bool:
        return self.is_actively_cooking

    @property
    def derived_status(self) -> str:
        """
        Human-friendly status derived from globalStatus + cookingState + basketState.

        globalStatus alone is insufficient: it stays "cooking" while paused and
        after the timer expires.  This property gives automations a clean single
        value to trigger on.
        """
        gs = self.global_status
        cs = self.cooking_state

        if gs == "offline":
            return "offline"
        if gs == "online":
            return "standby"
        if gs == "cooking":
            if cs == 3:
                return "cooking"
            if cs == 0:
                return "basket_open" if not self.basket_in else "paused"
            if cs == 4:
                return "done"
            if cs in (1, 2):
                return "preheating"
        return gs  # pass through any future unknown values

    @property
    def has_error(self) -> bool:
        return self.error_code != 0


class TyphurCoordinator(DataUpdateCoordinator[dict[str, TyphurDeviceState]]):
    """
    Central coordinator for the Typhur integration.

    Data flow:
      Primary  — Typhur cloud MQTT (AWS IoT, ~2s push updates while cooking)
      Fallback — REST poll of /app/device/bind/list every 30s when MQTT is down
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: TyphurApiClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=FALLBACK_POLL_INTERVAL),
        )
        self._api = api
        self._mqtt: TyphurMqttClient | None = None
        self._devices: list[TyphurDevice] = []
        self._creds: MqttCredentials | None = None
        self._token_refreshed_at: float = 0.0
        self._cert_refreshed_at: float = 0.0
        self._mqtt_connected = False
        self._refresh_lock = asyncio.Lock()
        # Guards against stacking reconnect tasks; owns the real back-off so a
        # flapping link escalates 5s → 10s → … → 300s instead of busy-looping.
        self._reconnecting = False
        self._reconnect_delay = MQTT_INITIAL_RECONNECT_DELAY

    # ── Setup / teardown ──────────────────────────────────────────────────

    async def async_setup(self) -> None:
        """Authenticate, discover devices, and start the MQTT listener."""
        await self._api.async_init()
        await self._refresh_token()
        await self._discover_devices()
        await self._start_mqtt()

    async def async_shutdown(self) -> None:
        """Clean up connections."""
        if self._mqtt:
            self._mqtt.stop()
            self._mqtt = None
        await self._api.async_close()

    # ── DataUpdateCoordinator hook (REST fallback) ────────────────────────

    async def _async_update_data(self) -> dict[str, TyphurDeviceState]:
        """
        Called by the coordinator on the update_interval schedule.

        When MQTT is healthy this is effectively a no-op (data comes via push);
        when MQTT is down it becomes the primary data source.
        """
        if self._mqtt_connected:
            # MQTT is live — just return current data without an extra API call
            return self.data or {}

        _LOGGER.debug("Typhur: MQTT down, falling back to REST poll")
        try:
            await self._maybe_refresh_token()
            devices = await self._api.async_get_devices()
        except TyphurAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TyphurApiError as err:
            raise UpdateFailed(str(err)) from err

        result: dict[str, TyphurDeviceState] = {}
        for dev in devices:
            state = (self.data or {}).get(dev.device_id) or TyphurDeviceState(dev)
            state.update(dev.last_state)
            result[dev.device_id] = state

        return result

    # ── MQTT message handler ──────────────────────────────────────────────

    def handle_mqtt_message(self, topic: str, payload: dict[str, Any]) -> None:
        """
        Called on the HA event loop when a MQTT message arrives.

        Only ``AF04:status:report`` (and equivalent model-specific status
        messages) carry device state.  Other message types (version reports,
        bind confirmations, cmd receipts) are logged and ignored for now.
        """
        cmd_type: str = payload.get("cmdType", "")
        cmd_data: dict[str, Any] = payload.get("cmdData", {})
        device_id: str = str(payload.get("deviceId", ""))

        if not cmd_type.endswith(":status:report"):
            _LOGGER.debug("Typhur MQTT: ignoring cmdType=%s", cmd_type)
            return

        # Find matching device by ID (embedded in topic or payload)
        if not device_id:
            # Parse device ID from topic: device/{model}/{id}/pub
            parts = topic.split("/")
            device_id = parts[2] if len(parts) >= 3 else ""

        current = (self.data or {}).get(device_id)
        if current is None:
            # Unknown device — trigger a re-discovery
            self.hass.async_create_task(self._discover_devices())
            return

        current.update(cmd_data)
        self.async_set_updated_data({**self.data, device_id: current})

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _refresh_token(self) -> None:
        try:
            await self._api.async_login()
            self._token_refreshed_at = time.monotonic()
        except TyphurAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err

    async def _maybe_refresh_token(self) -> None:
        if time.monotonic() - self._token_refreshed_at > TOKEN_REFRESH_INTERVAL:
            async with self._refresh_lock:
                if time.monotonic() - self._token_refreshed_at > TOKEN_REFRESH_INTERVAL:
                    await self._refresh_token()

    async def _discover_devices(self) -> None:
        devices = await self._api.async_get_devices()
        self._devices = devices

        states: dict[str, TyphurDeviceState] = {}
        existing = self.data or {}
        for dev in devices:
            state = existing.get(dev.device_id) or TyphurDeviceState(dev)
            # Seed with REST-provided last state if we have nothing yet
            if not state.raw:
                state.update(dev.last_state)
            states[dev.device_id] = state

        self.async_set_updated_data(states)
        _LOGGER.debug("Typhur: discovered %d device(s)", len(devices))

    async def _start_mqtt(self) -> None:
        try:
            self._creds = await self._api.async_get_mqtt_certs()
            self._cert_refreshed_at = time.monotonic()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Typhur: failed to get MQTT certs, will rely on REST polling: %s", err)
            return

        topics = [t for dev in self._devices for t in dev.sub_topics]
        if not topics:
            _LOGGER.warning("Typhur: no MQTT topics found for subscribed devices")
            return

        self._mqtt = TyphurMqttClient(
            hass_loop=self.hass.loop,
            creds=self._creds,
            topics=topics,
            on_message=self.handle_mqtt_message,
            on_connected=self._handle_mqtt_connected,
            on_disconnected=self._handle_mqtt_disconnected,
        )
        self._mqtt.start()

    def _handle_mqtt_connected(self) -> None:
        self._mqtt_connected = True
        _LOGGER.info("Typhur: MQTT connection established")

    def _handle_mqtt_disconnected(self) -> None:
        self._mqtt_connected = False
        # Never stack reconnect attempts — one in-flight task handles recovery
        # and re-arms itself only if the link is still down after the back-off.
        if self._reconnecting:
            return
        self._reconnecting = True
        _LOGGER.warning("Typhur: MQTT disconnected — switching to REST polling")
        self.hass.async_create_task(self._reconnect_mqtt())

    async def _reconnect_mqtt(self) -> None:
        """Refresh certs and rebuild MQTT after a disconnect, with back-off."""
        try:
            await asyncio.sleep(self._reconnect_delay)

            # paho's loop_start auto-reconnect may have already recovered the
            # link during the back-off window — if so, don't churn the client.
            if self._mqtt_connected:
                self._reconnect_delay = MQTT_INITIAL_RECONNECT_DELAY
                return

            try:
                await self._maybe_refresh_token()
                self._creds = await self._api.async_get_mqtt_certs()
                self._cert_refreshed_at = time.monotonic()
                if self._mqtt:
                    topics = [t for dev in self._devices for t in dev.sub_topics]
                    self._mqtt.update_credentials(self._creds, topics)
                self._reconnect_delay = MQTT_INITIAL_RECONNECT_DELAY
            except Exception as err:  # noqa: BLE001
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, MQTT_MAX_RECONNECT_DELAY
                )
                _LOGGER.warning(
                    "Typhur: cert refresh failed, retrying in %ss: %s",
                    self._reconnect_delay,
                    err,
                )
        finally:
            self._reconnecting = False
