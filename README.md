# muttlike-imap

[![CI](https://github.com/PierreSenellart/muttlike-imap/actions/workflows/ci.yml/badge.svg)](https://github.com/PierreSenellart/muttlike-imap/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/muttlike-imap?style=flat)](https://pypi.org/project/muttlike-imap/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Search IMAP mailboxes from the command line using mutt-style patterns.

```console
$ muttlike-imap "~f alice ~U" --summary
1 result(s):

UID:1264 | From:Alice <alice@example.com> | Date:Tue, 21 Apr 2026 15:02:35 +0000
Subject:Re: project update
Preview:Hi, here is the latest version of the document…
```

## Why

Mutt has a great pattern language for finding messages (`~f`, `~s`, `~d <7d`,
`!~U`, `(A | B) C`, …). IMAP servers can do most of the same searches, but the
wire protocol is verbose and the bindings in `imaplib` are awkward to compose.
`muttlike-imap` is a small CLI that translates mutt patterns into IMAP `SEARCH`
criteria and prints the matches as JSON or a human-readable summary.

It's intentionally small and scriptable – useful as a building block for
notification scripts, AI assistants, cron jobs, or one-off lookups.

## Install

```sh
pip install muttlike-imap
```

Requires Python 3.9+. No third-party dependencies.

## Configure

`muttlike-imap` looks for connection settings in this order (highest priority
first):

1. CLI flags: `--imap-host`, `--imap-port`, `--imap-user`,
   `--imap-password-cmd`, `--imap-password-env`, `--imap-tls`.
2. Environment variables: `IMAPQUERY_HOST`, `IMAPQUERY_PORT`, `IMAPQUERY_USER`,
   `IMAPQUERY_PASS`, `IMAPQUERY_TLS`.
3. The file pointed to by `$IMAPQUERY_CONFIG`, if set.
4. `$XDG_CONFIG_HOME/muttlike-imap/config` (defaults to
   `~/.config/muttlike-imap/config`).
5. `~/.config/imap-smtp-email/.env`: kept as a fallback for users who already
   have this file from the
   [openclaw imap-smtp-email skill](https://github.com/openclaw/imap-smtp-email).
   See [`docs/openclaw.md`](docs/openclaw.md) for wiring `muttlike-imap`
   into an openclaw workspace.

A minimal config file:

```ini
IMAP_HOST=imap.example.com
IMAP_PORT=993
IMAP_USER=you@example.com
IMAP_PASS_CMD=pass email/imap.example.com
IMAP_TLS=true
```

The keys can be written with the `IMAP_` prefix (shown above, matches mutt and
offlineimap conventions), with the `IMAPQUERY_` prefix, or with no prefix at
all (`HOST=…`); pick whichever you prefer.

### Passwords

There are three ways to give `muttlike-imap` your password, in increasing
order of how much they leak:

- **`IMAP_PASS_CMD=<shell command>`** (recommended). The command is run on
  every invocation; the first line of stdout is used as the password. Pair
  with [`pass`](https://www.passwordstore.org/),
  [`secret-tool`](https://wiki.gnome.org/Projects/Libsecret),
  macOS Keychain (`security find-generic-password -w`), or anything else
  that prints a secret to stdout. The password lives only in the
  `muttlike-imap` process memory and is never written to disk or to the
  environment.

  Equivalent CLI flag: `--imap-password-cmd "pass email/imap.example.com"`.

- **`--imap-password-env MY_VAR`**. Reads the password from a named
  environment variable. Useful when you already have a secret in a variable
  (e.g. via `direnv` or a parent process) and don't want to re-export it.
  Caveat: env vars leak through `/proc/<pid>/environ`, child processes,
  `ps eww`, and crash dumps.

- **`IMAP_PASS=<plaintext>`** in the config file. Simplest, least secure;
  fine for throwaway accounts and local-only setups. Make sure the file is
  `chmod 600`.

See [`docs/secrets.md`](docs/secrets.md) for worked examples with `pass`,
raw `gpg`, and OS keyrings.

## Examples

By default the search runs against `INBOX` and returns the 10 most recent
matches as JSON. Pass `--mailbox <name>` to search elsewhere, `--limit N`
to widen or narrow the result count, and `--summary` for human-readable
output.

```sh
# Unread mail, default INBOX, summary view
muttlike-imap "~U" --summary

# All mail from Alice in the last week
muttlike-imap "~f alice ~d <7d" --summary

# Archived correspondence with Bob about a specific topic
muttlike-imap '~L bob ~s "project x"' --mailbox Archive --summary

# Anything addressed to me, last 30 days, that I haven't replied to
muttlike-imap '~p ~d <30d !~Q' --summary

# Date range, ISO format
muttlike-imap '~d 2025-09-01-2025-12-31 ~f committee' --summary

# JSON for piping into jq
muttlike-imap "~U" | jq '.[] | {uid, subject, from}'

# Discover available folders
muttlike-imap --list-mailboxes
```

## Pattern syntax

`A B` is AND (juxtaposition), `A | B` is OR, `!A` is NOT, and `(...)` groups.

| Modifier | Meaning |
|----------|---------|
| `~f <text>` | From contains |
| `~t <text>` | To contains |
| `~s <text>` | Subject contains |
| `~b <text>` | Body contains |
| `~B <text>` | Body or any header contains |
| `~c <text>` | Cc contains |
| `~C <text>` | To, Cc, or Bcc contains |
| `~L <text>` | Any participant (From/To/Cc) |
| `~e <text>` | Sender header |
| `~i <text>` | Message-ID |
| `~y <text>` | X-Label |
| `~h "Name: text"` | Arbitrary header |
| `~x <text>` | References / In-Reply-To |
| `~A` | All messages |
| `~U` / `~N` | Unread / new |
| `~R` / `~O` | Read / old |
| `~F` | Flagged |
| `~D` | Deleted |
| `~Q` | Replied (answered) |
| `~p` | Addressed to you |
| `~P` | From you |
| `~d <Nu` | Date: header newer than N units (units `y m w d H M S`) |
| `~d >Nu` | Date: header older than N units |
| `~d =Nu` | Exactly N units old |
| `~d DATE` | On a specific date (`YYYY-MM-DD` or `D/M/Y`) |
| `~d -DATE` / `~d DATE-` | Half-open range (before / since) |
| `~d DATE-DATE` | Range |
| `~d DATE*Nu` | Range of ±N units around DATE |
| `~r DATERANGE` | Same grammar but on received date (INTERNALDATE) |
| `~z <N` / `~z >N` / `~z N-M` | Size in bytes (suffix `K`/`M`) |

See [`docs/pattern-syntax.md`](docs/pattern-syntax.md) for the full reference.

### Limitations vs mutt

* **No regex.** IMAP `SEARCH` is substring-only, so all text matches are
  literal. Anchors and character classes are not honored.
* **No mutt-runtime modifiers.** `~T` (tagged), `~v` (collapsed thread),
  `~m` (message-number), `~n` (score), `~$`, `~#`, `~(...)` (thread patterns),
  PGP modifiers: all error out instead of silently misbehaving.

`H`/`M`/`S` offsets are exact despite IMAP's day granularity: the server
narrows to the smallest whole-day window containing the precise range,
then a client-side post-filter trims the fetched candidates by their
`Date:` header (or `INTERNALDATE` for `~r`). `~d <30M` really does mean
"last 30 minutes". The post-filter is suppressed when the sub-day
modifier sits inside an `OR`, `!`, or paren-grouped disjunction, since
lifting its predicate to a top-level filter would change the pattern's
meaning.

## Library use

```python
from muttlike_imap.parser import parse_pattern
from muttlike_imap.client import search
from muttlike_imap.config import load_config

config = load_config()  # or pass in your own dict
results = search(config, "~f alice ~U", limit=10, mailbox="INBOX")
for r in results:
    print(r["subject"])

# Or just use the parser:
parse_pattern("(~f a | ~f b) ~U")
# → 'OR (FROM "a") (FROM "b") UNSEEN'
```

## License

MIT: see [LICENSE](LICENSE).
