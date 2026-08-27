"""Import ECE/BME faculty from the OpenAlex dataset.

CSRankings only tracks CS faculty, so pure Electrical & Computer
Engineering / Biomedical Engineering professors need a different source.
OpenAlex (https://openalex.org, CC0) indexes 250M+ works across ALL
disciplines with author affiliations, institution countries and topic
classifications — the raw material for an ECE/BME professor database.

Strategy:
  1. Resolve every topic ID of the two relevant subfields
     (2204 Biomedical Engineering, 2208 Electrical & Electronic
     Engineering) plus a small curated set of adjacent topics
     (electroporation, radiation therapy, ultrasound/hyperthermia,
     EMG, vital-sign monitoring, AI in cancer detection).
  2. Page the /authors endpoint filtered by those topics + target
     countries + h-index / works-count thresholds (a senior-researcher
     proxy that filters out PhD students).
  3. Classify each author into a research bucket (biomed / circuits /
     ml / ee_general) from their topic profile and upsert into the
     same `professors` table the CSRankings importer uses.

Notes & limitations (be honest when using the data):
  * OpenAlex knows nothing about academic rank — research staff and
    industry scientists slip in. The AI scoring pass is the
    human-in-the-loop filter for those.
  * A few OpenAlex author entities are "merged" junk (many name
    variants collapsed into one). `prof audit` flags duplicate persons.
  * No emails or homepages here; the scoring agent finds those live,
    the same way it does for CSRankings rows without a homepage.

Usage:
    prof import openalex [--country US ...] [--min-h-index 18]
                         [--min-works 30] [--limit N] [--refresh-topics]
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import time
from pathlib import Path

import httpx

API = "https://api.openalex.org"

# ISO alpha-2 -> display name (defaults; unknown codes pass through).
COUNTRY_NAMES = {
    "US": "United States", "CA": "Canada", "CH": "Switzerland",
    "DE": "Germany", "GB": "United Kingdom", "AT": "Austria",
    "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "NL": "Netherlands",
    "FR": "France", "IT": "Italy", "ES": "Spain", "BE": "Belgium",
}

# OpenAlex topic subfields that make up ECE + BME.
TOPIC_SUBFIELDS = ["2204", "2208"]  # Biomedical Eng., Electrical & Electronic Eng.

# Adjacent topics outside those two subfields that matter for
# bioelectronics / medical-physics outreach. Verified IDs.
EXTRA_TOPIC_IDS = [
    "T11176",  # Radiation Therapy and Dosimetry (medical physics)
    "T11455",  # Microbial Inactivation Methods (electroporation / PEF)
    "T10958",  # Ultrasound and Hyperthermia Applications
    "T10784",  # Muscle activation and electromyography studies
    "T11196",  # Non-Invasive Vital Sign Monitoring
    "T10862",  # AI in cancer detection
]

SELECT_FIELDS = ("id,orcid,display_name,last_known_institutions,"
                 "summary_stats,works_count,cited_by_count,topics")

BUCKETS = ("biomed", "circuits", "ml", "ee_general")

_KW = {
    "biomed": [
        "biomedical", "bioelectron", "biosensor", "bio-sensor", "medical",
        "cancer", "tumor", "tumour", "oncolog", "radiation therapy",
        "radiotherapy", "electroporation", "ultrasound", "hyperthermia",
        "electromyography", "vital sign", "clinical", "health", "ecg",
        "eeg", "brain", "neural engineer", "neuroengineer", "tissue",
        "theranostic", "implant", "wearable", "point-of-care", "organ",
        "cell culture", "gene", "drug deliver", "protein", "biofabricat",
        "microfluidic", "surgical", "physiology", "patient",
    ],
    "circuits": [
        "analog", "analogue", "circuit", "rf ", "radio frequency",
        "microwave", "antenna", "wireless", "telecommunication",
        "communication", "vlsi", "semiconductor", "transistor", "cmos",
        "photon", "optoelectron", "optical", "signal processing",
        "embedded", "sensor", "mems", "power electronics", "converter",
        "inverter", "photovoltaic", "solar cell", "radar", "5g", "6g",
        "mimo", "fpga", "asic", "filter design", "amplifier",
    ],
    "ml": [
        "machine learning", "deep learning", "artificial intelligence",
        "neural network", "computer vision", "data mining",
        "reinforcement learning", "pattern recognition",
        "domain adaptation", "generative model", "natural language",
    ],
}


def _mailto() -> str:
    return os.environ.get("OPENALEX_MAILTO", "")


def cache_dir(explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
    else:
        p = (Path.home() / ".cache" / "profdash" / "openalex").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slug(name: str, university: str) -> str:
    from .csrankings import _slug as slug_fn
    return slug_fn(name, university)


def _get(client: httpx.Client, url: str, *, retries: int = 3) -> dict:
    last_err: Exception | None = None
    for i in range(retries):
        try:
            r = client.get(url)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1.5 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"OpenAlex request failed after {retries} tries: "
                       f"{url} ({last_err})")


def resolve_topic_ids(client: httpx.Client, cache: Path, *,
                      refresh: bool = False, log=print) -> list[str]:
    """All topic IDs of the ECE/BME subfields + curated extras (cached 30d)."""
    tp = cache / "topics.json"
    if tp.exists() and not refresh:
        if (time.time() - tp.stat().st_mtime) < 30 * 86400:
            return json.loads(tp.read_text())
    ids: list[str] = []
    for sf in TOPIC_SUBFIELDS:
        url = (f"{API}/topics?filter=subfield.id:{sf}&per-page=200"
               + (f"&mailto={_mailto()}" if _mailto() else ""))
        data = _get(client, url)
        ids += [t["id"].rsplit("/", 1)[-1] for t in data.get("results", [])]
        log(f"  subfield {sf}: {len(data.get('results', []))} topics")
    ids += [t for t in EXTRA_TOPIC_IDS if t not in ids]
    tp.write_text(json.dumps(sorted(set(ids))))
    return sorted(set(ids))


def classify_bucket(topics: list[dict]) -> str:
    """Score each bucket by summed topic counts; pick the winner."""
    scores = {b: 0 for b in BUCKETS}
    for t in topics or []:
        name = (t.get("display_name") or "").lower()
        subfield = ((t.get("subfield") or {}).get("id") or "")
        weight = int(t.get("count") or 1)
        for bucket, kws in _KW.items():
            if any(k in name for k in kws):
                scores[bucket] += weight
        if subfield.endswith("2204"):
            scores["biomed"] += weight  # subfield match counts extra
    best = max(BUCKETS, key=lambda b: scores[b])
    return best if scores[best] > 0 else "ee_general"


def fetch_authors(client: httpx.Client, *, countries: list[str],
                  topic_ids: list[str], min_h: int, min_works: int,
                  log=print):
    """Yield author dicts for all target countries, cursor-paged.

    OpenAlex rejects very large OR-filters, so topic IDs are queried in
    chunks of 50; duplicate authors across chunks are deduped by the
    idempotent upsert.
    """
    countries_str = "|".join(countries)
    for start in range(0, len(topic_ids), 50):
        chunk = topic_ids[start:start + 50]
        filt = (f"last_known_institutions.country_code:{countries_str},"
                f"topics.id:{'|'.join(chunk)},"
                f"summary_stats.h_index:>{min_h},works_count:>{min_works}")
        url = (f"{API}/authors?filter={filt}"
               f"&select={SELECT_FIELDS}&sort=cited_by_count:desc&per-page=200"
               + (f"&mailto={_mailto()}" if _mailto() else ""))
        log(f"  topic chunk {start // 50 + 1}/{-(-len(topic_ids) // 50)}")
        cursor = "*"
        while cursor:
            page = _get(client, url + f"&cursor={cursor}")
            meta = page.get("meta", {})
            results = page.get("results", [])
            log(f"  fetched page ({meta.get('count', '?')} matches in chunk)")
            yield from results
            cursor = meta.get("next_cursor")
            time.sleep(0.15)


def _pick_institution(author: dict, wanted: set[str]) -> tuple[str, str] | None:
    """(university, country_name) from last_known_institutions, or None."""
    for inst in author.get("last_known_institutions") or []:
        cc = (inst.get("country_code") or "").upper()
        if cc in wanted and (inst.get("type") or "") == "education":
            return (inst.get("display_name") or "?"), COUNTRY_NAMES.get(cc, cc)
    for inst in author.get("last_known_institutions") or []:
        cc = (inst.get("country_code") or "").upper()
        if cc in wanted:  # fallback: keep non-education affiliations too
            return (inst.get("display_name") or "?"), COUNTRY_NAMES.get(cc, cc)
    return None


def upsert_professors(conn: sqlite3.Connection, rows: list[dict], *,
                      now: str, log=print) -> dict:
    stats = {"inserted": 0, "updated": 0}
    for r in rows:
        pid = _slug(r["name"], r["university"])
        dblp_url = "https://dblp.org/search?q=" + re.sub(r"\s+", "+", r["name"])
        exists = conn.execute(
            "SELECT 1 FROM professors WHERE id=?", (pid,)).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO professors(id, created_at, updated_at,
                       professor, university, location_country, homepage,
                       dblp_url, orcid, openalex_id, research_bucket,
                       status, stage, last_csv_sync_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, 'not_contacted',
                           'triaged', ?)""",
                (pid, now, now, r["name"], r["university"], r["country"],
                 r["homepage"], dblp_url, r["orcid"], r["openalex_id"],
                 r["bucket"], now))
            stats["inserted"] += 1
        else:
            sets, vals = ["updated_at=?", "last_csv_sync_at=?"], [now, now]
            if r["orcid"]:
                sets.append("orcid=COALESCE(orcid, ?)"); vals.append(r["orcid"])
            if r["homepage"]:
                sets.append("homepage=COALESCE(homepage, ?)"); vals.append(r["homepage"])
            sets.append("openalex_id=COALESCE(openalex_id, ?)"); vals.append(r["openalex_id"])
            sets.append("research_bucket=COALESCE(research_bucket, ?)"); vals.append(r["bucket"])
            conn.execute(f"UPDATE professors SET {', '.join(sets)} WHERE id=?",
                         (*vals, pid))
            stats["updated"] += 1
    return stats


def run_import(db_path: Path, cache_dir_arg: str | None = None, *,
               countries: list[str] | None = None,
               min_h: int = 18, min_works: int = 30,
               limit: int | None = None, refresh_topics: bool = False,
               log=print) -> dict:
    """Full import pass. Returns stats for CLI display."""
    from ..schema import create_all

    cache = cache_dir(cache_dir_arg)
    wanted = {c.strip().upper() for c in (countries or COUNTRY_NAMES)}
    unknown = wanted - set(COUNTRY_NAMES)
    if unknown:
        log(f"  (note: {', '.join(sorted(unknown))} not in the display-name "
            f"map; the ISO code will be stored as the country)")

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    bucket_counts = {b: 0 for b in BUCKETS}
    inserted = updated = skipped = 0
    try:
        create_all(conn)
        conn.commit()
        with httpx.Client(timeout=60, follow_redirects=True,
                          headers={"User-Agent":
                                   "profdash/0.1 (academic outreach; local tool)"}) as client:
            topic_ids = resolve_topic_ids(client, cache,
                                          refresh=refresh_topics, log=log)
            log(f"  using {len(topic_ids)} topic filters")
            # Stream into the DB per chunk so an interrupted import keeps
            # everything already fetched (upsert is idempotent/resumable).
            for start in range(0, len(topic_ids), 50):
                chunk = topic_ids[start:start + 50]
                chunk_rows = []
                for author in fetch_authors(client, countries=sorted(wanted),
                                            topic_ids=chunk, min_h=min_h,
                                            min_works=min_works, log=log):
                    picked = _pick_institution(author, wanted)
                    if not picked:
                        skipped += 1
                        continue
                    university, country = picked
                    bucket = classify_bucket(author.get("topics"))
                    bucket_counts[bucket] += 1
                    chunk_rows.append({
                        "name": author.get("display_name") or "?",
                        "university": university,
                        "country": country,
                        "homepage": None,
                        "orcid": (author.get("orcid") or "").rsplit("/", 1)[-1] or None,
                        "openalex_id": author["id"].rsplit("/", 1)[-1],
                        "bucket": bucket,
                    })
                    if limit and (inserted + updated) >= limit:
                        break
                stats = upsert_professors(conn, chunk_rows, now=now, log=log)
                inserted += stats["inserted"]
                updated += stats["updated"]
                conn.commit()
                log(f"  chunk done: {inserted} inserted, {updated} updated so far")
                if limit and (inserted + updated) >= limit:
                    break
    finally:
        conn.close()

    log("  buckets: " + ", ".join(f"{b}={n}" for b, n in bucket_counts.items())
        + f" | {skipped} rows skipped (no target-country institution)")
    return {"total_rows": inserted + updated, "inserted": inserted,
            "updated": updated, "cache": str(cache)}

