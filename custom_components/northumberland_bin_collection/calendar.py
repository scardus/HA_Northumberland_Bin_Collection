from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import partial

from aiohttp import web
from aiohttp.hdrs import METH_GET

from homeassistant.components import webhook
from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_ICS_FEED, CONF_WEBHOOK_ID, DOMAIN
from .coordinator import NorthumberlandCoordinator
from .ics import async_feed_url, render_ics

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
        self._address_id: str = entry.data["address_id"]
        self._device_name = f"Bin Collection – {address_name}"
        self._webhook_id: str | None = (
            entry.options.get(CONF_WEBHOOK_ID)
            if entry.options.get(CONF_ICS_FEED, False)
            else None
        )
        self._attr_unique_id = f"nbc_{entry.entry_id}"
        self._attr_name = "Bin Collection"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self._device_name,
            manufacturer="Northumberland County Council",
            model=entry.data.get("postcode", ""),
            # Always set so the link is cleared when the feed is turned off
            configuration_url=(
                async_feed_url(coordinator.hass, self._webhook_id)
                if self._webhook_id
                else None
            ),
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._webhook_id:
            webhook.async_register(
                self.hass,
                DOMAIN,
                self._device_name,
                self._webhook_id,
                self._async_handle_feed,
                allowed_methods=[METH_GET],
            )
            self.async_on_remove(
                partial(webhook.async_unregister, self.hass, self._webhook_id)
            )

    async def _async_handle_feed(
        self,
        hass: HomeAssistant,
        webhook_id: str,
        request: web.Request,
    ) -> web.Response:
        events = [
            _make_event(e)
            for e in sorted(self.coordinator.data or [], key=lambda e: e["date"])
        ]
        return web.Response(
            text=render_ics(
                self._device_name,
                events,
                f"{self._address_id}.{DOMAIN}",
                dt_util.utcnow(),
            ),
            content_type="text/calendar",
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
