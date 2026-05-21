"""Typhur sensor entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TyphurConfigEntry
from .const import COOKING_MODE_MAP, COOKING_STATE_MAP
from .coordinator import TyphurCoordinator, TyphurDeviceState
from .entity import TyphurEntity


@dataclass(frozen=True, kw_only=True)
class TyphurSensorDescription(SensorEntityDescription):
    """Describes a Typhur sensor."""


SENSOR_DESCRIPTIONS: tuple[TyphurSensorDescription, ...] = (
    TyphurSensorDescription(
        key="status",
        translation_key="status",
        icon="mdi:air-fryer",
    ),
    TyphurSensorDescription(
        key="cooking_mode",
        translation_key="cooking_mode",
        icon="mdi:chef-hat",
    ),
    TyphurSensorDescription(
        key="cooking_state",
        translation_key="cooking_state",
        icon="mdi:progress-clock",
    ),
    TyphurSensorDescription(
        key="top_temperature",
        translation_key="top_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
    ),
    TyphurSensorDescription(
        key="bottom_temperature",
        translation_key="bottom_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
    ),
    TyphurSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        icon="mdi:thermometer-chevron-up",
    ),
    TyphurSensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        icon="mdi:timer",
    ),
    TyphurSensorDescription(
        key="elapsed_time",
        translation_key="elapsed_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        icon="mdi:timer-play",
    ),
    TyphurSensorDescription(
        key="total_cook_time",
        translation_key="total_cook_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        icon="mdi:timer-outline",
    ),
    TyphurSensorDescription(
        key="preheat_remaining",
        translation_key="preheat_remaining",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        icon="mdi:thermometer-lines",
    ),
    TyphurSensorDescription(
        key="cooking_stage",
        translation_key="cooking_stage",
        icon="mdi:stairs",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TyphurConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TyphurCoordinator = entry.runtime_data

    entities: list[TyphurSensorEntity] = []
    for device_id in (coordinator.data or {}):
        for desc in SENSOR_DESCRIPTIONS:
            entities.append(TyphurSensorEntity(coordinator, device_id, desc))

    async_add_entities(entities)


class TyphurSensorEntity(TyphurEntity, SensorEntity):
    """A sensor entity for a Typhur device field."""

    entity_description: TyphurSensorDescription

    def __init__(
        self,
        coordinator: TyphurCoordinator,
        device_id: str,
        description: TyphurSensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        state = self._state
        if state is None:
            return None

        key = self.entity_description.key

        match key:
            case "status":
                return state.derived_status
            case "cooking_mode":
                mode = state.cooking_mode
                return COOKING_MODE_MAP.get(mode, f"mode_{mode}") if mode is not None else None
            case "cooking_state":
                cs = state.cooking_state
                return COOKING_STATE_MAP.get(cs, f"state_{cs}") if cs is not None else None
            case "top_temperature":
                return state.cur_temperature_f
            case "bottom_temperature":
                return state.cur_down_temperature_f
            case "target_temperature":
                return state.target_temperature_f
            case "remaining_time":
                return state.remaining_time
            case "elapsed_time":
                return state.elapsed_time
            case "total_cook_time":
                return state.set_time
            case "preheat_remaining":
                return state.preheat_remaining
            case "cooking_stage":
                stage = state.cooking_stage
                total = state.cooking_stage_count
                if stage is not None and total is not None:
                    return f"{stage}/{total}"
                return None
            case _:
                return None

    @property
    def available(self) -> bool:
        """
        Temperature/timer sensors become unavailable when not cooking;
        status and mode sensors are always available if device is reachable.
        """
        if not super().available:
            return False

        state = self._state
        if state is None:
            return False

        cooking_only = {
            "top_temperature",
            "bottom_temperature",
            "target_temperature",
            "remaining_time",
            "elapsed_time",
            "total_cook_time",
            "preheat_remaining",
            "cooking_stage",
            "cooking_mode",
            "cooking_state",
        }

        if self.entity_description.key in cooking_only:
            # Available throughout the whole cook session (cooking, paused, done)
            return state.is_in_cook_session

        return True
