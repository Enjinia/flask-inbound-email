"""flask inbound poll: run it from a timer (cron, systemd) every minute or two.
flask inbound read FILE.eml --to ADDRESS: try an email locally."""

import click
from flask.cli import AppGroup

inbound_cli = AppGroup("inbound", help="Emails received on the users' own addresses.")


@inbound_cli.command("poll")
@click.option("--batches", default=5, show_default=True, help="Up to 10 emails per batch.")
def poll_command(batches):
    """Process the emails waiting in the Amazon SES queue."""
    from . import ses

    if not ses.is_configured():
        raise click.ClickException("Set INBOUND_DOMAIN and INBOUND_QUEUE_URL first.")
    counts = ses.poll(batches)
    click.echo(" ".join(f"{k}={v}" for k, v in counts.items()))


@inbound_cli.command("read")
@click.argument("eml", type=click.File("rb"))
@click.option("--to", "recipient", required=True, help="The user's address it was sent to.")
def read_command(eml, recipient):
    """Process one .eml file as if it had just arrived (for development: no AWS, every check passed)."""
    from flask import current_app

    result = current_app.extensions["inbound_email"].process(eml.read(), recipients=[recipient], notify=False)
    click.echo(f"{result.outcome}" + (f" ({result.reason})" if result.reason else ""))
