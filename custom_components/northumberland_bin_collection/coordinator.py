from __future__ import annotations

import logging
import random
from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, NorthumberlandBinApi, ParseError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

WEEKS_BETWEEN_UPDATES = 1
MAX_JITTER_SECONDS = 86_400  # up to 24 hours of random spread


class NorthumberlandCoordinator(DataUpdateCoordinator[list[dict]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN)
        self._postcode: str = entry.data["postcode"]
        self._address_id: str = entry.data["address_id"]
        self._api = NorthumberlandBinApi()
        self._unsub_scheduled: Callable[[], None] | None = None

    def _schedule_next_update(self) -> None:
        if self._unsub_scheduled:
            self._unsub_scheduled()
        jitter = random.randint(0, MAX_JITTER_SECONDS)
        delay = timedelta(weeks=WEEKS_BETWEEN_UPDATES).total_seconds() + jitter
        _LOGGER.debug("Next bin collection refresh in %.1f hours", delay / 3600)
        self._unsub_scheduled = async_call_later(
            self.hass, delay, self._handle_scheduled_refresh
        )

    async def _handle_scheduled_refresh(self, _now: datetime) -> None:
        await self.async_request_refresh()
        self._schedule_next_update()

    def schedule_updates(self) -> None:
        self._schedule_next_update()

    def cancel_updates(self) -> None:
        if self._unsub_scheduled:
            self._unsub_scheduled()
            self._unsub_scheduled = None

    async def _async_update_data(self) -> list[dict]:
        try:
            result = await self._api.get_calendar_events(
                self.hass, self._postcode, self._address_id
            )
            async_delete_issue(self.hass, DOMAIN, "update_failed")
            return result
        except (CannotConnect, ParseError) as err:
            async_create_issue(
                self.hass,
                DOMAIN,
                "update_failed",
                is_fixable=False,
                severity=IssueSeverity.WARNING,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            )
            raise UpdateFailed(f"Error fetching bin collection data: {err}") from err
