# Changelog

All notable changes are documented here. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.2]

### Fixed
- `decode_header` no longer raises `LookupError` on headers with an
  unrecognised charset label such as `unknown-8bit`; falls back to UTF-8.

## [1.0.1]

### Added
- `--completion {bash,zsh,fish}` flag prints a shell-completion script.
  The zsh script offers per-flag tailoring (file paths, `(true false)`
  enumeration for `--imap-tls`, env-var names for `--imap-password-env`,
  live mailbox completion). Bash and fish are simpler but cover flag
  names and the enumeration choices.
- `py.typed` marker (PEP 561) and `Typing :: Typed` classifier so
  static type checkers pick up the package's existing type hints.
- Top-level imports: `from muttlike_imap import search, parse_pattern,
  compile_pattern, list_mailboxes, load_config, CompiledPattern`.

### Changed
- Refined PyPI classifiers: `Development Status :: 5 - Production/Stable`
  (was `4 - Beta`), `Intended Audience :: Developers` and
  `Intended Audience :: System Administrators` (replacing
  `End Users/Desktop`), and added
  `Topic :: Software Development :: Libraries :: Python Modules`.

## [1.0.0]: Initial release

First public release.

### Added
- Mutt-compatible pattern parser supporting AND (juxtaposition), `|` OR,
  `!` NOT, and `(...)` grouping.
- Text modifiers: `~f`, `~t`, `~s`, `~b`, `~B`, `~c`, `~C`, `~L`, `~e`, `~i`,
  `~y`, `~h`, `~x`.
- Flag modifiers: `~A`, `~U`, `~N`, `~R`, `~O`, `~F`, `~D`, `~Q`, `~p`, `~P`.
- Date modifiers `~d` and `~r` with the full mutt DATERANGE grammar:
  relative (`<Nu`, `>Nu`, `=Nu`), absolute (`D/M/Y` and ISO `YYYY-MM-DD`),
  ranges, half-open ranges, and error margins (`*Nu`). Sub-day units
  (`H`/`M`/`S`) get day-rounded server-side filtering plus a Python
  post-filter against `Date:` (or `INTERNALDATE` for `~r`), recovering
  precise sub-day windows like `~d <30M` for "last 30 minutes".
- Size modifier `~z` with `<`, `>`, range, and inclusive/exclusive forms.
- `--list-mailboxes` for folder discovery.
- Modified UTF-7 (RFC 3501 §5.1.3) encoding/decoding for non-ASCII folder
  names like `Éléments envoyés`.
- ASCII diacritic folding so `~f Müller` matches both UTF-8 and ASCII forms.
- Layered config: CLI flags > `IMAPQUERY_*` env vars > `$IMAPQUERY_CONFIG` >
  `~/.config/muttlike-imap/config` > legacy `~/.config/imap-smtp-email/.env`.
- `--imap-password-cmd` flag and `IMAP_PASS_CMD` config key for fetching the
  password from `pass`, `gpg`, `secret-tool`, the macOS Keychain, etc.,
  without storing it on disk or exporting it through the environment.
- `--imap-password-env` flag for picking up an already-set environment
  variable.
- Library API: `parse_pattern`, `search`, `list_mailboxes`, `load_config`.

[Unreleased]: https://github.com/PierreSenellart/muttlike-imap/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.0.2
[1.0.1]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.0.1
[1.0.0]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.0.0
