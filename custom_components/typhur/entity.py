"""Base entity for Typhur devices."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TyphurCoordinator, TyphurDeviceState


class TyphurEntity(CoordinatorEntity[TyphurCoordinator]):
    """Base class for all Typhur entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TyphurCoordinator,
        device_id: str,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{unique_id_suffix}"

    @property
    def _state(self) -> TyphurDeviceState | None:
        return (self.coordinator.data or {}).get(self._device_id)

    @property
    def available(self) -> bool:
        state = self._state
        if state is None:
            return False
        return state.global_status != "offline"

    @property
    def device_info(self) -> DeviceInfo:
        state = self._state
        device = state.device if state else None
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device.device_name if device else "Typhur",
            manufacturer="Typhur",
            model=device.device_model if device else None,
            serial_number=device.device_sn if device else None,
        )
