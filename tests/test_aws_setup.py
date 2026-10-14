"""The AWS setup against moto's fake AWS, then a whole trip: S3 + SNS -> SQS -> poll()."""

import json

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from conftest import make_email  # noqa: E402
from flask import Flask  # noqa: E402

from flask_inbound_email import InboundEmail, Result, ses  # noqa: E402
from flask_inbound_email.aws_setup import main, setup, write_env  # noqa: E402

REGION = "eu-west-1"


@pytest.fixture
def aws(monkeypatch):
    for key, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                       "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(key, value)
    with moto.mock_aws():
        yield boto3.session.Session(region_name=REGION)


def test_setup_creates_everything_once_and_writes_the_env(aws, tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET_KEY=keep-me\nINBOUND_DOMAIN=old.example\n")

    env = setup(aws, app="myapp", domain="in.myapp.example", env_file=env_file)
    out = capsys.readouterr().out
    assert "MX     in.myapp.example  ->  10 inbound-smtp.eu-west-1.amazonaws.com" in out
    assert env["INBOUND_AWS_SECRET_ACCESS_KEY"] not in out  # the key never reaches the screen

    lines = env_file.read_text().splitlines()
    assert lines[:2] == ["SECRET_KEY=keep-me", "INBOUND_DOMAIN=in.myapp.example"]
    assert any(line.startswith("INBOUND_AWS_ACCESS_KEY_ID=") for line in lines)

    rules = aws.client("ses").describe_active_receipt_rule_set()["Rules"]
    assert [(r["Name"], r["Recipients"]) for r in rules] == [("myapp-inbound", ["in.myapp.example"])]
    policy = aws.client("iam").get_user_policy(UserName="myapp-inbound-reader", PolicyName="inbound-read")
    actions = {a for s in policy["PolicyDocument"]["Statement"] for a in s["Action"]}
    assert "s3:PutObject" not in actions and "sqs:SendMessage" not in actions

    again = setup(aws, app="myapp", domain="in.myapp.example", env_file=env_file)
    assert "INBOUND_AWS_ACCESS_KEY_ID" not in again  # no second key
    assert len(aws.client("ses").describe_active_receipt_rule_set()["Rules"]) == 1
    assert len(aws.client("iam").list_access_keys(UserName="myapp-inbound-reader")["AccessKeyMetadata"]) == 1
    assert env_file.read_text().count("INBOUND_AWS_ACCESS_KEY_ID=") == 1


def test_an_existing_rule_set_of_another_app_is_shared(aws, tmp_path):
    client = aws.client("ses")
    client.create_receipt_rule_set(RuleSetName="otherapp")
    client.set_active_receipt_rule_set(RuleSetName="otherapp")
    setup(aws, app="myapp", domain="in.myapp.example", env_file=tmp_path / ".env")
    assert client.describe_active_receipt_rule_set()["Metadata"]["Name"] == "otherapp"


def test_write_env_creates_a_missing_file(tmp_path):
    write_env(tmp_path / ".env", {"A": "1"})
    assert (tmp_path / ".env").read_text() == "A=1\n"


def test_the_command_reports_aws_errors(aws, tmp_path, monkeypatch):
    def broken(*a, **kw):
        from botocore.exceptions import ClientError
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetCallerIdentity")

    monkeypatch.setattr("flask_inbound_email.aws_setup.setup", broken)
    with pytest.raises(SystemExit, match="AWS error"):
        main(["--app", "myapp", "--domain", "in.myapp.example", "--env-file", str(tmp_path / ".env")])


def test_a_whole_trip_through_s3_sns_and_sqs(aws, tmp_path):
    env = setup(aws, app="myapp", domain="in.myapp.example", env_file=tmp_path / ".env")
    bucket = "myapp-inbound-123456789012"  # moto's account id
    raw = make_email("call Bob", sender="alice@example.com").as_bytes()
    aws.client("s3").put_object(Bucket=bucket, Key="raw/one", Body=raw)
    # What SES publishes when an email arrives
    note = {"notificationType": "Received", "receipt": {
        "recipients": ["alice-x7k2m9p4q1@in.myapp.example"],
        "action": {"type": "S3", "bucketName": bucket, "objectKey": "raw/one"},
        **{f"{k}Verdict": {"status": "PASS"} for k in ("spf", "dkim", "dmarc", "spam", "virus")},
    }}
    topic = aws.client("sns").create_topic(Name="myapp-inbound")["TopicArn"]
    aws.client("sns").publish(TopicArn=topic, Message=json.dumps(note))

    app = Flask(__name__)
    app.config.update({k: v for k, v in env.items() if not k.startswith("INBOUND_AWS_")},
                      INBOUND_AWS_REGION=REGION)
    inbound = InboundEmail(app)
    handled = []
    inbound.user_loader(lambda token: {"alice-x7k2m9p4q1": "alice"}.get(token))
    inbound.trusted_senders(lambda user: ["alice@example.com"])
    inbound.handler(lambda user, email: handled.append((user, email.text.strip())) or Result("handled"))

    result = app.test_cli_runner().invoke(args=["inbound", "poll"])
    assert result.output.strip() == "received=1 handled=1 rejected=0 retry_later=0"
    assert handled == [("alice", "call Bob")]
    assert aws.client("s3").list_objects_v2(Bucket=bucket).get("KeyCount") == 0
    with app.app_context():
        assert ses.poll() == {"received": 0, "handled": 0, "rejected": 0, "retry_later": 0}
