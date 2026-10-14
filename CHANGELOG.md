# Changelog

All notable changes are listed here. The project follows [Semantic Versioning](https://semver.org/);
until 1.0, minor versions may change the API.

## [0.1.0] - 2026-10-14

First release.

### Added

- `InboundEmail` Flask extension: user lookup by address token, trusted senders, handler,
  notifier, recorder, user context and time zone callbacks.
- `read_email()`: text, forwarded message (Gmail, Apple Mail, Outlook; English and Italian),
  attachments (PDFs, pictures rotated and scaled), calendar invites.
- `rejection()`: SPF/DKIM/DMARC, spam, virus, auto-replies, bounces.
- Amazon SES delivery through S3 + SNS + SQS: `flask inbound poll`.
- `flask inbound read FILE.eml --to ADDRESS` to try emails locally.
- `inbound-email-aws-setup` command to create the AWS resources.
