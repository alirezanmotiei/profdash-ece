"""Path resolution for the profdash database and profile file.

Resolution order (highest first):
  1. explicit argument (--db / --profile)
  2. environment variable (PROF_DB_PATH / PROF_PROFILE)
  3. conventional files in the current working directory
     (./data/profdash.sqlite, ./profile.toml)

`prof init` creates ./data/ and ./profile.toml in the current directory,
so a typical workflow is: mkdir my-outreach && cd my-outreach && prof init.
All other commands work from that directory without flags.
"""
from __future__ import annotations

import os
from pathlib import Path

DB_FILENAME = "profdash.sqlite"
PROFILE_FILENAME = "profile.toml"


def find_db(explicit: str | None = None, *, must_exist: bool = True) -> Path:
    """Resolve the active SQLite database path."""
    raw = explicit or os.environ.get("PROF_DB_PATH")
    if raw:
        p = Path(raw).expanduser().resolve()
        if must_exist and not p.exists():
            raise FileNotFoundError(
                f"Database not found at {p}\n"
                f"Run `prof init` or pass --db / set PROF_DB_PATH."
            )
        return p
    for candidate in (Path.cwd() / "data" / DB_FILENAME,
                      Path.cwd() / DB_FILENAME):
        if candidate.exists():
            return candidate.resolve()
    if must_exist:
        raise FileNotFoundError(
            f"No {DB_FILENAME} found in ./data/ or ./\n"
            f"Run `prof init` first, or pass --db / set PROF_DB_PATH."
        )
    return (Path.cwd() / "data" / DB_FILENAME).resolve()


def suggest_db(create_parent: bool = False) -> Path:
    """Where `prof init` will put the database."""
    p = Path.cwd() / "data" / DB_FILENAME
    if create_parent:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def find_profile(explicit: str | None = None) -> Path | None:
    """Resolve the profile.toml path, or None to use built-in defaults."""
    raw = explicit or os.environ.get("PROF_PROFILE")
    if raw:
        p = Path(raw).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Profile not found at {p}")
        return p
    local = Path.cwd() / PROFILE_FILENAME
    if local.exists():
        return local.resolve()
    xdg = Path.home() / ".config" / "profdash" / PROFILE_FILENAME
    if xdg.exists():
        return xdg.resolve()
    return None


def default_token_path() -> Path:
    """Default Gmail OAuth token location (XDG-style)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "profdash" / "google_token.json"
