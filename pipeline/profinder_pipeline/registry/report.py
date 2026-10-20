from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .merge import Merged

COLUMNS = [
    "name", "status", "matched_sources", "n_sources", "nameZh", "nameZh_source", "moe_code", "province", "city", "city_zh",
    "officialUrl_proposed", "officialUrl_source", "url_ok", "officialUrl_existing", "existing_is_placeholder",
    "agencyNumber_existing", "agency_equals_moe_code", "openAlexId_proposed", "openAlexId_existing", "ror_id",
    "campuschina_key", "existing_id", "flags", "targets_found",
]


def to_row(m: Merged) -> dict:
    return {
        "name": m.name, "status": m.status, "matched_sources": "+".join(m.sources), "n_sources": len(m.sources),
        "nameZh": m.name_zh or "", "nameZh_source": m.name_zh_source or "", "moe_code": m.moe_code or "",
        "province": m.province or "", "city": m.city or "", "city_zh": m.city_zh or "",
        "officialUrl_proposed": m.official_url or "", "officialUrl_source": m.official_url_source or "",
        "url_ok": "" if m.url_ok is None else str(m.url_ok).lower(),
        "officialUrl_existing": m.existing_official_url or "",
        "existing_is_placeholder": str(m.existing_official_url_is_placeholder).lower(),
        "agencyNumber_existing": m.existing_agency_number or "",
        "agency_equals_moe_code": ("" if not (m.existing_agency_number and m.moe_code) else str(m.existing_agency_number == m.moe_code).lower()),
        "openAlexId_proposed": m.openalex_id or "", "openAlexId_existing": m.existing_openalex_id or "",
        "ror_id": m.ror_id or "", "campuschina_key": m.campuschina_key or "", "existing_id": m.existing_id or "",
        "flags": ";".join(m.flags), "targets_found": "n/a (task 2.5)",
    }


def write_report(merged: list[Merged], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_p, json_p = out_dir / "registry_report.csv", out_dir / "universities_merged.json"
    with csv_p.open("w", encoding="utf-8-sig", newline="") as f:   # BOM so Excel shows Chinese correctly
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for m in merged:
            w.writerow(to_row(m))
    json_p.write_text(json.dumps([asdict(m) for m in merged], ensure_ascii=False, indent=1), encoding="utf-8")
    return csv_p, json_p


def summary(merged: list[Merged]) -> str:
    from collections import Counter

    st = Counter(m.status for m in merged)
    fl = Counter(f.split(":")[0] for m in merged for f in m.flags)
    src = Counter(s for m in merged for s in m.sources)
    lines = [f"universities: {len(merged)}", "status: " + ", ".join(f"{k}={v}" for k, v in sorted(st.items())),
             "matched per source: " + ", ".join(f"{k}={src.get(k, 0)}" for k in ("moe", "ror", "openalex", "campuschina")),
             "flags: " + (", ".join(f"{k}={v}" for k, v in fl.most_common()) or "none")]
    return "\n".join(lines)


_QUIET = {"no_existing_row", "no_campuschina_match", "url_not_checked"}


def diagnose(merged: list[Merged], checks: dict[str, dict]) -> str:
    """Compact text for humans (and for pasting into a chat): every row that is not clean, plus URL failure reasons."""
    from collections import Counter

    def url_state(m: Merged) -> str:
        c = checks.get(m.official_url or "")
        return "-" if not c else str(c.get("status") or c.get("error") or "?")

    out = []
    bad = [m for m in merged if m.status != "verified"]
    out.append(f"== NOT VERIFIED ({len(bad)}) ==")
    for m in bad:
        out.append(f"- {m.name} | {m.status} | src={'+'.join(m.sources) or '-'} | url={url_state(m)} {m.official_url or ''} | {', '.join(f for f in m.flags if f.split(':')[0] not in _QUIET)}")
    noted = [m for m in merged if m.status == "verified" and any(f.split(":")[0] not in _QUIET for f in m.flags)]
    out.append(f"== VERIFIED BUT WITH NOTES ({len(noted)}) ==")
    for m in noted:
        out.append(f"- {m.name} | {', '.join(f for f in m.flags if f.split(':')[0] not in _QUIET)}")
    fails = Counter(url_state(m) for m in merged if m.official_url and url_state(m) not in ("200", "-"))
    out.append("== URL FAILURE REASONS == " + (", ".join(f"{k}={v}" for k, v in fails.most_common()) or "none"))
    return "\n".join(out)