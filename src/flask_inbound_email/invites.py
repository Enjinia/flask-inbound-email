"""Calendar invitations (.ics) carried by an email, read in the user's time zone."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone, tzinfo
from email.message import EmailMessage

from icalendar import Calendar

from .text import html_to_text

_VIDEO_LINK = re.compile(r"https://(meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com|teams\.live\.com)/\S+")


@dataclass
class Invite:
    uid: str
    method: str  # REQUEST, CANCEL, PUBLISH...
    sequence: int  # grows each time the organiser changes the event
    summary: str
    day: date  # local date of the start
    time: str | None  # local "HH:MM", None for all-day events
    location: str | None
    description: str
    organizer: str
    video_link: str | None  # Google Meet, Zoom or Teams


def find_calendar(msg: EmailMessage) -> bytes | None:
    """Raw bytes of the first text/calendar part or .ics attachment, or None."""
    for part in msg.walk():
        filename = (part.get_filename() or "").lower()
        if part.get_content_type() == "text/calendar" or filename.endswith(".ics"):
            data = part.get_payload(decode=True)
            if data:
                return data
    return None


def parse_invite(data: bytes, tz: tzinfo | None = None) -> Invite | None:
    """First VEVENT of a calendar as an Invite, or None if unreadable.

    tz is the user's time zone (e.g. ZoneInfo("Europe/Rome")); None keeps times in UTC.
    Floating times (no zone in the file) are taken as already local.
    """
    try:
        cal = Calendar.from_ical(data)
    except Exception:
        return None
    method = str(cal.get("METHOD", "PUBLISH")).upper()
    event = next(iter(cal.walk("VEVENT")), None)
    if event is None or event.get("DTSTART") is None:
        return None

    start = event.get("DTSTART").dt
    if isinstance(start, datetime):
        local = start if start.tzinfo is None else start.astimezone(tz or timezone.utc)
        day, time = local.date(), local.strftime("%H:%M")
    else:
        day, time = start, None

    description = str(event.get("DESCRIPTION", "") or "")
    if "<" in description and ">" in description:
        description = html_to_text(description)
    conference = str(event.get("X-GOOGLE-CONFERENCE", "") or "")
    link = _VIDEO_LINK.search(conference) or _VIDEO_LINK.search(description)
    organizer = str(event.get("ORGANIZER", "") or "")
    organizer_name = event.get("ORGANIZER").params.get("CN", "") if event.get("ORGANIZER") is not None else ""

    return Invite(
        uid=str(event.get("UID", "")),
        method=method,
        sequence=int(event.get("SEQUENCE", 0) or 0),
        summary=str(event.get("SUMMARY", "") or "").strip(),
        day=day,
        time=time,
        location=str(event.get("LOCATION", "") or "").strip() or None,
        description=description.strip(),
        organizer=str(organizer_name) or re.sub(r"^mailto:", "", organizer, flags=re.I),
        video_link=link.group(0) if link else None,
    )

