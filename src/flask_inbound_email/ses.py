"""Pull received emails from Amazon SES: SES stores each email in S3 and notifies SNS -> SQS.

Polling a queue works the same on a laptop and in production, with no public webhook. A message
is deleted from the queue (and its email from S3) only once processed; when the handler raises
TemporaryFailure it stays, and SQS makes it visible again later.
"""

import json
import logging

from flask import current_app

from .checks import Verdicts
from .extension import REJECTED, TemporaryFailure

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    cfg = current_app.config
    return bool(cfg.get("INBOUND_DOMAIN") and cfg.get("INBOUND_QUEUE_URL"))


def clients():
    """(sqs, s3) clients. Without INBOUND_AWS_* keys boto3 looks for credentials as usual
    (environment, ~/.aws, the instance role)."""
    import boto3  # optional dependency: pip install flask-inbound-email[aws]

    cfg = current_app.config
    session = boto3.session.Session(
        aws_access_key_id=cfg.get("INBOUND_AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=cfg.get("INBOUND_AWS_SECRET_ACCESS_KEY"),
        region_name=cfg["INBOUND_AWS_REGION"],
    )
    return session.client("sqs"), session.client("s3")


def verdicts_of(receipt: dict) -> Verdicts:
    def status(key):
        return (receipt.get(key) or {}).get("status", "GRAY")

    return Verdicts(spf=status("spfVerdict"), dkim=status("dkimVerdict"), dmarc=status("dmarcVerdict"),
                    spam=status("spamVerdict"), virus=status("virusVerdict"))


def _notification(sqs_message):
    """SES notification from an SQS message (SNS envelope or raw delivery)."""
    body = json.loads(sqs_message["Body"])
    return json.loads(body["Message"]) if "Message" in body and "Type" in body else body


def poll(max_batches: int = 5, *, sqs=None, s3=None) -> dict:
    """Process what is waiting in the queue. Returns counters."""
    inbound = current_app.extensions["inbound_email"]
    if sqs is None or s3 is None:
        sqs, s3 = clients()
    queue_url = current_app.config["INBOUND_QUEUE_URL"]
    counts = {"received": 0, "handled": 0, "rejected": 0, "retry_later": 0}

    def delete(m):
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])

    for _ in range(max_batches):
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1)
        messages = resp.get("Messages", [])
        if not messages:
            break
        for m in messages:
            try:
                note = _notification(m)
            except (ValueError, KeyError):
                logger.warning("Inbound email: unreadable queue message %s dropped", m.get("MessageId"))
                delete(m)
                continue
            if note.get("notificationType") != "Received":  # e.g. the test notification SES sends at setup
                delete(m)
                continue

            receipt = note["receipt"]
            action = receipt["action"]
            counts["received"] += 1
            try:
                obj = s3.get_object(Bucket=action["bucketName"], Key=action["objectKey"])
                result = inbound.process(obj["Body"].read(), recipients=receipt.get("recipients", []),
                                         verdicts=verdicts_of(receipt))
            except TemporaryFailure:
                counts["retry_later"] += 1  # left in the queue: tried again when visible again
                continue
            counts["rejected" if result.outcome == REJECTED else "handled"] += 1
            s3.delete_object(Bucket=action["bucketName"], Key=action["objectKey"])
            delete(m)
    return counts
