# Contributing to muttlike-imap

Thank you for your interest in contributing to muttlike-imap!

## Reporting bugs and requesting features

Please use the [GitHub issue
tracker](https://github.com/PierreSenellart/muttlike-imap/issues). Two
issue templates are provided and will auto-fill when you open a new
issue: a bug-report form (asking for `muttlike-imap --version`, your
Python version, OS, and a reproducer) and a feature-request form. For
security vulnerabilities, please use the [private security
advisory](https://github.com/PierreSenellart/muttlike-imap/security/advisories/new)
flow instead of a public issue (see [SECURITY.md](SECURITY.md)).

## Development setup

```sh
git clone https://github.com/PierreSenellart/muttlike-imap
cd muttlike-imap
pip install -e ".[dev]"
```

That installs `ruff` (lint + format) and `pytest` (with `pytest-cov`)
into your current Python. The package itself has no runtime
dependencies.

## Running the tests and linters

```sh
pytest                        # full suite, ~240 tests, no IMAP server needed
pytest --cov                  # with coverage
ruff check .                  # lint
ruff format --check .         # check formatting
ruff format .                 # apply formatting
```

CI runs the same commands across Python 3.9 through 3.13.

## Submitting a pull request

1. Fork the repository and create a branch from `main`.
2. Make your changes. Add or update tests for any code change: the bar
   is "the test would have caught this bug" or "the test demonstrates
   the new behaviour".
3. Ensure `pytest`, `ruff check .`, and `ruff format --check .` pass.
4. Update `CHANGELOG.md` under `[Unreleased]` with a one-line summary,
   and update `README.md` / `docs/pattern-syntax.md` / `docs/secrets.md`
   if you change user-visible behaviour.
5. Open a pull request against `main` with a clear description of what
   the change does and why.

## License

By contributing, you agree that your contributions will be licensed
under the [MIT License](LICENSE).
