# Security Policy

## Supported versions

Plumbline is a hackathon prototype (`0.0.x`). Only the latest commit on `main` is supported.

## Reporting a vulnerability

Please report it privately through GitHub: **Security → Report a vulnerability** on this repository
(private vulnerability reporting). Do not open a public issue for a vulnerability.

We aim to acknowledge a report within 7 days.

## Scope notes

- Plumbline only reads public chain metadata. Its optional canary command signs transactions only with a
  key the operator supplies from a file outside the repository; no key, seed or `.env` is ever committed.
- CI runs fully offline: no secrets, no funded key and no live RPC writes.
