# Contributing

Thanks for taking a look. Plumbline is a small Python CLI; contributions are welcome as issues or pull requests.

## Set up

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Before you open a pull request

```bash
ruff check .          # lint
python -m pytest -q   # offline tests (live tests are opt-in: pytest -m live)
```

CI runs the same two steps plus a package build, CodeQL, gitleaks and pip-audit. None of them needs a key
or a live chain write.

## Guidelines

- Keep changes small and focused, with a test for new behaviour.
- Never commit secrets, seeds or `.env` files.
- Chain measurements must name their profile (`v3`, `mainnet-1002`, `interim-sdk`) and be reproducible
  from a command in the README.
