# Changelog

All notable changes are documented here. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.2.0]

### Added
- `attachments` field in each record: number, file name, content type
  and size of each attachment; also shown by `--summary`.
- `--save-attachments DIR` (with `--uid`, optionally `--attachment` and
  `--uidvalidity`) saves attachments into `DIR` without ever
  overwriting a file, or to standard output with `DIR` `-`.
- `--search QUERY` finds messages across all folders with an external
  search engine (notmuch, possibly over `ssh`, or any command printing
  Message-IDs and file paths), then locates them over IMAP; an optional
  mutt pattern filters the matches. Configured with `SEARCH_CMD`,
  `SEARCH_ENGINE`, `SEARCH_LAYOUT` and `SEARCH_ROOT`.
- `mailbox` field in each record: the folder the message is in.
- Library API: `search_engine`, `move_messages`, `save_attachments`,
  `attachments` and `select_attachments` are exported by the package.
- `--move-to FOLDER` (with `--uid`, optionally `--uidvalidity` and
  `--dry-run`) moves messages to another folder without any risk of
  losing them: atomic `UID MOVE` when the server supports it, otherwise
  copy, verify the copy, then `UID EXPUNGE` of that UID only; the
  original is kept whenever the copy cannot be confirmed.
- `uidvalidity` field in each record: the folder's `UIDVALIDITY`, which
  qualifies the UID (if it changes, the server has renumbered the
  folder). Empty string when the server does not report it.

### Fixed
- The `uid` field and `--uid` now really are IMAP UIDs. Earlier
  versions used plain `SEARCH`/`FETCH`, which work on message sequence
  numbers: these shift whenever a message above them leaves the folder,
  so a number read from one search could designate a different message
  a few minutes later. Searches and fetches now use `UID SEARCH` and
  `UID FETCH`. The numbers printed will differ from those of earlier
  versions.

## [1.1.3]

### Added
- `in_reply_to` and `references` fields in each record: the message's
  `In-Reply-To` and `References` headers, whitespace-collapsed (folded
  lines joined, runs of whitespace reduced to single spaces). Together
  with `message_id` these enable accurate thread reconstruction by
  downstream tools. Empty string when the header is absent.

## [1.1.2]

### Added
- `message_id` field in each record: the message's `Message-ID`
  header (stripped of surrounding whitespace), giving a stable locator
  for threading or jumping to a specific message. Empty string when the
  header is absent.

## [1.1.1]

### Added
- Single-quoted modifier values: `~f 'Jane Doe'` now parses the
  same as `~f "Jane Doe"`. Mutt itself only accepts double quotes,
  but single quotes are common in shell contexts and LLM-generated
  patterns. The opening quote chooses the terminator, so a mid-bareword
  quote stays literal (`~f O'Brien` still works).
- Backslash-escapes for the active quote and for the backslash itself
  inside quoted values: `~s 'outils d\'IA'` and `~s "say \"hi\""` now
  parse as expected. Other backslash sequences (`\n`, `\bar`, etc.) are
  left untouched so existing literal-backslash patterns are unaffected.

## [1.1.0]

### Added
- `--body` flag: include full plain-text body in output (JSON `body` key;
  shown in place of `Preview:` in `--summary` output).
- `--uid UID [UID ...]` flag: fetch specific messages by UID without searching.
  Composable with `--body` and `--mailbox`.
- Library: `fetch_by_uids()` function, now exported from the top-level package.

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

[Unreleased]: https://github.com/PierreSenellart/muttlike-imap/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.2.0
[1.1.3]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.1.3
[1.1.2]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.1.2
[1.1.1]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.1.1
[1.1.0]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.1.0
[1.0.2]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.0.2
[1.0.1]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.0.1
[1.0.0]: https://github.com/PierreSenellart/muttlike-imap/releases/tag/v1.0.0
