# Webhook agent checkout over SSH

For an SSH repository URL, give the Docker agent access to a directory with
an authorized private key and a verified `known_hosts` file:

```bash
python3 scripts/webhook-acquire-agent.py --ssh-dir "$HOME/.ssh"
```

Alternatively, export `BUILDKITE_SSH_DIR` before starting the watcher. Without
either setting, the launcher does not mount SSH files.

For a key with a nonstandard filename, select it explicitly:

```bash
python3 scripts/webhook-acquire-agent.py \
  --ssh-dir "$HOME/.ssh" --ssh-key buildkite_spacecamp
```

Alternatively, export `BUILDKITE_SSH_KEY=buildkite_spacecamp`. The key must be
a file inside the SSH directory. This sets `GIT_SSH_COMMAND` inside the
container with the selected identity, batch mode, and strict host verification.

The directory is mounted read-only at `/root/.ssh`, matching the root user in
`ao-buildkite-acquire-agent:local`. Use a dedicated directory with a repository
deploy key to limit which credentials the job can access. Standard key names
such as `id_ed25519` work automatically; other names need `--ssh-key` or an
`IdentityFile` entry in the directory's `config`, using the path inside the container.
Any SSH configuration must work with Linux OpenSSH, including its file paths.

The launcher sets `BUILDKITE_NO_SSH_KEYSCAN=true` so checkout uses the supplied
host keys without trying to update the read-only directory. Ensure the Git
server's verified host key is already in `known_hosts`. Private key files
should have mode `600` and the directory mode `700`.

This option mounts files, not the host's SSH agent. Passphrase-protected keys
need a separate SSH agent setup for unattended checkout. Images running as a
different user need an appropriate mount target instead of `/root/.ssh`.

Start the watcher before triggering a fresh build. To inspect generated Docker
commands for existing events without launching agents, add
`--once --replay-existing --dry-run`; event filters and webhook verification
still apply.
