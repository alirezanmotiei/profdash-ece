"""User profile: the configuration that makes profdash *yours*.

Loaded from profile.toml (see paths.find_profile). Every personalization
in the framework — your name, research interests, email signature,
timezone, venue tiers, self-filter rules — lives here, never in code.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass
class Identity:
    """Who you are. Used when drafting outreach emails."""
    name: str = ""                  # full name used in the signature
    email: str = ""                 # your from address (informational)
    bio: str = ""                   # first-person paragraph: degree, interests, results
    interests_short: str = "your research area"   # short phrase for relevance notes
    signature: str = ""             # closing lines after "Best regards,"

    def loaded(self) -> bool:
        return bool(self.name and self.bio)


@dataclass
class SelfFilter:
    """Rules that tell the Gmail backfill which addresses are *you* (or
    otherwise not a professor), so sent-mail scanning doesn't misattribute."""
    domains: list[str] = field(default_factory=list)          # e.g. ["myuni.edu"]
    name_tokens: list[str] = field(default_factory=list)      # e.g. ["jsmith"]
    skip_subject_patterns: list[str] = field(default_factory=list)  # regexes


@dataclass
class Gmail:
    token_path: str = ""            # empty → paths.default_token_path()


@dataclass
class Venues:
    """Venue tiers used by paper selection and scoring digests.

    `preset` names a file in presets/ shipped with the package; inline
    lists extend or override it.
    """
    preset: str = ""
    strong: list[str] = field(default_factory=list)
    moderate: list[str] = field(default_factory=list)
    lower: list[str] = field(default_factory=list)


@dataclass
class Outreach:
    follow_up_days: int = 21        # days before a contacted prof shows on /follow-ups
    min_score_shortlist: int = 7    # default min_score on /shortlist


@dataclass
class Profile:
    timezone: str = "UTC"
    identity: Identity = field(default_factory=Identity)
    self_filter: SelfFilter = field(default_factory=SelfFilter)
    gmail: Gmail = field(default_factory=Gmail)
    venues: Venues = field(default_factory=Venues)
    outreach: Outreach = field(default_factory=Outreach)
    source_path: Path | None = None

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return ZoneInfo("UTC")


def _load_preset(name: str) -> dict:
    """Load a venue preset by built-in name (presets/<name>.toml in the package)."""
    p = Path(__file__).resolve().parent / "presets" / f"{name}.toml"
    if not p.exists():
        raise FileNotFoundError(
            f"Unknown venue preset {name!r} (no {p}). "
            f"Or point venues.preset at your own .toml file path."
        )
    with open(p, "rb") as f:
        return tomllib.load(f)


def _load_preset_file(path: str | Path) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Venue preset not found: {p}")
    with open(p, "rb") as f:
        return tomllib.load(f)


def load_profile(path: str | Path | None = None) -> Profile:
    """Build a Profile from TOML + defaults. Missing file → pure defaults."""
    from . import paths

    resolved: Path | None = None
    if path is not None:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Profile not found at {resolved}")
    else:
        resolved = paths.find_profile()

    prof = Profile(source_path=resolved)
    if resolved is None:
        return prof

    with open(resolved, "rb") as f:
        raw = tomllib.load(f)

    prof.timezone = raw.get("timezone", prof.timezone)

    ident = raw.get("identity", {})
    prof.identity = Identity(
        name=ident.get("name", ""),
        email=ident.get("email", ""),
        bio=ident.get("bio", ""),
        interests_short=ident.get("interests_short", prof.identity.interests_short),
        signature=ident.get("signature", ""),
    )

    sf = raw.get("self_filter", {})
    prof.self_filter = SelfFilter(
        domains=[d.lower() for d in sf.get("domains", [])],
        name_tokens=[t.lower() for t in sf.get("name_tokens", [])],
        skip_subject_patterns=list(sf.get("skip_subject_patterns", [])),
    )

    g = raw.get("gmail", {})
    prof.gmail = Gmail(token_path=g.get("token_path", ""))

    v = raw.get("venues", {})
    strong = [s.lower() for s in v.get("strong", [])]
    moderate = [s.lower() for s in v.get("moderate", [])]
    lower = [s.lower() for s in v.get("lower", [])]
    preset_name = v.get("preset", "")
    if preset_name:
        p = Path(preset_name).expanduser()
        data = _load_preset_file(p) if p.suffix == ".toml" else _load_preset(preset_name)
        strong += [s.lower() for s in data.get("strong", [])]
        moderate += [s.lower() for s in data.get("moderate", [])]
        lower += [s.lower() for s in data.get("lower", [])]
    prof.venues = Venues(preset=preset_name, strong=strong,
                         moderate=moderate, lower=lower)

    o = raw.get("outreach", {})
    prof.outreach = Outreach(
        follow_up_days=int(o.get("follow_up_days", 21)),
        min_score_shortlist=int(o.get("min_score_shortlist", 7)),
    )
    return prof
