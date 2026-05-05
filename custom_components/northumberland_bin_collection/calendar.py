from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NorthumberlandCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NorthumberlandCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([NorthumberlandBinCalendar(coordinator, entry)])


class NorthumberlandBinCalendar(CoordinatorEntity[NorthumberlandCoordinator], CalendarEntity):
    _attr_has_entity_name = True
    _attr_supported_features = CalendarEntityFeature(0)

    def __init__(
        self,
        coordinator: NorthumberlandCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        address_name = entry.data["address_name"]
        self._attr_unique_id = f"nbc_{entry.entry_id}"
        self._attr_name = "Bin Collection"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Bin Collection – {address_name}",
            manufacturer="Northumberland County Council",
            model=entry.data.get("postcode", ""),
        )

    def _upcoming_events(self) -> list[dict]:
        if not self.coordinator.data:
            return []
        today = date.today()
        return sorted(
            (e for e in self.coordinator.data if e["date"] >= today),
            key=lambda e: e["date"],
        )

    @property
    def event(self) -> CalendarEvent | None:
        upcoming = self._upcoming_events()
        if not upcoming:
            return None
        return _make_event(upcoming[0])

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        if not self.coordinator.data:
            return []

        start_d = start_date.date() if isinstance(start_date, datetime) else start_date
        end_d = end_date.date() if isinstance(end_date, datetime) else end_date

        return [
            _make_event(e)
            for e in self.coordinator.data
            if e["date"] >= start_d and e["date"] <= end_d
        ]


def _make_event(e: dict) -> CalendarEvent:
    event_date: date = e["date"]
    return CalendarEvent(
        summary=e["summary"],
        start=event_date,
        end=event_date + timedelta(days=1),
        uid=f"nbc_{event_date.isoformat()}_{e['summary'].lower().replace(' ', '_')}",
    )
