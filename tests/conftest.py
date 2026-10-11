"""Fixtures for the Northumberland Bin Collection tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.northumberland_bin_collection.const import DOMAIN

ADDRESS_ID = "100000000001"
ADDRESS_NAME = "15 Example Street"
POSTCODE = "NE12 3AB"

EVENTS = [
    {"summary": "General waste", "date": date(2026, 10, 14)},
    {"summary": "Recycling", "date": date(2026, 10, 21)},
    {"summary": "Garden waste", "date": date(2026, 10, 28)},
]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow the integration under test to load."""


@pytest.fixture
def mock_api() -> Generator[AsyncMock]:
    """Stop the coordinator calling the council website."""
    with patch(
        "custom_components.northumberland_bin_collection.coordinator.NorthumberlandBinApi",
        autospec=True,
    ) as api_class:
        api = api_class.return_value
        api.get_calendar_events.return_value = list(EVENTS)
        yield api


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for one address."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"{ADDRESS_NAME}, {POSTCODE}",
        unique_id=ADDRESS_ID,
        data={
            "postcode": POSTCODE,
            "address_id": ADDRESS_ID,
            "address_name": ADDRESS_NAME,
        },
    )


@pytest.fixture
async def unload_entries(hass: HomeAssistant) -> AsyncGenerator[None]:
    """Unload every entry after the test so the weekly refresh timer is cancelled."""
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    unload_entries: None,
) -> MockConfigEntry:
    """Set up the integration for one address."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
