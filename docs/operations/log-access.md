# Log access

Every component writes its logs as files on the VPS. They can be read remotely
through one restricted, read-only SSH account.

## How it fits together

| Piece | Where |
|---|---|
| Writers | `factorlab.core.logging` writes JSON lines to stderr and to `/var/log/factorlab/<component>/<service>.jsonl` (`FACTORLAB_LOG_DIR`). nginx (web) writes `access.jsonl` and `error.log`. The political cron wrapper writes `cron.log`. |
| Rotation | `deploy/logrotate/factorlab`: hourly check, daily or at 256 MB, compressed, 30 days. Docker's json-file driver is also capped (20 MB × 5) as a backstop. journald is limited to 1 GB or 30 days. |
| Ownership | Directories are `<container uid>:factorlab-logs 2750`; files are readable by the `factorlab-logs` group (GID 10002). The deployer fixes ownership on each release. |
| Reader | `deploy/host/factorlab_log_reader.py`, installed as `/usr/local/bin/factorlab-log-reader`. It is stdlib Python 3.10+ and only reads. |
| Account | `factorlab-logs` (UID 10002). Its only group is `factorlab-logs`; it has no docker access and no sudo. The sshd drop-in `deploy/ssh/60-factorlab-logs.conf` forces the reader as its only command, with no TTY and no forwarding. Its keys live in a root-owned file. |
| Client | `tools/read_logs.py`, and the `factorlab-logs` Claude skill (`.claude/skills/factorlab-logs`). |

`prepare-host.sh` installs the account, the reader and the sshd drop-in. It runs as
part of every platform release. It checks the sshd configuration with `sshd -t`
before reloading; if sshd rejects it, it removes the drop-in, so SSH stays as it was.

## One-time setup

1. **Key (workstation).** Create a key used only for this account:

   ```powershell
   ssh-keygen -t ed25519 -f $HOME\.ssh\factorlab_logs -C factorlab-logs
   ```

2. **Authorize it (VPS, as an administrator).** Append the public key with the
   restrictions. The file is root-owned, so the account cannot change it:

   ```bash
   echo 'restrict,command="/usr/local/bin/factorlab-log-reader" ssh-ed25519 AAAA... factorlab-logs' \
     | sudo tee -a /etc/factorlab/log-reader/authorized_keys
   ```

   If `/etc/ssh/sshd_config` limits logins with `AllowUsers` or `AllowGroups`, add
   `factorlab-logs` there too, then run `sudo sshd -t && sudo systemctl reload ssh`.

3. **Client settings (workstation `.env` or the environment).** Leave the host key
   pinned: the VPS must already be in `~/.ssh/known_hosts`, verified out of band as
   for the deploy key.

   ```dotenv
   FACTORLAB_LOGS_SSH_HOST=<vps host>          # defaults to CLICKHOUSE_SSH_HOST
   FACTORLAB_LOGS_SSH_KEY_PATH=C:\Users\<you>\.ssh\factorlab_logs
   # FACTORLAB_LOGS_SSH_PORT=22  FACTORLAB_LOGS_SSH_USER=factorlab-logs
   ```

4. **Check it:** `uv run python tools/read_logs.py list`.

## Use

```bash
uv run python tools/read_logs.py list
uv run python tools/read_logs.py errors --since 24h
uv run python tools/read_logs.py tail --component ingest-us --since 2h --level WARNING
uv run python tools/read_logs.py run --run-id <meta.ingestion_runs.run_id>
uv run python tools/read_logs.py --local ./copied-logs tail --component api   # no SSH
```

The host enforces these limits: windows up to 30 days, at most 200 records, and at
most 64 KiB per response. Secrets are redacted again when they are read. The client
prints at most 12 KiB (`--max-bytes` raises this to 64 KiB).

## Troubleshooting

| Symptom | Cause |
|---|---|
| `ssh failed ... Permission denied (publickey)` | The key is not in `/etc/factorlab/log-reader/authorized_keys`, the wrong key path is set, or `AllowUsers` excludes the account. |
| `Host key verification failed` | The VPS is not in `known_hosts`. Add the verified host key; never disable checking. |
| `no logs for component X` | Nothing has written there yet. The component may still be on the monolith image, which only logs to Docker, or it may not have run since the platform release. |
| A container crashes before it logs anything | Its output is in Docker's log only. An operator can read it with `sudo factorlab-compose logs --tail 200 <service>`. The reader account deliberately cannot. |
| `sshd rejected ...` during a platform release | The drop-in did not validate on that OpenSSH version. It was removed, and SSH is unchanged. Fix the drop-in and release the platform again. |
