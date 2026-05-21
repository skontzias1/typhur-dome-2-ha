"""Typhur binary sensor entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TyphurConfigEntry
from .coordinator import TyphurCoordinator
from .entity import TyphurEntity


@dataclass(frozen=True, kw_only=True)
class TyphurBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Typhur binary sensor."""


BINARY_SENSOR_DESCRIPTIONS: tuple[TyphurBinarySensorDescription, ...] = (
    TyphurBinarySensorDescription(
        key="cooking",
        translation_key="cooking",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:air-fryer",
    ),
    TyphurBinarySensorDescription(
        key="basket",
        translation_key="basket",
        # True = basket is inserted (door closed analogue)
        device_class=BinarySensorDeviceClass.DOOR,
        icon="mdi:tray-arrow-down",
    ),
    TyphurBinarySensorDescription(
        key="error",
        translation_key="error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert-circle",
    ),
    TyphurBinarySensorDescription(
        key="preheating",
        translation_key="preheating",
        icon="mdi:thermometer-lines",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TyphurConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TyphurCoordinator = entry.runtime_data

    entities: list[TyphurBinarySensorEntity] = []
    for device_id in (coordinator.data or {}):
        for desc in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(TyphurBinarySensorEntity(coordinator, device_id, desc))

    async_add_entities(entities)


class TyphurBinarySensorEntity(TyphurEntity, BinarySensorEntity):
    """A binary sensor entity for a Typhur device."""

    entity_description: TyphurBinarySensorDescription

    def __init__(
        self,
        coordinator: TyphurCoordinator,
        device_id: str,
        description: TyphurBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        state = self._state
        if state is None:
            return None

        match self.entity_description.key:
            case "cooking":
                return state.is_actively_cooking
            case "basket":
                # True = basket IN (door closed) — we invert curBasketState
                # curBasketState: 0 = basket inserted, 1 = removed (confirmed pending)
                return not state.basket_in  # True = door open (basket removed)
            case "error":
                return state.has_error
            case "preheating":
                cs = state.cooking_state
                return cs in (1, 2) if cs is not None else False
            case _:
                return None
