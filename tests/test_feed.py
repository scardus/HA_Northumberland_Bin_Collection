"""Tests for publishing the calendar as an iCal feed."""

from __future__ import annotations

from datetime import date
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr

from custom_components.northumberland_bin_collection.const import (
    CONF_ICS_FEED,
    CONF_WEBHOOK_ID,
    DOMAIN,
)

from .conftest import ADDRESS_ID, ADDRESS_NAME, POSTCODE

EXTERNAL_URL = "https://bins.example.com"


@pytest.fixture(autouse=True)
async def external_url(hass: HomeAssistant) -> None:
    """Give Home Assistant an address that is reachable from the internet."""
    await hass.config.async_update(external_url=EXTERNAL_URL)


async def _set_feed(
    hass: HomeAssistant, entry: MockConfigEntry, enabled: bool
) -> dict[str, Any]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ICS_FEED: enabled}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    return result


def _feed_path(entry: MockConfigEntry) -> str:
    return f"/api/webhook/{entry.options[CONF_WEBHOOK_ID]}"


def _configuration_url(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return device.configuration_url


async def _assert_not_served(
    hass_client_no_auth: ClientSessionGenerator, path: str
) -> None:
    client = await hass_client_no_auth()
    response = await client.get(path)
    # Home Assistant answers unknown webhooks with an empty 200
    assert response.status == HTTPStatus.OK
    assert response.content_type != "text/calendar"
    assert await response.text() == ""


async def test_feed_off_by_default(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A new entry has no feed, and the options form starts with it unticked."""
    assert CONF_ICS_FEED not in init_integration.options
    assert _configuration_url(hass, init_integration) is None

    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({}) == {CONF_ICS_FEED: False}

    # The form already shows the address the feed would use, but nothing is served
    feed_url = result["description_placeholders"]["feed_url"]
    assert feed_url.startswith(f"{EXTERNAL_URL}/api/webhook/")
    await _assert_not_served(hass_client_no_auth, feed_url.removeprefix(EXTERNAL_URL))


async def test_turn_feed_on(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Turning the feed on serves the collections and links them from the device."""
    await _set_feed(hass, init_integration, True)
    assert init_integration.options[CONF_ICS_FEED] is True
    path = _feed_path(init_integration)
    assert _configuration_url(hass, init_integration) == f"{EXTERNAL_URL}{path}"

    client = await hass_client_no_auth()
    response = await client.get(path)
    assert response.status == HTTPStatus.OK
    assert response.content_type == "text/calendar"
    assert response.charset == "utf-8"
    body = await response.text()
    lines = body.split("\r\n")
    assert f"X-WR-CALNAME:Bin Collection – {ADDRESS_NAME}" in lines
    assert [line for line in lines if line.startswith("SUMMARY:")] == [
        "SUMMARY:General waste",
        "SUMMARY:Recycling",
        "SUMMARY:Garden waste",
    ]
    assert (
        f"UID:nbc_2026-10-14_general_waste@{ADDRESS_ID}.{DOMAIN}" in lines
    )

    response = await client.post(path)
    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED

    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["data_schema"]({}) == {CONF_ICS_FEED: True}
    assert result["description_placeholders"]["feed_url"] == f"{EXTERNAL_URL}{path}"


async def test_turn_feed_off_and_on_again(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Turning the feed off stops it, and turning it back on reuses the address."""
    await _set_feed(hass, init_integration, True)
    path = _feed_path(init_integration)

    await _set_feed(hass, init_integration, False)
    assert _feed_path(init_integration) == path
    assert _configuration_url(hass, init_integration) is None
    await _assert_not_served(hass_client_no_auth, path)

    await _set_feed(hass, init_integration, True)
    assert _feed_path(init_integration) == path
    client = await hass_client_no_auth()
    response = await client.get(path)
    assert response.content_type == "text/calendar"


async def test_unload_stops_feed(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Unloading the entry stops the feed."""
    await _set_feed(hass, init_integration, True)
    path = _feed_path(init_integration)

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await _assert_not_served(hass_client_no_auth, path)


async def test_feed_follows_refreshed_data(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AsyncMock,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """The feed is built from the latest schedule on every request."""
    await _set_feed(hass, init_integration, True)
    mock_api.get_calendar_events.return_value = [
        {"summary": "Recycling", "date": date(2026, 11, 4)},
    ]
    await hass.data[DOMAIN][init_integration.entry_id].async_refresh()

    client = await hass_client_no_auth()
    response = await client.get(_feed_path(init_integration))
    body = await response.text()
    assert "DTSTART;VALUE=DATE:20261104" in body.split("\r\n")
    assert body.count("BEGIN:VEVENT") == 1


async def test_one_feed_per_address(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Each address gets its own feed address and calendar name."""
    second = MockConfigEntry(
        domain=DOMAIN,
        title=f"17 Example Street, {POSTCODE}",
        unique_id="100000000002",
        data={
            "postcode": POSTCODE,
            "address_id": "100000000002",
            "address_name": "17 Example Street",
        },
    )
    second.add_to_hass(hass)
    await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    await _set_feed(hass, init_integration, True)
    await _set_feed(hass, second, True)
    assert _feed_path(init_integration) != _feed_path(second)

    client = await hass_client_no_auth()
    for entry, name in (
        (init_integration, ADDRESS_NAME),
        (second, "17 Example Street"),
    ):
        response = await client.get(_feed_path(entry))
        body = await response.text()
        assert f"X-WR-CALNAME:Bin Collection – {name}" in body.split("\r\n")
        assert f"@{entry.unique_id}.{DOMAIN}" in body
