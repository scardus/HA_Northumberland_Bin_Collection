from __future__ import annotations

import logging
import re
import tempfile
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession, RequestsError

from homeassistant.core import HomeAssistant

from .const import BASE_URL

_LOGGER = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 1_000_000   # 1 MB — council page is typically ~50 KB
MAX_CSRF_LENGTH = 256
MAX_ADDRESSES = 200
MAX_EVENTS = 500

# curl_cffi uses libcurl's own SSL stack. The council website doesn't serve its
# intermediate CA (Sectigo RSA OV Secure Server CA), so we build a combined CA
# bundle from certifi's Mozilla roots plus our bundled intermediate cert.
def _build_ca_bundle() -> str:
    intermediate = (Path(__file__).parent / "certs" / "intermediate.pem").read_bytes()
    try:
        import certifi
        base = Path(certifi.where()).read_bytes()
    except (ImportError, OSError):
        import ssl as _ssl
        cafile = _ssl.get_default_verify_paths().cafile
        base = Path(cafile).read_bytes() if cafile and Path(cafile).exists() else b""
    out = Path(tempfile.gettempdir()) / "nbc_ca_bundle.pem"
    out.write_bytes(base + b"\n" + intermediate)
    return str(out)

CA_BUNDLE = _build_ca_bundle()

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


class CannotConnect(Exception):
    pass


class NoAddressesFound(Exception):
    pass


class ParseError(Exception):
    pass


def _extract_csrf(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    token = soup.find("input", {"name": "_csrf"})
    if not token or not token.get("value"):
        raise ParseError("CSRF token not found in page")
    value = token["value"]
    if len(value) > MAX_CSRF_LENGTH:
        raise ParseError("CSRF token exceeds expected length — unexpected page content")
    return value


def _extract_bot_cookie(html: str) -> tuple[str, int, int] | None:
    """Compute the x-bni-ja bot-detection cookie value from the page's inline script.

    Returns (cookie_value_str, fixed_number, variable_number) or None if the
    script is not found.
    """
    raw_script = re.search(r"<script[^>]*>(var _0xcaad=.*?)</script>", html, re.DOTALL)
    _LOGGER.debug("Bot-detection script: %s", raw_script.group(1).strip() if raw_script else "NOT FOUND")
    m = re.search(r"_0x\w+=(-?\d+);var _0x\w+=(-?\d+)", html)
    if not m:
        return None
    fixed, variable = int(m.group(1)), int(m.group(2))
    return str(fixed + variable), fixed, variable


def _parse_addresses(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    select = soup.find("select", {"name": "address"})
    if not select:
        raise NoAddressesFound("Address dropdown not found — postcode may be invalid")
    options = [
        {"id": opt["value"], "name": opt.get_text(strip=True)}
        for opt in select.find_all("option")
        if opt.get("value")
    ][:MAX_ADDRESSES]
    if not options:
        raise NoAddressesFound("No addresses listed for this postcode")
    return options


def _parse_date_text(text: str, year: int) -> tuple[date, int, bool] | None:
    """Parse a date string like '29 April' or '29 April 2026'.

    Returns (date, month_num, year_was_explicit) or None if unparseable.
    """
    parts = text.split()
    if len(parts) < 2:
        return None
    try:
        day = int(parts[0])
    except ValueError:
        return None
    month_num = MONTH_NAMES.get(parts[1].lower())
    if month_num is None:
        return None
    explicit_year = False
    if len(parts) >= 3:
        try:
            year = int(parts[2])
            explicit_year = True
        except ValueError:
            pass
    try:
        return date(year, month_num, day), month_num, explicit_year
    except ValueError:
        return None


def _parse_calendar(html: str) -> list[dict]:
    """Parse the /calendarPrint HTML page into a list of collection events."""
    soup = BeautifulSoup(html, "html.parser")
    events: list[dict] = []
    current_year = date.today().year
    last_month_num: int | None = None

    tables = soup.find_all("table", class_="govuk-table") or soup.find_all("table")
    _LOGGER.debug("_parse_calendar: HTML length=%d, tables found=%d", len(html), len(tables))

    for table in tables:
        prev = table.find_previous_sibling()
        while prev is not None:
            if prev.name in ("h1", "h2", "h3", "h4", "p"):
                m = re.search(r"\b(20\d{2})\b", prev.get_text())
                if m:
                    new_year = int(m.group(1))
                    if new_year != current_year:
                        current_year = new_year
                        last_month_num = None
                break
            prev = prev.find_previous_sibling()

        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 3:
                continue

            date_text = cells[0].get_text(strip=True)
            collection_type = cells[2].get_text(strip=True)

            result = _parse_date_text(date_text, current_year)
            if result is None:
                continue

            event_date, month_num, explicit_year = result

            if explicit_year:
                current_year = event_date.year
            elif last_month_num is not None and month_num < last_month_num:
                current_year += 1
                try:
                    event_date = event_date.replace(year=current_year)
                except ValueError:
                    continue

            last_month_num = month_num

            if collection_type:
                events.append({"summary": collection_type, "date": event_date})
                if len(events) >= MAX_EVENTS:
                    _LOGGER.warning("Reached event limit (%d); truncating results", MAX_EVENTS)
                    return events

    return events


class NorthumberlandBinApi:
    async def get_addresses(self, hass: HomeAssistant, postcode: str) -> list[dict]:
        """Run the postcode lookup flow and return a list of address dicts."""
        try:
            async with AsyncSession(impersonate="firefox") as session:
                r = await session.get(f"{BASE_URL}/start", verify=CA_BUNDLE)
                r.raise_for_status()
                html = r.text[:MAX_RESPONSE_BYTES]

                csrf = await hass.async_add_executor_job(_extract_csrf, html)

                r = await session.post(
                    f"{BASE_URL}/postcode",
                    data={"_csrf": csrf, "postcode": postcode},
                    verify=CA_BUNDLE,
                    allow_redirects=True,
                )
                r.raise_for_status()
                html = r.text[:MAX_RESPONSE_BYTES]

                return await hass.async_add_executor_job(_parse_addresses, html)

        except (NoAddressesFound, ParseError):
            raise
        except RequestsError as err:
            raise CannotConnect(f"Network error: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error fetching addresses")
            raise CannotConnect(str(err)) from err

    async def get_calendar_events(
        self, hass: HomeAssistant, address_id: str
    ) -> list[dict]:
        """Fetch all collection events for the year using a 3-step session flow."""
        try:
            async with AsyncSession(impersonate="firefox") as session:
                # Step 1 — GET /address-select: establishes session + CSRF token.
                r = await session.get(f"{BASE_URL}/address-select", verify=CA_BUNDLE)
                r.raise_for_status()
                html = r.text[:MAX_RESPONSE_BYTES]

                csrf = await hass.async_add_executor_job(_extract_csrf, html)

                result = await hass.async_add_executor_job(_extract_bot_cookie, html)
                if result is not None:
                    bot_cookie, fixed, variable = result
                    session.cookies.set(
                        "x-bni-ja", bot_cookie,
                        domain="bincollection.northumberland.gov.uk",
                    )
                    _LOGGER.debug(
                        "Step 1 bot cookie: fixed=%d, variable=%d, cookie=%s",
                        fixed, variable, bot_cookie,
                    )
                else:
                    _LOGGER.debug("Step 1: bot-detection script not found")

                # Step 2 — submit address ID, follow redirect to results page.
                r = await session.post(
                    f"{BASE_URL}/address-select",
                    data={"_csrf": csrf, "address": address_id},
                    verify=CA_BUNDLE,
                    allow_redirects=True,
                )
                r.raise_for_status()
                results_html = r.text[:MAX_RESPONSE_BYTES]
                results_url = str(r.url)
                _LOGGER.debug("Results page URL after address-select: %s", results_url)

                result = await hass.async_add_executor_job(_extract_bot_cookie, results_html)
                if result is not None:
                    bot_cookie, fixed, variable = result
                    session.cookies.set(
                        "x-bni-ja", bot_cookie,
                        domain="bincollection.northumberland.gov.uk",
                    )
                    _LOGGER.debug(
                        "Step 2 bot cookie: fixed=%d, variable=%d, cookie=%s",
                        fixed, variable, bot_cookie,
                    )
                else:
                    _LOGGER.warning(
                        "Bot-detection cookie could not be computed — the calendar "
                        "may return only a limited number of upcoming collections. "
                        "The site's script obfuscation may have changed."
                    )

                # Step 3 — fetch full year calendar.
                r = await session.get(
                    f"{BASE_URL}/calendarPrint",
                    verify=CA_BUNDLE,
                    headers={"Referer": results_url},
                )
                r.raise_for_status()
                html = r.text[:MAX_RESPONSE_BYTES]
                _LOGGER.debug("calendarPrint final URL: %s", str(r.url))
                _LOGGER.debug(
                    "calendarPrint response: length=%d, first 1000 chars=%.1000s",
                    len(html), html,
                )

                events = await hass.async_add_executor_job(_parse_calendar, html)
                _LOGGER.debug(
                    "Parsed %d calendar events from %d-byte response",
                    len(events), len(html),
                )

                if not events:
                    _LOGGER.warning(
                        "No calendar events parsed — the page structure may have changed"
                    )

                return events

        except ParseError:
            raise
        except RequestsError as err:
            raise CannotConnect(f"Network error: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error fetching calendar events")
            raise CannotConnect(str(err)) from err
