"""The Flask extension with a tiny made-up app: users live in a dict, the log in a list."""

import json
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from conftest import make_email
from flask import Flask

from flask_inbound_email import InboundEmail, Result, TemporaryFailure, Verdicts, new_token, ses

TOKEN = "alice-x7k2m9p4q1"
INVITE = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:1@example.com\r\n"
          b"DTSTART:20261020T080000Z\r\nSUMMARY:Kickoff\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


@dataclass
class User:
    email: str
    extra: list = field(default_factory=list)
    zone: str | None = None
    language: str = "en"


class World:
    def __init__(self):
        self.users = {TOKEN: User("alice@example.com", extra=["alice@work.example"])}
        self.handled, self.answers, self.log, self.languages = [], [], [], []
        self.fail = None


@pytest.fixture
def world():
    return World()


@pytest.fixture
def app(world):
    app = Flask(__name__)
    app.config.update(INBOUND_DOMAIN="in.example.org", INBOUND_LEGACY_DOMAINS=["old.example.org"],
                      INBOUND_QUEUE_URL="https://sqs.example/queue", INBOUND_TIMEZONE="Europe/Rome")
    inbound = InboundEmail(app)

    @inbound.user_loader
    def load(token):
        return world.users.get(token)

    @inbound.trusted_senders
    def senders(user):
        return [user.email, *user.extra]

    @inbound.handler
    def handle(user, email):
        if world.fail:
            raise world.fail
        world.handled.append(email)
        world.languages.append(current_language[0])
        return Result("handled", extra={"tasks": 1}) if "task" in email.text else None

    @inbound.notifier
    def answer(user, email, result):
        world.answers.append((email.sender, result.outcome, current_language[0]))

    @inbound.recorder
    def record(user, sender, subject, result):
        world.log.append((user.email if user else None, sender, result.outcome, result.reason))

    current_language = ["server"]

    @inbound.user_context
    @contextmanager
    def speaking(user):
        current_language[0] = user.language
        yield
        current_language[0] = "server"

    @inbound.user_timezone
    def zone(user):
        return user.zone

    return app


def send(app, text="new task: call Bob", *, to=f"{TOKEN}@in.example.org", verdicts=None, **kw):
    kw.setdefault("sender", "Alice <Alice@Example.com>")
    with app.app_context():
        inbound = app.extensions["inbound_email"]
        return inbound.process(make_email(text, **kw).as_bytes(), recipients=[to], verdicts=verdicts)


def test_an_email_from_the_user_is_handled_and_answered(app, world):
    result = send(app)
    assert (result.outcome, result.extra) == ("handled", {"tasks": 1})
    assert world.handled[0].text.strip() == "new task: call Bob"
    assert world.answers == [("alice@example.com", "handled", "en")]
    assert world.log == [("alice@example.com", "alice@example.com", "handled", "")]


def test_handler_returning_none_counts_as_handled(app, world):
    assert send(app, "just a note").outcome == "handled"


def test_handler_and_notifier_run_in_the_user_context(app, world):
    world.users[TOKEN].language = "it"
    send(app)
    assert world.languages == ["it"] and world.answers[0][2] == "it"


def test_a_verified_extra_address_is_trusted_and_the_old_domain_still_works(app, world):
    assert send(app, sender="alice@work.example", to=f"{TOKEN}@old.example.org").outcome == "handled"


@pytest.mark.parametrize("kw, reason", [
    ({"to": "someone-else@in.example.org"}, "unknown_address"),
    ({"to": f"{TOKEN}@elsewhere.example"}, "unknown_address"),
    ({"sender": "mallory@evil.example"}, "untrusted_sender"),
    ({"verdicts": Verdicts(dmarc="FAIL")}, "dmarc_failed"),
    ({"headers": {"Auto-Submitted": "auto-replied"}}, "auto_reply"),
])
def test_rejected_emails_are_logged_and_never_answered(app, world, kw, reason):
    result = send(app, **kw)
    assert (result.outcome, result.reason) == ("rejected", reason)
    assert world.handled == [] and world.answers == []
    assert world.log[0][2:] == ("rejected", reason)


def test_invites_use_the_user_time_zone_or_the_default(app, world):
    attachments = [("invite.ics", "text/calendar", INVITE, False)]
    send(app, attachments=attachments)
    world.users[TOKEN].zone = "America/New_York"
    send(app, attachments=attachments)
    assert [e.invite.time for e in world.handled] == ["10:00", "04:00"]


def test_a_broken_notifier_does_not_undo_the_work(app, world):
    @app.extensions["inbound_email"].notifier
    def broken(user, email, result):
        raise ConnectionError("SMTP down")

    assert send(app).outcome == "handled" and len(world.handled) == 1


def test_missing_callbacks_are_said_clearly():
    app = Flask(__name__)
    inbound = InboundEmail(app)
    with app.app_context(), pytest.raises(RuntimeError, match="user_loader"):
        inbound.process(b"Subject: hi\r\n\r\nhi", recipients=["x@y"])


def test_tokens_are_readable_and_unguessable(app):
    token = new_token("Alice.Smith+news@example.com")
    assert token.startswith("alicesmithne-") and len(token) == 23
    assert new_token() != new_token()
    with app.app_context():
        assert app.extensions["inbound_email"].address_for(token) == f"{token}@in.example.org"
        assert app.extensions["inbound_email"].address_for(None) is None


# --- the Amazon SES queue, with fake SQS and S3 -------------------------------------------------

class FakeAWS:
    def __init__(self, emails):
        self.objects, self.queue, self.deleted_messages, self.deleted_objects = {}, [], [], []
        for i, (raw, verdict) in enumerate(emails):
            key = f"mail/{i}"
            self.objects[key] = raw
            note = {"notificationType": "Received", "receipt": {
                "recipients": [f"{TOKEN}@in.example.org"], "action": {"bucketName": "b", "objectKey": key},
                **{f"{k}Verdict": {"status": verdict} for k in ("spf", "dkim", "dmarc", "spam", "virus")},
            }}
            self.queue.append({"MessageId": str(i), "ReceiptHandle": f"h{i}",
                               "Body": json.dumps({"Type": "Notification", "Message": json.dumps(note)})})
        self.queue.append({"MessageId": "setup", "ReceiptHandle": "h-setup",
                           "Body": json.dumps({"notificationType": "AmazonSnsSubscriptionSucceeded"})})
        self.queue.append({"MessageId": "junk", "ReceiptHandle": "h-junk", "Body": "not json"})

    # SQS
    def receive_message(self, **kw):
        batch = [m for m in self.queue if m["ReceiptHandle"] not in self.deleted_messages]
        return {"Messages": batch} if batch and not getattr(self, "_served", False) else {}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted_messages.append(ReceiptHandle)

    # S3
    def get_object(self, Bucket, Key):
        self._served = True
        raw = self.objects[Key]

        class Body:
            def read(self):
                return raw

        return {"Body": Body()}

    def delete_object(self, Bucket, Key):
        self.deleted_objects.append(Key)


def test_poll_processes_the_queue_and_cleans_up(app, world):
    good = make_email("new task", sender="alice@example.com").as_bytes()
    spam = make_email("buy now", sender="alice@example.com").as_bytes()
    aws = FakeAWS([(good, "PASS"), (spam, "FAIL")])
    with app.app_context():
        counts = ses.poll(sqs=aws, s3=aws)
    assert counts == {"received": 2, "handled": 1, "rejected": 1, "retry_later": 0}
    assert sorted(aws.deleted_messages) == ["h-junk", "h-setup", "h0", "h1"]
    assert aws.deleted_objects == ["mail/0", "mail/1"]


def test_poll_leaves_the_email_in_the_queue_on_temporary_failure(app, world):
    world.fail = TemporaryFailure("API down")
    aws = FakeAWS([(make_email("new task", sender="alice@example.com").as_bytes(), "PASS")])
    with app.app_context():
        counts = ses.poll(sqs=aws, s3=aws)
    assert counts["retry_later"] == 1
    assert "h0" not in aws.deleted_messages and aws.deleted_objects == []


def test_the_command_needs_the_configuration():
    app = Flask(__name__)
    InboundEmail(app)
    result = app.test_cli_runner().invoke(args=["inbound", "poll"])
    assert result.exit_code != 0 and "INBOUND_QUEUE_URL" in result.output


def test_an_eml_file_can_be_tried_locally(app, world, tmp_path):
    eml = tmp_path / "note.eml"
    eml.write_bytes(make_email("new task: water the plants", sender="alice@example.com").as_bytes())
    runner = app.test_cli_runner()
    assert runner.invoke(args=["inbound", "read", str(eml), "--to", f"{TOKEN}@in.example.org"]).output == "handled\n"
    assert world.answers == []  # nobody is written to from a local try
    out = runner.invoke(args=["inbound", "read", str(eml), "--to", "nobody@in.example.org"]).output
    assert out == "rejected (unknown_address)\n"
