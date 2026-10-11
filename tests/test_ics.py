"""Tests for rendering collections as an iCalendar document."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from homeassistant.components.calendar import CalendarEvent

from custom_components.northumberland_bin_collection.ics import render_ics

STAMP = datetime(2026, 10, 11, 9, 30, tzinfo=UTC)


def _event(summary: str, day: date) -> CalendarEvent:
    return CalendarEvent(
        summary=summary,
        start=day,
        end=day + timedelta(days=1),
        uid=f"nbc_{day.isoformat()}_{summary.lower().replace(' ', '_')}",
    )


def _unfold(ics: str) -> list[str]:
    return ics.replace("\r\n ", "").split("\r\n")


def test_render_all_day_events() -> None:
    """Each collection becomes an all-day, non-busy event with a stable UID."""
    ics = render_ics(
        "Bin Collection",
        [_event("General waste", date(2026, 10, 14))],
        "100000000001.northumberland_bin_collection",
        STAMP,
    )
    assert ics.split("\r\n") == [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//scardus//Northumberland Bin Collection//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Bin Collection",
        "BEGIN:VEVENT",
        "UID:nbc_2026-10-14_general_waste@100000000001.northumberland_bin_collection",
        "DTSTAMP:20261011T093000Z",
        "DTSTART;VALUE=DATE:20261014",
        "DTEND;VALUE=DATE:20261015",
        "SUMMARY:General waste",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]


def test_render_no_events() -> None:
    """An empty schedule is still a valid calendar."""
    ics = render_ics("Bin Collection", [], "example", STAMP)
    assert "BEGIN:VEVENT" not in ics
    assert ics.endswith("END:VCALENDAR\r\n")


def test_dtstamp_is_utc() -> None:
    """The timestamp is converted to UTC."""
    stamp = datetime(2026, 10, 11, 10, 30, tzinfo=timezone(timedelta(hours=1)))
    ics = render_ics(
        "Bin Collection", [_event("Recycling", date(2026, 10, 21))], "example", stamp
    )
    assert "DTSTAMP:20261011T093000Z" in ics.split("\r\n")


def test_text_is_escaped() -> None:
    """Commas, semicolons, backslashes and newlines are escaped."""
    ics = render_ics(
        "Bin Collection – 1, The Street",
        [_event("Glass; cans\\tins\nand jars", date(2026, 10, 14))],
        "example",
        STAMP,
    )
    lines = _unfold(ics)
    assert "X-WR-CALNAME:Bin Collection – 1\\, The Street" in lines
    assert "SUMMARY:Glass\\; cans\\\\tins\\nand jars" in lines


def test_long_lines_are_folded() -> None:
    """Lines over 75 octets are folded without splitting multi-byte characters."""
    # "X-WR-CALNAME:" plus 61 characters is 74 octets, so the 3-octet dash
    # doesn't fit on the first line
    name = "x" * 61 + "–" + "y" * 100
    ics = render_ics(name, [], "example", STAMP)
    lines = ics.split("\r\n")
    assert all(len(line.encode()) <= 75 for line in lines)
    first = lines.index("X-WR-CALNAME:" + "x" * 61)
    assert lines[first + 1].startswith(" –y")
    assert f"X-WR-CALNAME:{name}" in _unfold(ics)
