# Security Policy

## Supported versions

This project currently supports the latest code on `main`.

## Reporting a vulnerability

If you find a security issue, please do **not** open a public GitHub issue with exploit details.

Instead:
- contact the repository owner privately through GitHub, or
- open a private security advisory if available for the repository.

Please include:
- a short description of the issue
- reproduction steps
- affected routes/files
- any proof-of-concept details needed to verify the problem
- suggested remediation, if you have one

## Security notes

This app is intended to be run behind normal application security hygiene:
- strong unique credentials
- HTTPS in production
- secure cookie settings enabled in production (`SESSION_COOKIE_SECURE=1`)
- restricted network exposure where appropriate
- regular dependency updates
