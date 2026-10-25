"""Task 6.2: the legacy audit is read-only and lists the defects the owner must decide on."""
import copy

from profinder_pipeline.audit.legacy import REASONS, audit_professors, audit_templates, summary_md, to_csv
from profinder_pipeline.utils.placeholders import PlaceholderRegistry

REG = PlaceholderRegistry.from_yaml()
UNI = {"u1": "Tsinghua University", "u2": "Fudan University"}


def P(i, **kw):
    base = {"id": i, "name": f"Name {i}", "nameZh": None, "universityId": "u1", "dataSource": "official_import", "dataStatus": "verified",
            "email": None, "profileUrl": None, "position": "Professor", "researchInterests": None}
    return {**base, **kw}


def test_every_finding_kind_is_listed():
    rows = [P("a1", dataSource="openalex", dataStatus="unverified"), P("a2", dataSource=None), P("a3", dataSource="seed"),
            P("d1", email="X@tsinghua.edu.cn"), P("d2", email="x@tsinghua.edu.cn"),
            P("z1", nameZh="王新"), P("z2", nameZh="王新"), P("z3", nameZh="王新", universityId="u2"),
            P("s1", profileUrl="https://www.cs.x.edu.cn/people.htm"), P("s2", profileUrl="https://www.cs.x.edu.cn/people.htm"),
            P("s3", profileUrl="https://www.cs.x.edu.cn/people.htm", universityId="u2"),
            P("pp", position="Faculty", researchInterests="computer science"), P("ok", profileUrl="https://x.edu.cn/1")]
    out = audit_professors(rows, UNI, REG)
    by = {}
    for r in out:
        by.setdefault(r["reason"], set()).add(r["professor_id"])
    assert by["openalex_source"] == {"a1"} and by["no_source"] == {"a2"} and by["not_official_source"] == {"a3"}
    assert by["duplicate_email"] == {"d1", "d2"}                                  # case-insensitive pair
    assert by["duplicate_name_zh"] == {"z1", "z2"}                                # same name at ANOTHER university is not a duplicate
    assert by["shared_directory_url"] == {"s1", "s2"}
    assert by["placeholder_position"] == {"pp"} and by["placeholder_interests"] == {"pp"}
    assert "ok" not in {r["professor_id"] for r in out}
    pair = [r for r in out if r["reason"] == "duplicate_email" and r["professor_id"] == "d1"][0]
    assert pair["other_ids"] == "d2" and pair["university"] == "Tsinghua University" and pair["group_key"] == "x@tsinghua.edu.cn"


def test_audit_does_not_modify_its_input_and_csv_is_complete():
    rows = [P("a1", dataSource="openalex"), P("d1", email="x@y.cn"), P("d2", email="x@y.cn")]
    before = copy.deepcopy(rows)
    out = audit_professors(rows, UNI, REG)
    assert rows == before                                                           # read-only: no mutation, no DB handle in the signature
    csv_text = to_csv(out)
    assert csv_text.splitlines()[0].startswith("professor_id,university,name,nameZh,dataSource") and len(csv_text.splitlines()) == len(out) + 1


def test_summary_counts_pairs_and_templates():
    rows = [P("d1", email="x@y.cn"), P("d2", email="x@y.cn"), P("e1", email="q@y.cn"), P("e2", email="q@y.cn"), P("o", dataSource="openalex")]
    out = audit_professors(rows, UNI, REG)
    t = audit_templates([{"programUrl": None, "deadline": "Not confirmed - check"}, {"programUrl": "https://x", "deadline": "2026-03-31"}],
                        [{"deadline": "Not confirmed", "verificationStatus": "UNVERIFIED"}], REG)
    assert t == {"programs": 2, "programs_without_programUrl": 1, "programs_placeholder_deadline": 1, "scholarships": 1,
                 "scholarships_placeholder_deadline": 1, "scholarships_not_verified": 1}
    md = summary_md(out, rows, t)
    assert "Duplicate-email groups: 2" in md and "openalex_source: 1" in md and "Nothing was written" in md
    assert all(r in md for r in REASONS)
