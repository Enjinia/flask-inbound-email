# Contributing

Thanks for helping! Bug reports, forwarded-email formats we do not recognise yet and pull
requests are all welcome.

## Reporting a bug

Open an issue with what you expected and what happened. If an email is read wrongly, attach a
**made-up** `.eml` that shows the problem: never share real emails, they contain other people's data.

## Working on the code

```sh
git clone https://github.com/enjinia/flask-inbound-email
cd flask-inbound-email
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
```

- Every change comes with a test. The tests use invented emails, built in `tests/conftest.py`.
- Keep the safety rules: rejected emails are never answered, and only the user's own addresses are trusted.
- Note your change under "Unreleased" in `CHANGELOG.md`.

## New languages for forwarded emails

`forwarded.py` recognises the forward markers and header names of each mail client. To add a
language, add its markers and header names, with a test built from a real client's output
(rewritten with made-up names and addresses).

By contributing you agree that your contribution is licensed under the [Apache-2.0](LICENSE) license.
