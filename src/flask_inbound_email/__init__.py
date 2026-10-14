"""Give every user of your Flask app their own email address, and read what arrives there.

InboundEmail is the Flask extension (see extension.py). The reading part also works on its own:

    from flask_inbound_email import read_email, rejection, Verdicts

    email = read_email(raw_bytes, tz=ZoneInfo("Europe/Rome"))
    email.forward.note, email.forward.body, email.files, email.skipped, email.invite
"""

from .attachments import Attachment, Limits, SkippedFile, SkipReason
from .checks import Rejection, Verdicts, rejection
from .extension import InboundEmail, Result, TemporaryFailure, new_token
from .forwarded import Forwarded, clean_subject, split_forward
from .invites import Invite, parse_invite
from .reader import ReceivedEmail, parse_message, read_email, sender_of
from .text import html_to_text, strip_quoted

__version__ = "0.1.0"

__all__ = [
    "Attachment",
    "Forwarded",
    "InboundEmail",
    "Invite",
    "Limits",
    "ReceivedEmail",
    "Rejection",
    "Result",
    "SkipReason",
    "SkippedFile",
    "TemporaryFailure",
    "Verdicts",
    "clean_subject",
    "html_to_text",
    "new_token",
    "parse_invite",
    "parse_message",
    "read_email",
    "rejection",
    "sender_of",
    "split_forward",
    "strip_quoted",
]
