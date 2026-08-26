# Setup guide

## Requirements

- Python 3.11+
- That's it. No database server, no node_modules, no build step.

## Install

```bash
pip install profdash          # into a venv is fine too
# or isolated:
pipx install profdash
```

Gmail features (optional): `pip install profdash[gmail]`

## First run

```bash
mkdir my-outreach && cd my-outreach
prof init --demo              # try it with sample data first
prof serve --no-open          # http://localhost:8000
```

`prof init` creates:

```
./data/profdash.sqlite    # the entire database (WAL mode)
./profile.toml            # your personalization
```

All commands resolve these from the current directory — run `prof` from
this folder (or pass `--db` / set `PROF_DB_PATH`).

## Real data

```bash
prof import csrankings                     # everything (~30k rows)
prof import csrankings --region europe     # or filter
prof import csrankings --country "South Korea" --country Japan
```

Dataset files are downloaded from the CSRankings GitHub repo and cached in
`~/.cache/profdash/csrankings` for a week (`--refresh` forces re-download).

Rows land in stage `triaged` with status `not_contacted` — ready for the
scoring pass. Re-running the import is idempotent: existing rows get
missing metadata refreshed; scores/statuses are never touched.

## Configure your profile

Edit `profile.toml`. The important parts for outreach:

- `[identity]` — used verbatim in drafted emails
- `[venues]` — which venues count as strong/moderate for *your field*
  (built-in preset: `theory`; ship your own `.toml` and point at it)
- `[self_filter]` — how the Gmail backfill recognizes your own addresses
- `timezone` — display timezone for every timestamp in the UI

## Scoring pass

See [scoring.md](scoring.md).

## Updating

```bash
pip install --upgrade profdash
prof migrate        # apply any pending additive migrations (usually a no-op)
```

## Backups

```bash
prof backup                     # ~/backups/profdash/profdash-<ts>.sqlite.gz
prof backup --dest /path --keep 30
```

Uses the SQLite backup API — consistent even while the dashboard is running.
