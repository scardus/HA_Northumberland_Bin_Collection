from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import CannotConnect, NoAddressesFound, NorthumberlandBinApi
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class NorthumberlandBinConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._postcode: str = ""
        self._addresses: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            postcode = user_input["postcode"].strip().upper()
            api = NorthumberlandBinApi()
            try:
                addresses = await api.get_addresses(self.hass, postcode)
                self._postcode = postcode
                self._addresses = addresses
                return await self.async_step_address()
            except NoAddressesFound:
                errors["postcode"] = "invalid_postcode"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during postcode lookup")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("postcode"): str}),
            errors=errors,
        )

    async def async_step_address(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            address_id = user_input["address"]
            address_name = next(
                (a["name"] for a in self._addresses if a["id"] == address_id),
                address_id,
            )
            await self.async_set_unique_id(address_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"{address_name}, {self._postcode}",
                data={
                    "postcode": self._postcode,
                    "address_id": address_id,
                    "address_name": address_name,
                },
            )

        return self.async_show_form(
            step_id="address",
            data_schema=vol.Schema({
                vol.Required("address"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": a["id"], "label": a["name"]}
                            for a in self._addresses
                        ],
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }),
            errors=errors,
        )
