# Using muttlike-imap with openclaw

`muttlike-imap` slots into an [openclaw](https://docs.openclaw.ai) workspace
as the email-search tool for skills like `/inbox`, `/urgent`, `/from`,
`/awaiting-reply`, and `/search`.

## Install

```sh
pip install muttlike-imap
```

If you'd rather keep the package isolated from your system Python:

```sh
pipx install muttlike-imap
```

## Config

For users coming from the openclaw `imap-smtp-email` skill, the package reads
`~/.config/imap-smtp-email/.env` as a fallback when no other config is
present. So if you already have that file configured (`IMAP_HOST`,
`IMAP_USER`, `IMAP_PASS`, `IMAP_TLS`), nothing else is required.

To migrate to the canonical location, copy the file:

```sh
mkdir -p ~/.config/muttlike-imap
cp ~/.config/imap-smtp-email/.env ~/.config/muttlike-imap/config
```

## Wiring it into skills

In `SOUL.md` and any skill that runs the email tool, use the installed CLI:

```
timeout 30 muttlike-imap "<pattern>" --limit 10 --summary
```

The mutt-style pattern syntax is documented in
[`pattern-syntax.md`](pattern-syntax.md). Use `--list-mailboxes` from a skill
that needs to discover non-INBOX folders for archival lookup.

## Common skill recipes

Concrete invocations for typical agent intents. The triage logic
(scoring, ranking, "narrative summary" conventions) lives in your
skill's `SKILL.md`; `muttlike-imap` just retrieves the candidate set.

| Intent | Command |
|---|---|
| Today's unread mail | `muttlike-imap "~U ~d <0d" --limit 1000 --summary` |
| Unread in the last week | `muttlike-imap "~U ~d <7d" --limit 1000 --summary` |
| All correspondence with a person (any role) | `muttlike-imap "~L <name>" --limit 100 --summary` |
| Mail you've sent in the last month | `muttlike-imap "~P ~d <30d" --limit 100 --summary` |
| Owed a reply: unread, unanswered, last 2 weeks | `muttlike-imap "~U !~Q ~d <14d" --limit 1000 --summary` |
| Anything in the last hour | `muttlike-imap "~d <1H" --summary` |
| Mail since a specific date | `muttlike-imap "~d 2025-09-01-" --summary` |
| Large attachments cluttering the inbox | `muttlike-imap "~z >5M" --summary` |
| Look up a message in an archive folder | `muttlike-imap "~f <name> ~s <topic>" --mailbox <folder> --summary` |
| Discover archive folders | `muttlike-imap --list-mailboxes` |

For "all of X" intents (`/inbox today`, `/from <name>`) raise `--limit`
high enough to see the whole window so the skill's filter doesn't get
silently truncated. For one-shot lookups (a specific message, the most
urgent item) the default `--limit 10` is fine.

## Notes

- `~p` and `~P` need the user's email address. The package picks it up from
  `IMAP_USER` automatically; you can override with `--me you@example.com` if
  you want a different identity for those modifiers.
- Any rules you have in your workspace about how the agent should narrate or
  log email-tool invocations are independent of `muttlike-imap` itself; only
  the binary name changes when you migrate from the in-tree script.
