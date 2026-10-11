from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from homeassistant.components.calendar import CalendarEvent

PRODID = "-//scardus//Northumberland Bin Collection//EN"
MAX_LINE_OCTETS = 75


def render_ics(
    name: str,
    events: Iterable[CalendarEvent],
    uid_domain: str,
    stamp: datetime,
) -> str:
    """Render all-day collection events as an iCalendar (RFC 5545) document."""
    dtstamp = stamp.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
    ]
    for event in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event.uid}@{uid_domain}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{event.start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{event.end:%Y%m%d}",
            f"SUMMARY:{_escape(event.summary)}",
            # Collections shouldn't mark the subscriber as busy all day
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(f"{_fold(line)}\r\n" for line in lines)


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    # Lines are limited to 75 octets; continuation lines start with a space and
    # multi-byte characters must not be split
    parts: list[str] = []
    current = ""
    size = 0
    for char in line:
        width = len(char.encode())
        if size + width > MAX_LINE_OCTETS:
            parts.append(current)
            current = " "
            size = 1
        current += char
        size += width
    parts.append(current)
    return "\r\n".join(parts)
