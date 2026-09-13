# ASP PWPanel

[Bahasa Indonesia](README.id.md)

ASP PWPanel is a lightweight web administration and player portal for a
self-hosted Perfect World 1.5.5 server. It uses Python's standard library and
MariaDB, and is designed to run beside the existing PW155 services on Ubuntu.

## Features

- Player registration, sign-in, account panel, rankings, news, and downloads
- Admin account and GM management
- Boutique cash queue and character synchronization
- Core service monitoring and map controls
- Draft-based Boutique, NPC spawn, equipment, merchant, and recipe editors
- ASP CPW Patch Manager integration
- Admin-triggered database backups with protected download access

## What is not included

This repository contains the panel's original source code only. It does **not**
contain a Perfect World client/server, game binaries, game data, patch payloads,
credentials, backups, or third-party configuration schemas. See
[NOTICE.md](NOTICE.md).

## Requirements

- Ubuntu Server 20.04 or a compatible Linux host with `systemd`
- Python 3.8 or newer
- MariaDB client/server and an existing compatible PW155 database
- Existing PW155 services and legally obtained game data
- A dedicated, least-privileged database account for the web panel
- Optional: ASP CPW for client patch publishing

No third-party Python packages are required.

## Quick start for development

1. Clone the repository.
2. Copy `.env.example` values into your shell or service environment.
3. Create `/etc/pw155-web/db.cnf` with a least-privileged MariaDB account.
4. Provide the required game data paths through the `PW155_*` variables.
5. Generate a unique CSRF secret of at least 32 characters.
6. Run the panel:

```bash
export PW155_WEB_CSRF_SECRET="replace-this-with-a-unique-random-secret"
export PW155_WEB_DB_CONFIG="/etc/pw155-web/db.cnf"
python3 app.py
```

Open `http://127.0.0.1:8080`. Pages that query accounts or game data require a
configured database and compatible files.

For production, run the panel as an unprivileged `systemd` service behind an
HTTPS reverse proxy. Do not expose Python's built-in HTTP server directly to
the public internet. Restrict the database file to its service account and
keep all worker/control directories outside the web root.

## PWKU installation and updates

If this panel is installed as part of the existing PWKU environment:

1. Start the Ubuntu VM/VPS and verify SSH access.
2. Open **ASP CPW Desktop** on Windows.
3. Run **Install / Update Server** once after updating the panel package. This
   installs or refreshes the web, monitor, map-control, and backup workers.
4. Open the Admin Panel and verify service status.
5. For every client update, use **Create Update -> Preview -> Publish -> Verify**
   in ASP CPW. Reinstalling the panel is not required for ordinary patch files.

## Database backup from Admin Panel

1. Sign in with an administrator account.
2. Open **Database Backup**.
3. Select **Create Backup** and wait for the worker to finish.
4. Download the generated archive from the protected backup list.
5. Store an additional copy outside the VM/VPS.

The web process never runs database dump commands as root. It places an
allowlisted request for a separate system worker, which creates and packages
the backup. Default retention is 14 days and can be changed with
`PW155_BACKUP_RETENTION_DAYS`.

## Downloads and CPW payloads

Copy `downloads/manifest.example.json` to `downloads/manifest.json` and provide
only packages you are legally allowed to distribute. `downloads/CPW`, ZIP
files, game data, and generated manifests are ignored by Git.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests that need proprietary PW data are skipped automatically when the local
fixtures are absent; source-only security and worker tests still run.

## License

ASP PWPanel source code and documentation are released under the
[MIT License](LICENSE). The license does not grant rights to Perfect World or
any third-party assets.
