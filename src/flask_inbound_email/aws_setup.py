"""One-off AWS setup for an app's inbound email: Amazon SES -> S3 -> SNS -> SQS.

Run it once with ADMIN credentials (not the app's), e.g.:

    AWS_PROFILE=admin inbound-email-aws-setup --app myapp --domain in.myapp.example --env-file .env

It is idempotent: running it again skips what already exists. At the end it prints the DNS
records to add at the registrar, and writes the INBOUND_* variables into the .env file. The
access key it creates for the app can only read the queue and read/delete the stored emails,
and is never printed.
"""

import argparse
import json
import sys


def log(msg):
    print(f"  - {msg}")


def ensure_bucket(s3, bucket, region, account_id):
    from botocore.exceptions import ClientError

    try:
        s3.head_bucket(Bucket=bucket)
        log(f"bucket {bucket} already there")
    except ClientError:
        if region == "us-east-1":  # the one region that refuses a LocationConstraint
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})
        log(f"bucket {bucket} created")
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
    })
    # Safety net: emails are deleted right after processing, and in any case after 2 days
    s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": [{
        "ID": "expire-raw", "Status": "Enabled", "Filter": {"Prefix": "raw/"}, "Expiration": {"Days": 2},
    }]})
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
        "Sid": "AllowSESPuts", "Effect": "Allow", "Principal": {"Service": "ses.amazonaws.com"},
        "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{bucket}/raw/*",
        "Condition": {"StringEquals": {"AWS:SourceAccount": account_id}},
    }]}))


def ensure_topic_and_queue(sns, sqs, name, account_id):
    topic_arn = sns.create_topic(Name=name)["TopicArn"]  # idempotent
    sns.set_topic_attributes(TopicArn=topic_arn, AttributeName="Policy", AttributeValue=json.dumps({
        "Version": "2012-10-17", "Statement": [{
            "Sid": "AllowSESPublish", "Effect": "Allow", "Principal": {"Service": "ses.amazonaws.com"},
            "Action": "sns:Publish", "Resource": topic_arn,
            "Condition": {"StringEquals": {"AWS:SourceAccount": account_id}},
        }]}))
    log(f"SNS topic {topic_arn}")

    queue_url = sqs.create_queue(QueueName=name, Attributes={
        "VisibilityTimeout": "300", "MessageRetentionPeriod": "345600",  # 5 minutes, 4 days
    })["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    sqs.set_queue_attributes(QueueUrl=queue_url, Attributes={"Policy": json.dumps({
        "Version": "2012-10-17", "Statement": [{
            "Sid": "AllowTopic", "Effect": "Allow", "Principal": {"Service": "sns.amazonaws.com"},
            "Action": "sqs:SendMessage", "Resource": queue_arn,
            "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}},
        }]})})
    subs = sns.list_subscriptions_by_topic(TopicArn=topic_arn).get("Subscriptions", [])
    if not any(s["Endpoint"] == queue_arn for s in subs):
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)
    log(f"SQS queue {queue_url}")
    return topic_arn, queue_url, queue_arn


def ensure_ses(ses, name, rule_set_name, domain, bucket, topic_arn):
    from botocore.exceptions import ClientError

    # Receiving only: no DKIM (that is for sending from this subdomain, which the app never does)
    verification = ses.verify_domain_identity(Domain=domain)["VerificationToken"]
    log(f"domain {domain} registered in SES (waiting for the TXT record)")

    active = ses.describe_active_receipt_rule_set()
    if active.get("Metadata"):
        rule_set = active["Metadata"]["Name"]
        rules = active.get("Rules", [])
        log(f"using the receipt rule set already active, '{rule_set}' (a region has only one)")
    else:
        try:
            ses.create_receipt_rule_set(RuleSetName=rule_set_name)
        except ClientError as e:
            if e.response["Error"]["Code"] != "AlreadyExists":
                raise
        ses.set_active_receipt_rule_set(RuleSetName=rule_set_name)
        rule_set, rules = rule_set_name, []
        log(f"receipt rule set '{rule_set}' created and activated")

    if any(r["Name"] == name for r in rules):
        log(f"receipt rule '{name}' already there")
    else:
        ses.create_receipt_rule(RuleSetName=rule_set, Rule={
            "Name": name, "Enabled": True, "ScanEnabled": True, "TlsPolicy": "Optional",
            "Recipients": [domain],
            "Actions": [{"S3Action": {"BucketName": bucket, "ObjectKeyPrefix": "raw/", "TopicArn": topic_arn}}],
        })
        log(f"receipt rule '{name}' created: email for {domain} -> s3://{bucket}/raw/ + notification")
    return verification


def ensure_reader(iam, name, bucket, queue_arn):
    """An IAM user for the app that can only read the queue and read/delete the stored emails."""
    from botocore.exceptions import ClientError

    user = f"{name}-reader"
    try:
        iam.create_user(UserName=user)
        log(f"IAM user {user} created")
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        log(f"IAM user {user} already there")
    iam.put_user_policy(UserName=user, PolicyName="inbound-read", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:DeleteObject"],
             "Resource": f"arn:aws:s3:::{bucket}/raw/*"},
            {"Effect": "Allow", "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
                                           "sqs:ChangeMessageVisibility"], "Resource": queue_arn},
        ]}))
    if iam.list_access_keys(UserName=user)["AccessKeyMetadata"]:
        log("the user already has an access key: no new one (keep the one in your .env)")
        return None
    return iam.create_access_key(UserName=user)["AccessKey"]


def write_env(path, values):
    """Set KEY=value lines in an .env file (replacing existing keys, appending new ones)."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    pending = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in pending:
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def setup(session, *, app, domain, env_file, bucket=None):
    """Create (or find) everything; returns the INBOUND_* values written to env_file."""
    name = f"{app}-inbound"  # bucket prefix, topic, queue, receipt rule, IAM user
    region = session.region_name
    account_id = session.client("sts").get_caller_identity()["Account"]
    bucket = bucket or f"{name}-{account_id}"
    print(f"AWS account {account_id}, region {region}\n")

    ensure_bucket(session.client("s3"), bucket, region, account_id)
    topic_arn, queue_url, queue_arn = ensure_topic_and_queue(session.client("sns"), session.client("sqs"), name,
                                                             account_id)
    verification = ensure_ses(session.client("ses"), name, app, domain, bucket, topic_arn)
    key = ensure_reader(session.client("iam"), name, bucket, queue_arn)

    print("\n=== DNS records to add at your registrar ===")
    print(f"MX     {domain}  ->  10 inbound-smtp.{region}.amazonaws.com")
    print(f'TXT    _amazonses.{domain}  ->  "{verification}"')

    env = {"INBOUND_DOMAIN": domain, "INBOUND_AWS_REGION": region, "INBOUND_QUEUE_URL": queue_url}
    if key:
        env["INBOUND_AWS_ACCESS_KEY_ID"] = key["AccessKeyId"]
        env["INBOUND_AWS_SECRET_ACCESS_KEY"] = key["SecretAccessKey"]
    write_env(env_file, env)
    print(f"\n{', '.join(env)} written to {env_file}" + ("" if key else " (existing access key left as it is)"))
    return env


def main(argv=None):
    parser = argparse.ArgumentParser(prog="inbound-email-aws-setup", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app", required=True, help="short name of the app, e.g. myapp")
    parser.add_argument("--domain", required=True, help="subdomain that receives the emails, e.g. in.myapp.example")
    parser.add_argument("--env-file", required=True,
                        help="the .env file to write the INBOUND_* variables to (the key is never printed)")
    parser.add_argument("--region", default="eu-west-1",
                        help="an SES region that receives email (default eu-west-1)")
    parser.add_argument("--bucket", help="S3 bucket name (default <app>-inbound-<account id>)")
    args = parser.parse_args(argv)

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        sys.exit("boto3 is needed: pip install 'flask-inbound-email[aws]'")
    try:
        setup(boto3.session.Session(region_name=args.region), app=args.app, domain=args.domain,
              env_file=args.env_file, bucket=args.bucket)
    except (ClientError, BotoCoreError) as e:
        sys.exit(f"AWS error: {e}")


if __name__ == "__main__":
    main()
