"""Tests for setting up and unloading the integration."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.northumberland_bin_collection.const import DOMAIN


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The entry loads, its calendar returns the collections and it unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED

    entity_id = er.async_get(hass).async_get_entity_id(
        "calendar", DOMAIN, f"nbc_{init_integration.entry_id}"
    )
    assert entity_id is not None
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": entity_id,
            "start_date_time": "2026-10-01T00:00:00",
            "end_date_time": "2026-11-01T00:00:00",
        },
        blocking=True,
        return_response=True,
    )
    assert [event["summary"] for event in response[entity_id]["events"]] == [
        "General waste",
        "Recycling",
        "Garden waste",
    ]

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    assert init_integration.state is ConfigEntryState.NOT_LOADED
