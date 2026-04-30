# Storing the IMAP password securely

`muttlike-imap` supports a "password command" mechanism (`IMAP_PASS_CMD` in
the config file, or `--imap-password-cmd` on the CLI). The tool runs the
command, reads the first line of stdout, and uses that as the password. The
secret travels through a pipe directly into the process; it never enters the
environment, never appears in argv, never persists on disk.

This is the same pattern used by `mutt` (`set imap_pass=` with backticks),
`mbsync` (`PassCmd`), `msmtp` (`passwordeval`), and `git-credential-helper`.

## With `pass` (recommended)

[`pass`](https://www.passwordstore.org/) is the standard Unix password
manager: a thin wrapper over `gpg` that handles file naming, search, and
clipboard integration.

One-time setup:

```sh
# Pick (or generate) the gpg key you want to encrypt with
gpg --list-secret-keys --keyid-format=long      # see what you have
gpg --full-generate-key                         # …or make one (RSA 4096, no expiry)

# Initialize the password store with that key's UID
pass init you@example.com

# Store the IMAP password
pass insert email/imap.example.com              # prompts twice
```

Then in `~/.config/muttlike-imap/config`:

```ini
IMAP_HOST=imap.example.com
IMAP_USER=you@example.com
IMAP_PASS_CMD=pass email/imap.example.com
IMAP_TLS=true
```

The first call after a reboot prompts for your gpg passphrase via
`pinentry`; `gpg-agent` caches it for `default-cache-ttl` seconds (default
600) so subsequent calls run silently. To bump the TTL, edit
`~/.gnupg/gpg-agent.conf`:

```
default-cache-ttl 28800
max-cache-ttl 28800
```

Then `gpgconf --kill gpg-agent` to apply.

## With raw `gpg` (no `pass`)

If you don't want another tool, use `gpg` directly:

```sh
echo -n "your-imap-password" | gpg --encrypt --armor \
  --recipient you@example.com -o ~/.config/muttlike-imap/imap.pass.asc
```

```ini
IMAP_PASS_CMD=gpg --quiet --decrypt ~/.config/muttlike-imap/imap.pass.asc
```

The advantage of `pass` over this is just convenience: `pass` handles file
naming and the multi-account layout for you.

## With a system keyring

### Linux (GNOME / KDE / freedesktop)

`secret-tool` ships with libsecret. Store once:

```sh
secret-tool store --label="IMAP" service imap user you@example.com
```

Then in the config:

```ini
IMAP_PASS_CMD=secret-tool lookup service imap user you@example.com
```

### macOS

```sh
security add-generic-password -a you@example.com -s imap -w
# prompts for the password (or pass it inline; check the man page)
```

```ini
IMAP_PASS_CMD=security find-generic-password -a you@example.com -s imap -w
```

## A note on shell quoting in the config file

The value of `IMAP_PASS_CMD` is run via `/bin/sh -c`, so shell metacharacters
work as expected. You can pipe and chain:

```ini
IMAP_PASS_CMD=gpg --decrypt ~/secret.gpg | head -1
```

The config-file parser strips at most one layer of *matching* surrounding
quotes (both ends must agree on the quote character). Write the value
plainly: don't wrap it like a shell argument. A common gotcha is wrapping a
command that already contains nested quotes:

```ini
# WRONG: extra outer single quotes get stripped, leaving the inner 'set imap'
# unbalanced when the shell runs it.
IMAP_PASS_CMD='gpg --decrypt ~/secret.gpg | grep 'set imap''

# Right: write the command as you would type it at a shell prompt.
IMAP_PASS_CMD=gpg --decrypt ~/secret.gpg | grep 'set imap'
```

A trailing closing quote that's part of your command (such as the closing
``"`` of ``sed "s/x/y/"``) is left alone because there's no matching opening
quote at the start of the line.
