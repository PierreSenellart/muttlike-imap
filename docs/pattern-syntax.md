# Pattern syntax reference

`muttlike-imap` accepts mutt-style search patterns and translates them into
IMAP `SEARCH` criteria. This document is the full reference.

## Operators

| Form | Meaning |
|------|---------|
| `A B` | AND (juxtaposition) |
| `A \| B` | OR |
| `!A` | NOT |
| `(...)` | Grouping |

`AND` binds tighter than `OR`, so `~f a ~U \| ~s x` parses as
`(~f a AND ~U) OR ~s x`. Use parentheses to override.

## Quoting

A modifier value may be:

- A bareword: `~f alice` (terminated by whitespace, `(`, `)`, or `\|`).
- A quoted string: `~s "hello world"`.

Backslash-escapes inside quotes are not supported; use a different quoting
mechanism in your shell if you need a literal `"` inside the value.

## Headers and addresses

| Modifier | IMAP equivalent | Notes |
|----------|----------------|-------|
| `~f <text>` | `FROM` | From header substring |
| `~t <text>` | `TO` | |
| `~s <text>` | `SUBJECT` | |
| `~c <text>` | `CC` | |
| `~C <text>` | `OR (OR TO CC) BCC` | To, Cc, or Bcc |
| `~L <text>` | `OR (FROM TO) CC` | Any participant |
| `~e <text>` | `HEADER Sender …` | |
| `~i <text>` | `HEADER Message-ID …` | |
| `~y <text>` | `HEADER X-Label …` | |
| `~h "Name: text"` | `HEADER Name text` | Arbitrary header |
| `~x <text>` | `OR HEADER References HEADER In-Reply-To` | |

### Diacritic folding

If the value contains non-ASCII characters, the parser ORs the original UTF-8
form with an ASCII-folded form. So `~f Müller` matches both `Müller` and
`Muller` in one round trip.

## Body and full-text

| Modifier | IMAP equivalent | Notes |
|----------|----------------|-------|
| `~b <text>` | `BODY` | Body only |
| `~B <text>` | `TEXT` | Body and any header (IMAP `TEXT`) |

## Flags

| Modifier | IMAP flag | Mutt meaning |
|----------|-----------|---------------|
| `~A` | `ALL` | All messages |
| `~U` / `~N` | `UNSEEN` | Unread / new |
| `~R` / `~O` | `SEEN` | Read / old |
| `~F` | `FLAGGED` | Starred / flagged |
| `~D` | `DELETED` | Marked for deletion |
| `~Q` | `ANSWERED` | Replied to |

## Addressing

| Modifier | Meaning |
|----------|---------|
| `~p` | Addressed to you (uses `IMAP_USER` or `--me`) |
| `~P` | From you |

## Dates

`~d` matches the `Date:` header (IMAP `SENTSINCE`/`SENTBEFORE`/`SENTON`).
`~r` matches the IMAP server's `INTERNALDATE` (`SINCE`/`BEFORE`/`ON`).

### Relative offsets

| Form | Meaning |
|------|---------|
| `~d <Nu` | Newer than N units ago |
| `~d >Nu` | Older than N units ago |
| `~d =Nu` | Exactly N units old |
| `~d Nu` | Legacy alias for `~d <Nu` |

Units: `y` (years, ≈365d), `m` (months, ≈30d), `w` (weeks), `d` (days),
`H` (hours), `M` (minutes), `S` (seconds).

IMAP `SEARCH` is day-granular, but `muttlike-imap` recovers sub-day
precision with a client-side post-filter. When you give an `H`/`M`/`S`
offset, the tool sends the day-rounded version to the server (which
narrows to the smallest whole-day window that contains the precise
range: today's mail for offsets under 24 hours, the last few days for
larger `H` offsets), then reparses each candidate's `Date:` header (or
`INTERNALDATE` for `~r`) and drops messages that don't actually fall
within the precise window. `~d <30M` therefore really does return the
last 30 minutes of mail, not all of today's; `~d <72H` returns
exactly the last 72 hours, not just the last 3 calendar days.

The post-filter is only applied when the sub-day modifier appears in a
top-level conjunctive position. Inside an `OR` arm, a negation, or a
parenthesized disjunction the modifier falls back to day granularity,
because lifting its predicate to a top-level filter would change the
pattern's logical meaning.

### Absolute dates

| Form | Meaning |
|------|---------|
| `~d DATE` | On that date |
| `~d -DATE` | Before that date |
| `~d DATE-` | Since that date |
| `~d DATE-DATE` | Within that range (inclusive of endpoints) |
| `~d DATE*Nu` | ±N units around DATE |
| `~d DATE±Nu` | (same as `*Nu`) |

`DATE` accepts:

- ISO `YYYY-MM-DD` (recommended – unambiguous).
- Mutt-style `D/M/Y` or `D/M/YY` (two-digit years <70 expand to 20xx,
  otherwise to 19xx).
- Mutt-style `D/M` (year defaults to current).

## Size

| Form | Meaning |
|------|---------|
| `~z <N` | Smaller than N bytes |
| `~z >N` | Larger than N bytes |
| `~z N-M` | Between N and M (inclusive) |
| `~z -N` | Up to N (inclusive) |
| `~z N-` | At least N (inclusive) |
| `~z N` | Approximately N (widened to a one-byte slot) |

`N` and `M` accept a `K` (KiB = 1024) or `M` (MiB = 1024 × 1024) suffix.

## What's missing vs mutt

These mutt features have no IMAP equivalent and raise a clear `ValueError`
when used:

- `~T`: tagged (mutt-local)
- `~v`: collapsed-thread membership (mutt-local)
- `~m N-M`: message-number range (mutt-local)
- `~n N-M`: score range (mutt-local)
- `~$`, `~#`: unreferenced / broken threads
- `~( PATTERN )`, `~<( PATTERN )`, `~>( PATTERN )`: thread patterns
- `~g`, `~G`, `~k`, `~V`: PGP-related

And these structural differences are intentional, not bugs:

- **Substring matching, not regex.** IMAP `SEARCH` doesn't expose regex; the
  values you pass are sent as literal substring criteria.
