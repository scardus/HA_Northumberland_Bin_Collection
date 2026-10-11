"""Tests for the config flow."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.northumberland_bin_collection.api import (
    CannotConnect,
    NoAddressesFound,
)
from custom_components.northumberland_bin_collection.const import DOMAIN

from .conftest import ADDRESS_ID, ADDRESS_NAME, POSTCODE


@pytest.fixture
def mock_flow_api() -> Generator[AsyncMock]:
    """Return one address for any postcode."""
    with patch(
        "custom_components.northumberland_bin_collection.config_flow.NorthumberlandBinApi",
        autospec=True,
    ) as api_class:
        api = api_class.return_value
        api.get_addresses.return_value = [{"id": ADDRESS_ID, "name": ADDRESS_NAME}]
        yield api


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Stop the new entry being set up."""
    with (
        patch(
            "custom_components.northumberland_bin_collection.async_setup_entry",
            return_value=True,
        ) as setup_entry,
        patch(
            "custom_components.northumberland_bin_collection.async_unload_entry",
            return_value=True,
        ),
    ):
        yield setup_entry


async def test_user_flow(hass: HomeAssistant, mock_flow_api: AsyncMock) -> None:
    """A postcode and an address create an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"postcode": " ne12 3ab "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "address"
    mock_flow_api.get_addresses.assert_awaited_once_with(hass, POSTCODE)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"address": ADDRESS_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{ADDRESS_NAME}, {POSTCODE}"
    assert result["data"] == {
        "postcode": POSTCODE,
        "address_id": ADDRESS_ID,
        "address_name": ADDRESS_NAME,
    }
    assert result["result"].unique_id == ADDRESS_ID

    # Unload while the mocks are still active
    assert await hass.config_entries.async_unload(result["result"].entry_id)


@pytest.mark.parametrize(
    ("error", "errors"),
    [
        (NoAddressesFound, {"postcode": "invalid_postcode"}),
        (CannotConnect, {"base": "cannot_connect"}),
        (RuntimeError, {"base": "unknown"}),
    ],
)
async def test_postcode_errors(
    hass: HomeAssistant,
    mock_flow_api: AsyncMock,
    error: type[Exception],
    errors: dict[str, str],
) -> None:
    """A failed postcode lookup shows an error and the flow can recover."""
    mock_flow_api.get_addresses.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"postcode": POSTCODE}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == errors

    mock_flow_api.get_addresses.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"postcode": POSTCODE}
    )
    assert result["step_id"] == "address"


async def test_already_configured(
    hass: HomeAssistant,
    mock_flow_api: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The same address can't be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"postcode": POSTCODE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"address": ADDRESS_ID}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
