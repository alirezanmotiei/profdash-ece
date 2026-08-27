"""DBLP publication digest for the scoring pass.

Fetches an author's recent publications from the DBLP search API and
condenses them into a compact text digest a reviewer (human or AI agent)
can judge quickly:

    DIGEST Jane Doe | hits=42 | span=2011-2026 | recentSTRONG=3 recentMEDIUM=1
    -- RECENT STRONG/MEDIUM --
    2026 [STOC] Fast algorithms for ...
    ...

Robustness (learned the hard way):
  * DBLP author facets are CASE-SENSITIVE — we try verbatim, title-cased,
    and word-swapped name forms.
  * DBLP occasionally serves stale "0 hits" or rate-limited responses —
    we retry across mirrors with backoff.
  * Results are cached per professor for 24h under <data>/sources_cache/.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

import httpx

MIRRORS = ["https://dblp.org", "https://dblp.uni-trier.de", "https://dblp.dagstuhl.de"]
USER_AGENT = "profdash/0.1 (academic outreach; local tool)"

SKIP_TYPES = {"Informal and Other Publications", "Editorship", "Reference Works"}


def default_venue_tiers() -> tuple[set[str], set[str]]:
    """Fallback tiers if the caller has no profile/preset."""
    return (
        {"stoc", "focs", "soda", "icalp", "esa", "socg", "sicomp",
         "jacm", "podc", "disc", "ec"},
        {"itcs", "wine", "sosa", "approx", "random", "latin", "cpm",
         "wg", "stacs", "mfcs", "isaac", "lipics", "spaa"},
    )


def _curl_get(client: httpx.Client, url: str, timeout: float = 30.0) -> str:
    try:
        r = client.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT},
                       follow_redirects=True)
        return r.text or ""
    except Exception:
        return ""


def name_forms(name: str) -> list[str]:
    """Candidate spellings to try against the case-sensitive DBLP facet.

    CSRankings-style names may carry DBLP disambiguators which ARE valid
    author-facet tokens ("Jane Doe [SWS]", "Aaron Roth 0001") — but not
    every form resolves for every author, so we try several.
    """
    base = name.strip()
    no_bracket = re.sub(r"\s*\[[^\]]+\]\s*$", "", base)
    no_number = re.sub(r"\s+\d{4}$", "", base)
    no_both = re.sub(r"\s+\d{4}$", "", no_bracket)

    forms: list[str] = []
    for candidate in (base, no_bracket, no_number, no_both):
        titled = candidate.title()
        variants = {candidate, titled}
        parts = titled.split()
        if len(parts) >= 2:
            variants.add(" ".join(reversed(parts)))
        parts_raw = candidate.split()
        if len(parts_raw) >= 2:
            variants.add(" ".join(reversed(parts_raw)))
        for v in variants:
            if v and v.lower() not in {f.lower() for f in forms}:
                forms.append(v)
    return forms


def query_url(form: str, h: int = 100, host: str = MIRRORS[0]) -> str:
    q = "author:" + form.replace(" ", "_") + ":"
    return f"{host}/search/publ/api?q={quote(q)}&format=xml&h={h}"


def has_hits(body: str) -> bool:
    return bool(re.search(r"<hit[\s>]", body))


def looks_suspicious_empty(body: str) -> bool:
    """Completions present but zero hits => stale cache window; retry/mirror."""
    return '<hits total="0"' in body and 'completions total="1"' in body


def looks_rate_limited(body: str) -> bool:
    return "Too Many Requests" in body or 'status code="429"' in body or len(body) < 80


def fetch_xml(client: httpx.Client, name: str, *, tries_per_form: int = 4) -> tuple[str | None, str]:
    """Try every name form across mirrors. Returns (xml|None, tried_summary)."""
    tried = []
    for form in name_forms(name):
        for attempt in range(tries_per_form):
            host = MIRRORS[min(attempt // 2, len(MIRRORS) - 1)]
            body = _curl_get(client, query_url(form, host=host))
            if has_hits(body) and '<hits total="0"' not in body:
                return body, form
            tried.append(form)
            if looks_suspicious_empty(body) or looks_rate_limited(body):
                time.sleep(2 + 2 * attempt)
                continue
            break
        time.sleep(0.5)
    return None, ", ".join(sorted(set(tried)))


def parse_hits(xml_text: str) -> list[dict]:
    """Minimal XML parse without pulling ElementTree namespaces into play."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for hit in root.iter("hit"):
        info = hit.find("info")
        if info is None:
            continue

        def g(tag: str) -> str:
            el = info.find(tag)
            return (el.text or "") if el is not None else ""

        out.append({"title": g("title").rstrip("."),
                    "venue": g("venue"),
                    "year": g("year"),
                    "type": g("type"),
                    "ee": g("ee")})
    out.sort(key=lambda x: x.get("year") or "", reverse=True)
    return out


def build_digest(name: str, hits: list[dict], strong: set[str], moderate: set[str],
                 *, now_y: int | None = None) -> str:
    now_y = now_y or datetime.date.today().year
    strong = {s.lower().strip() for s in strong}
    moderate = {s.lower().strip() for s in moderate}
    strong_recent, med_recent, other, years = [], [], [], []
    for h in hits:
        y = int(h["year"]) if str(h["year"]).isdigit() else None
        if y:
            years.append(y)
        v = (h["venue"] or "").lower().strip()
        line = "%s [%s] %s" % (y, ((h["venue"] or "?"))[:30], h["title"][:90])
        recent = y is not None and y >= now_y - 3
        if v in strong:
            (strong_recent if recent else other).append(line)
        elif v in moderate:
            (med_recent if recent else other).append(line)
        else:
            other.append(line)
    span = "%d-%d" % (min(years), max(years)) if years else "?"
    lines = ["DIGEST %s | hits=%d | span=%s | recentSTRONG=%d recentMEDIUM=%d"
             % (name, len(hits), span, len(strong_recent), len(med_recent)),
             "-- RECENT STRONG/MEDIUM --"]
    lines += (strong_recent + med_recent) if (strong_recent or med_recent) else ["  (none)"]
    lines += ["-- OTHER (max 14) --"] + other[:14]
    return "\n".join(lines)


def _oa_works(client: httpx.Client, openalex_id: str) -> list[dict]:
    """Recent (>=2019) works for one OpenAlex author id."""
    import os as _os
    sel = "publication_year,title,primary_location"
    url = (f"https://api.openalex.org/works?filter=author.id:{openalex_id},"
           f"publication_year:>2018&sort=publication_date:desc&per-page=50"
           f"&select={sel}")
    mailto = _os.environ.get("OPENALEX_MAILTO")
    if mailto:
        url += f"&mailto={mailto}"
    r = client.get(url, headers={"User-Agent": USER_AGENT},
                   follow_redirects=True)
    r.raise_for_status()
    return r.json().get("results", [])


def _venue_hit(venue: str, tiers: set[str]) -> bool:
    """Tier match as a case-insensitive substring of the venue name."""
    v = (venue or "").lower()
    return any(t.lower().strip() in v for t in tiers if t.strip())


def build_digest_openalex(name: str, works: list[dict],
                          strong: set[str], moderate: set[str]) -> str:
    """Same DIGEST text shape as the DBLP builder, fed by OpenAlex works."""
    strong_recent, med_recent, other, years = [], [], [], []
    for w in works:
        y = w.get("publication_year")
        if y:
            years.append(int(y))
        venue = (((w.get("primary_location") or {}).get("source") or {})
                 .get("display_name") or "?")
        line = "%s [%s] %s" % (y, venue[:30], (w.get("title") or "?")[:90])
        recent = y is not None and int(y) >= datetime.date.today().year - 3
        if _venue_hit(venue, strong):
            (strong_recent if recent else other).append(line)
        elif _venue_hit(venue, moderate):
            (med_recent if recent else other).append(line)
        else:
            other.append(line)
    span = "%d-%d" % (min(years), max(years)) if years else "?"
    lines = ["DIGEST %s | hits=%d | span=%s | recentSTRONG=%d "
             "recentMEDIUM=%d | source=openalex"
             % (name, len(works), span, len(strong_recent), len(med_recent)),
             "-- RECENT STRONG/MEDIUM --"]
    lines += (strong_recent + med_recent) if (strong_recent or med_recent) else ["  (none)"]
    lines += ["-- OTHER (max 14) --"] + other[:14]
    return "\n".join(lines)


def fetch_digest(prof_id: str, db_path: Path, *, cache_root: Path | None = None,
                 strong: set[str] | None = None, moderate: set[str] | None = None,
                 max_age_h: float = 24.0, client: httpx.Client | None = None) -> str:
    """Digest for one professor id. Cached 24h under cache_root/<prof_id>/."""
    own_client = client is None
    client = client or httpx.Client(timeout=30)
    strong = strong or set()
    moderate = moderate or set()
    if not strong and not moderate:
        strong, moderate = default_venue_tiers()

    conn = sqlite3.connect(str(db_path))
    try:
        try:
            row = conn.execute(
                "SELECT professor, openalex_id FROM professors WHERE id=?",
                (prof_id,)).fetchone()
        except sqlite3.OperationalError:  # pre-migration DB
            row = conn.execute(
                "SELECT professor, NULL FROM professors WHERE id=?",
                (prof_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return json.dumps({"error": "not_found", "id": prof_id})
    name, openalex_id = row[0], row[1]

    cache_root = cache_root or (Path(db_path).parent / "sources_cache")
    pdir = cache_root / prof_id.replace("/", "_")
    pdir.mkdir(parents=True, exist_ok=True)
    metap, xmlp = pdir / "meta.json", pdir / "dblp.xml"
    oajsonp = pdir / "openalex.json"

    if metap.exists() and xmlp.exists():
        if (time.time() - metap.stat().st_mtime) / 3600.0 < max_age_h:
            xml = xmlp.read_text(errors="replace")
            if has_hits(xml) and '<hits total="0"' not in xml:
                return build_digest(name, parse_hits(xml), strong, moderate)

    if metap.exists() and oajsonp.exists():
        if (time.time() - metap.stat().st_mtime) / 3600.0 < max_age_h:
            try:
                works = json.loads(oajsonp.read_text())
                if works:
                    return build_digest_openalex(name, works, strong, moderate)
            except Exception:
                pass  # fall through to a fresh fetch

    xml, form = fetch_xml(client, name)
    if xml is None:
        (pdir / "dblp_fail.txt").write_text(
            "no hits for %r\n" % name_forms(name))
        if openalex_id:
            # EE/BME professors are often missing from DBLP — fall back
            # to their OpenAlex publication record.
            try:
                works = _oa_works(client, openalex_id)
            except Exception:
                works = []
            if works:
                oajsonp.write_text(json.dumps(works))
                metap.write_text(json.dumps({
                    "prof": name, "form": "openalex",
                    "sha256": hashlib.sha256(
                        json.dumps(works).encode()).hexdigest(),
                    "fetched_at": datetime.datetime.now(
                        datetime.timezone.utc).isoformat()},
                    indent=1))
                return build_digest_openalex(name, works, strong, moderate)
        return json.dumps({"error": "dblp_no_hits", "id": prof_id, "name": name})
    xmlp.write_text(xml)
    metap.write_text(json.dumps({
        "prof": name, "form": form,
        "sha256": hashlib.sha256(xml.encode()).hexdigest(),
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
        indent=1))
    return build_digest(name, parse_hits(xml), strong, moderate)


def fetch_digests(prof_ids: list[str], db_path: Path, **kwargs) -> dict[str, str]:
    """Digests for several ids, politely rate-limited."""
    out: dict[str, str] = {}
    with httpx.Client(timeout=30) as client:
        for pid in prof_ids:
            out[pid] = fetch_digest(pid, db_path, client=client, **kwargs)
            time.sleep(0.6)
    return out
