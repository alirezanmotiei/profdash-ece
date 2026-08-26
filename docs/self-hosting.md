# Self-hosting

## Running persistently

### systemd (user service, Linux)

`~/.config/systemd/user/profdash.service`:

```ini
[Unit]
Description=profdash dashboard
After=network-online.target

[Service]
WorkingDirectory=/home/YOU/my-outreach
ExecStart=%h/.local/bin/prof serve --host 0.0.0.0 --port 8000 --no-open
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now profdash.service
loginctl enable-linger          # keep it running after logout
journalctl --user -u profdash -f
```

> systemd gotcha: `WorkingDirectory=` must NOT be quoted even when the
> path contains spaces; `Environment=VAR="..."` lines ARE quoted.

### Workers as timers

`~/.config/systemd/user/profdash-tasks.{service,timer}`:

```ini
# .service
[Unit]
Description=profdash agent task worker

[Service]
Type=oneshot
WorkingDirectory=/home/YOU/my-outreach
ExecStart=%h/.local/bin/prof worker tasks --batch-size 5

# .timer
[Unit]
Description=Run profdash task worker

[Timer]
OnUnitActiveSec=10min
Persistent=true

[Install]
WantedBy=timers.target
```

Same pattern for Gmail scanning (`prof worker gmail-scan`,
`OnUnitActiveSec=30min`) — enable that timer only after `prof setup-gmail`.

### cron alternative

```cron
*/10 * * * * cd /home/YOU/my-outreach && prof worker tasks --batch-size 5 >> worker.log 2>&1
*/30 * * * * cd /home/YOU/my-outreach && prof worker gmail-scan >> gmail.log 2>&1
0 4 * * *   cd /home/YOU/my-outreach && prof backup >> backup.log 2>&1
```

## Security model (read this)

- The dashboard has **no authentication**. It is meant to run on
  `127.0.0.1` or your personal LAN. Do not port-forward it to the
  internet. If you must expose it, put it behind a tunnel with auth
  (e.g. Cloudflare Access, Tailscale, a reverse proxy with basic auth).
- The Gmail token sits in `~/.config/profdash/` — file permissions are
  yours to keep tight.
- All writes are local SQLite. `prof backup` nightly is cheap insurance.

## LAN access

`prof serve --host 0.0.0.0` binds all interfaces; open the port if you
run a firewall:

```bash
sudo ufw allow 8000/tcp    # example
```

## Multiple machines

The state is one SQLite file plus the token file. To move machines:
`prof backup`, copy the `.sqlite.gz`, restore by unzipping into
`data/profdash.sqlite` on the new machine.
