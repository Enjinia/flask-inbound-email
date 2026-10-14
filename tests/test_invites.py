from datetime import date
from zoneinfo import ZoneInfo

from conftest import make_email

from flask_inbound_email import parse_invite, read_email

INVITE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example//Calendar//EN
METHOD:{method}
BEGIN:VEVENT
UID:meeting-1@example.com
SEQUENCE:{sequence}
DTSTART:{start}
DTEND:20261020T090000Z
SUMMARY:Project kickoff
LOCATION:Room 4
ORGANIZER;CN=Bob Builder:mailto:bob@builder.example
DESCRIPTION:Join: https://meet.google.com/abc-defg-hij
END:VEVENT
END:VCALENDAR
"""


def ics(method="REQUEST", sequence=0, start="20261020T080000Z"):
    return INVITE.format(method=method, sequence=sequence, start=start).replace("\n", "\r\n").encode()


def test_invite_is_read_in_the_user_time_zone():
    invite = parse_invite(ics(), ZoneInfo("Europe/Rome"))
    assert (invite.day, invite.time) == (date(2026, 10, 20), "10:00")
    assert invite.summary == "Project kickoff"
    assert invite.location == "Room 4"
    assert invite.organizer == "Bob Builder"
    assert invite.video_link == "https://meet.google.com/abc-defg-hij"
    assert (invite.method, invite.sequence, invite.uid) == ("REQUEST", 0, "meeting-1@example.com")


def test_without_time_zone_times_stay_utc_and_local_times_stay_as_written():
    assert parse_invite(ics()).time == "08:00"
    assert parse_invite(ics(start="20261020T140000"), ZoneInfo("Asia/Tokyo")).time == "14:00"


def test_all_day_event_and_cancellation():
    invite = parse_invite(ics(method="CANCEL", sequence=2, start="20261020").replace(
        b"DTSTART:20261020", b"DTSTART;VALUE=DATE:20261020"))
    assert (invite.day, invite.time, invite.method, invite.sequence) == (date(2026, 10, 20), None, "CANCEL", 2)


def test_garbage_is_not_an_invite():
    assert parse_invite(b"not a calendar") is None


def test_invite_attached_to_an_email_is_found():
    msg = make_email("You are invited", attachments=[("invite.ics", "text/calendar", ics(), False)])
    assert read_email(msg.as_bytes(), tz=ZoneInfo("Europe/Rome")).invite.time == "10:00"
