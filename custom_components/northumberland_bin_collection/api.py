from __future__ import annotations

import logging
import re
import ssl
from datetime import date
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

from homeassistant.core import HomeAssistant

from .const import BASE_URL

_LOGGER = logging.getLogger(__name__)

# Safety limits for data parsed from the third-party website
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# SSL context that completes the certificate chain using the bundled intermediate CA.
# The council website does not serve its intermediate certificate, so we supply it here
# to allow full chain verification rather than disabling SSL checks entirely.
def _build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(
        cafile=Path(__file__).parent / "certs" / "intermediate.pem"
    )
    return ctx

SSL_CONTEXT = _build_ssl_context()
MAX_RESPONSE_BYTES = 1_000_000   # 1 MB — council page is typically ~50 KB
MAX_CSRF_LENGTH = 256
MAX_ADDRESSES = 200
MAX_EVENTS = 500

# Mimic a real browser so the council server serves the same full-year calendar
# it serves to browsers rather than a truncated "upcoming events" response.
SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.5",
}

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


async def _read_response(resp: aiohttp.ClientResponse) -> str:
    """Read a response body up to MAX_RESPONSE_BYTES and return as text."""
    resp.raise_for_status()
    raw = await resp.content.read(MAX_RESPONSE_BYTES)
    return raw.decode(resp.charset or "utf-8", errors="replace")


def _extract_csrf(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    token = soup.find("input", {"name": "_csrf"})
    if not token or not token.get("value"):
        raise ParseError("CSRF token not found in page")
    value = token["value"]
    if len(value) > MAX_CSRF_LENGTH:
        raise ParseError("CSRF token exceeds expected length — unexpected page content")
    return value


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

    # Use govuk-table class; fall back to all tables if none found
    tables = soup.find_all("table", class_="govuk-table") or soup.find_all("table")
    _LOGGER.debug("_parse_calendar: HTML length=%d, tables found=%d", len(html), len(tables))

    for table in tables:
        # Check for a preceding heading that contains a year (e.g. "April 2026")
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

            # Skip header rows — they won't parse as a date
            result = _parse_date_text(date_text, current_year)
            if result is None:
                continue

            event_date, month_num, explicit_year = result

            if explicit_year:
                current_year = event_date.year
            elif last_month_num is not None and month_num < last_month_num:
                # Month number decreased — crossed a year boundary
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
            async with aiohttp.ClientSession(
                timeout=REQUEST_TIMEOUT, headers=SESSION_HEADERS
            ) as session:
                async with session.get(f"{BASE_URL}/start", ssl=SSL_CONTEXT) as resp:
                    html = await _read_response(resp)

                csrf = await hass.async_add_executor_job(_extract_csrf, html)

                async with session.post(
                    f"{BASE_URL}/postcode",
                    data={"_csrf": csrf, "postcode": postcode},
                    ssl=SSL_CONTEXT,
                    allow_redirects=True,
                ) as resp:
                    html = await _read_response(resp)

                return await hass.async_add_executor_job(_parse_addresses, html)

        except (NoAddressesFound, ParseError):
            raise
        except aiohttp.ClientResponseError as err:
            raise CannotConnect(f"HTTP error {err.status}: {err.message}") from err
        except aiohttp.ClientError as err:
            raise CannotConnect(f"Network error: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error fetching addresses")
            raise CannotConnect(str(err)) from err

    async def get_calendar_events(
        self, hass: HomeAssistant, postcode: str, address_id: str
    ) -> list[dict]:
        """Run the full session flow and return all collection events for the year."""
        try:
            async with aiohttp.ClientSession(
                timeout=REQUEST_TIMEOUT, headers=SESSION_HEADERS
            ) as session:
                # Step 1 — get session cookie + initial CSRF
                async with session.get(f"{BASE_URL}/start", ssl=SSL_CONTEXT) as resp:
                    html = await _read_response(resp)
                csrf = await hass.async_add_executor_job(_extract_csrf, html)

                # Step 2 — submit postcode, follow redirect to address-select
                async with session.post(
                    f"{BASE_URL}/postcode",
                    data={"_csrf": csrf, "postcode": postcode},
                    ssl=SSL_CONTEXT,
                    allow_redirects=True,
                ) as resp:
                    html = await _read_response(resp)
                csrf = await hass.async_add_executor_job(_extract_csrf, html)

                # Step 3 — submit address ID, follow redirect to results page;
                # read the body so the connection is fully consumed and any
                # server-side session state triggered by viewing the results page
                # is recorded before we request the print calendar.
                async with session.post(
                    f"{BASE_URL}/address-select",
                    data={"_csrf": csrf, "address": address_id},
                    ssl=SSL_CONTEXT,
                    allow_redirects=True,
                ) as resp:
                    results_html = await _read_response(resp)
                    results_url = str(resp.url)
                    _LOGGER.debug("Results page URL after address-select: %s", results_url)

                if "x-bni-ja" in results_html:
                    _LOGGER.warning(
                        "The council website's bot-detection script is active — "
                        "the calendar may only show a limited number of upcoming "
                        "collections rather than the full year. "
                        "This is typically triggered by frequent requests; "
                        "if you have been reloading the integration repeatedly, "
                        "wait a while and the full schedule should return on the "
                        "next weekly refresh."
                    )

                # Step 4 — fetch full year calendar; send Referer so the server
                # treats this as navigation from the results page.
                async with session.get(
                    f"{BASE_URL}/calendarPrint",
                    ssl=SSL_CONTEXT,
                    headers={"Referer": results_url},
                ) as resp:
                    html = await _read_response(resp)
                    _LOGGER.debug("calendarPrint final URL: %s", str(resp.url))

                _LOGGER.debug(
                    "calendarPrint response: length=%d, first 1000 chars=%.1000s",
                    len(html), html,
                )
                events = await hass.async_add_executor_job(_parse_calendar, html)
                _LOGGER.debug("Parsed %d calendar events from %d-byte response", len(events), len(html))

                if not events:
                    _LOGGER.warning(
                        "No calendar events parsed — the page structure may have changed"
                    )

                return events

        except ParseError:
            raise
        except aiohttp.ClientResponseError as err:
            raise CannotConnect(f"HTTP error {err.status}: {err.message}") from err
        except aiohttp.ClientError as err:
            raise CannotConnect(f"Network error: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error fetching calendar events")
            raise CannotConnect(str(err)) from err
