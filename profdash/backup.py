"""Point-in-time backup via the sqlite3 online backup API.

Unlike `cp`, this captures WAL contents correctly and produces a single
consistent snapshot file.
"""
from __future__ import annotations

import datetime
import gzip
import sqlite3
from pathlib import Path


def backup(db_path, dest_dir=None, keep: int = 14, log=print) -> Path:
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"Database not found at {src}")
    base = Path(dest_dir).expanduser() if dest_dir else \
        Path.home() / "backups" / "profdash"
    base.mkdir(parents=True, exist_ok=True)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    raw = base / f"profdash-{stamp}.sqlite"
    conn = sqlite3.connect(str(src))
    try:
        dst = sqlite3.connect(str(raw))
        with dst:
            conn.backup(dst)
        dst.close()
    finally:
        conn.close()

    out = raw.with_suffix(".sqlite.gz")
    with open(raw, "rb") as f_in, gzip.open(out, "wb") as f_out:
        f_out.writelines(f_in)
    raw.unlink()

    # prune old backups, newest kept
    backups = sorted(base.glob("profdash-*.sqlite.gz"))
    for old in backups[:-keep] if keep else []:
        old.unlink()

    log(f"Backup written: {out}")
    return out
