"""A whole app in one file: Alice forwards emails to her own address and they become notes.

Try it without AWS:

    pip install flask-inbound-email
    flask --app examples/app.py inbound read examples/forwarded.eml --to alice-x7k2m9p4q1@in.example.org
    flask --app examples/app.py run          # then open http://127.0.0.1:5000

With AWS (see the README): set the INBOUND_* variables and run `flask --app examples/app.py inbound poll`
from a timer. A real app keeps users and notes in its database; here they live in memory and a
JSON file, to keep the example short.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask
from markupsafe import escape

from flask_inbound_email import InboundEmail, Result, TemporaryFailure

app = Flask(__name__)
app.config.update(
    INBOUND_DOMAIN="in.example.org",
    INBOUND_TIMEZONE="Europe/Rome",
)
inbound = InboundEmail(app)
NOTES = Path(__file__).with_name("notes.json")


@dataclass
class User:
    name: str
    email: str
    inbound_token: str  # made with flask_inbound_email.new_token(email)
    verified_emails: list[str] = field(default_factory=list)


USERS = {u.inbound_token: u for u in [
    User("Alice", "alice@example.com", "alice-x7k2m9p4q1", verified_emails=["alice@work.example"]),
]}


@inbound.user_loader
def load_user(token):
    return USERS.get(token)


@inbound.trusted_senders
def trusted_senders(user):
    return [user.email, *user.verified_emails]


@inbound.handler
def handle(user, email):
    notes = json.loads(NOTES.read_text()) if NOTES.exists() else []
    if any(n["message_id"] == email.message_id for n in notes):
        return Result("ignored", "already received")  # the same email can arrive twice
    try:
        notes.append({
            "user": user.name,
            "message_id": email.message_id,
            "note": email.forward.note,
            "from": email.forward.original_from or email.sender,
            "subject": email.forward.original_subject or email.subject,
            "attachments": [f.filename for f in email.files],
            "not_read": [f"{s.filename}: {s.reason.value}" for s in email.skipped],
            "meeting": email.invite and f"{email.invite.summary} on {email.invite.day} {email.invite.time or ''}",
        })
        NOTES.write_text(json.dumps(notes, indent=2))
    except OSError as e:  # e.g. the disk is full: keep the email in the queue and try later
        raise TemporaryFailure(str(e)) from e
    return Result("handled")


@inbound.notifier
def answer(user, email, result):
    # A real app sends a short email here, with the header "Auto-Submitted: auto-replied".
    print(f"Would tell {email.sender}: {result.outcome}")


@app.get("/")
def index():
    notes = json.loads(NOTES.read_text()) if NOTES.exists() else []
    address = inbound.address_for(USERS["alice-x7k2m9p4q1"].inbound_token)
    items = "".join(f"<li><b>{escape(n['subject'])}</b> from {escape(n['from'])}: {escape(n['note'])}</li>"
                    for n in notes)
    return f"<h1>Alice's notes</h1><p>Forward emails to <code>{address}</code></p><ul>{items}</ul>"
