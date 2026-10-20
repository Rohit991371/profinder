"""Tests for the registry stage (tasks 2.1-2.4, 2.6). Plain asserts; run with: python -m pytest tests/test_registry.py -q"""
import json
import unittest
from pathlib import Path

import yaml

from profinder_pipeline.registry import campuschina, moe, openalex, ror
from profinder_pipeline.registry.domains import build_domains
from profinder_pipeline.registry.matching import pick_best
from profinder_pipeline.registry.merge import Existing, Seed, load_seed, merge_all
from profinder_pipeline.registry.report import COLUMNS, diagnose, to_row
from profinder_pipeline.registry.textnorm import (host_allowed, norm_en, origin_url, prov_key, registrable_domain)

FIX = Path(__file__).parent / "fixtures" / "registry"
ROOT = Path(__file__).resolve().parents[1]


def test_norm_en_ignores_spaces_case_and_ampersand():
    assert norm_en("North China ElectricPower University") == norm_en("North China Electric Power University")
    assert norm_en("Nanjing University of Scienceand Technology") == norm_en("Nanjing University of Science and Technology")
    assert norm_en("Xi'an Jiaotong University") == norm_en("Xi\u2019an Jiaotong University")
    assert norm_en("Science & Technology") == norm_en("science and technology")


def test_domains_and_urls():
    assert registrable_domain("https://www.cs.tsinghua.edu.cn/szzk/jzgml.htm") == "tsinghua.edu.cn"
    assert registrable_domain("www.buaa.edu.cn") == "buaa.edu.cn"
    assert registrable_domain("http://example.com") == "example.com"
    assert registrable_domain("http://127.0.0.1:8000") is None
    assert origin_url("http://www.buaa.edu.cn/a/b?x=1") == "http://www.buaa.edu.cn/"
    assert host_allowed("cs.tsinghua.edu.cn", ["tsinghua.edu.cn"])
    assert not host_allowed("evil-tsinghua.edu.cn", ["tsinghua.edu.cn"])


def test_prov_key_variants():
    assert prov_key("Xinjiang Uygur Autonomous Region") == prov_key("Xinjiang")
    assert prov_key("Guangxi Zhuang Autonomous Region") == prov_key("Guangxi")
    assert prov_key("Shaanxi") != prov_key("Shanxi")
    assert prov_key("Beijing Municipality") == prov_key("Beijing")


def test_moe_sample_provinces_and_codes():
    recs = moe.load_moe(FIX / "moe_sample.csv")
    by = {r.name_zh: r for r in recs}
    assert by["清华大学"].province == "Beijing" and by["清华大学"].external_id == "4111010003"
    assert by["清华大学"].city_zh == "北京市" and by["清华大学"].note.startswith("本科")
    tj = [r for r in recs if r.province == "Tianjin"]
    assert tj, "province header row for Tianjin not applied"


def test_campuschina_html_fixture():
    recs = {r.name_en: r for r in campuschina.parse_list_html((FIX / "campuschina_list_sample.html").read_text(encoding="utf-8"))}
    assert set(recs) == {"Anhui Normal University", "Beihang University", "Beijing Foreign Studies University"}  # truncated 4th skipped
    a = recs["Anhui Normal University"]
    assert (a.province, a.city, a.students, a.website) == ("Anhui", "Wuhu", 43800, "http://www.ahnu.edu.cn/")
    b = recs["Beihang University"]
    assert b.badges == ("211", "985") and b.website == "http://www.buaa.edu.cn" and b.external_id == "2999CA8A6E083864"
    assert recs["Beijing Foreign Studies University"].name_zh == "北京外国语大学"


def test_campuschina_json_skips_descriptions_and_html_wins():
    recs = campuschina.parse_names_json((FIX / "campuschina_names_sample.json").read_text(encoding="utf-8"))
    assert [r.name_en for r in recs] == ["Anhui Normal University", "Beihang University"]
    allr = campuschina.load_campuschina([FIX / "campuschina_names_sample.json", FIX / "campuschina_list_sample.html"])
    assert {r.name_en: r.website for r in allr}["Beihang University"] == "http://www.buaa.edu.cn"


def test_openalex_and_ror_pick_best():
    oc = openalex.parse_response(json.loads((FIX / "openalex_sample.json").read_text(encoding="utf-8")))
    p = pick_best(oc, "Tsinghua University", "清华大学")
    assert p.record and p.record.external_id == "I99999001" and not p.ambiguous   # US homonym and the Press are not picked
    assert p.record.ror_id == "03cve4549" and p.record.city == "Beijing"
    rc = ror.parse_response(json.loads((FIX / "ror_sample.json").read_text(encoding="utf-8")))
    p2 = pick_best(rc, "Tsinghua University", "清华大学")
    assert p2.record and p2.record.name_zh == "清华大学" and p2.record.website == "https://www.tsinghua.edu.cn/"


def test_pick_best_ambiguous_and_none():
    a = openalex.parse_institution({"id": "https://openalex.org/I1", "display_name": "Foo University", "country_code": "CN"})
    b = openalex.parse_institution({"id": "https://openalex.org/I2", "display_name": "Foo University", "country_code": "CN"})
    assert pick_best([a, b], "Foo University", None).ambiguous
    assert pick_best([], "Foo University", None).record is None
    c = openalex.parse_institution({"id": "https://openalex.org/I3", "display_name": "Bar College", "country_code": "CN"})
    assert pick_best([c], "Foo University", None).record is None


def _scenario(url_checks, existing=True, ror_site="https://www.tsinghua.edu.cn/"):
    seed = Seed("Tsinghua University", "清华大学", extra_email_domains=("cernet.edu.cn",))
    moe_recs = moe.load_moe(FIX / "moe_sample.csv")
    oc = openalex.parse_response(json.loads((FIX / "openalex_sample.json").read_text(encoding="utf-8")))
    rc = ror.parse_response(json.loads((FIX / "ror_sample.json").read_text(encoding="utf-8")))
    ex = [Existing("u1", "Tsinghua University", "10003", "https://www.campuschina.org/", None, None, None)] if existing else []
    return seed, merge_all([seed], moe_recs, [], {"tsinghuauniversity": oc}, {"tsinghuauniversity": rc}, ex, url_checks)[0]


def test_merge_verified_needs_live_url_and_two_sources():
    seed, m = _scenario({"https://www.tsinghua.edu.cn/": {"status": 200, "final_url": "https://www.tsinghua.edu.cn/"}})
    assert m.status == "verified", m.flags
    assert m.sources == ["moe", "ror", "openalex"] and m.name_zh_source.startswith("moe_exact+")
    assert (m.province, m.city, m.moe_code, m.openalex_id) == ("Beijing", "Beijing", "4111010003", "I99999001")
    assert m.official_url == "https://www.tsinghua.edu.cn/" and m.existing_official_url_is_placeholder
    row = to_row(m)
    assert set(row) == set(COLUMNS) and row["agency_equals_moe_code"] == "false"


def test_merge_without_url_check_is_needs_review():
    _, m = _scenario({})
    assert m.status == "needs_review" and "url_not_checked" in m.flags


def test_merge_failed_url_and_single_source():
    _, m = _scenario({"https://www.tsinghua.edu.cn/": {"status": 503}})
    assert m.status == "needs_review" and any(f.startswith("url_failed") for f in m.flags)


def test_merge_province_conflict_blocks_verified():
    seed = Seed("Tsinghua University", "清华大学")
    oc = openalex.parse_response(json.loads((FIX / "openalex_sample.json").read_text(encoding="utf-8")))
    oc[0] = openalex.parse_institution({**json.loads((FIX / "openalex_sample.json").read_text(encoding="utf-8"))["results"][0], "geo": {"city": "Beijing", "region": "Hebei", "country_code": "CN"}})
    m = merge_all([seed], moe.load_moe(FIX / "moe_sample.csv"), [], {"tsinghuauniversity": oc}, {}, [],
                  {"https://www.tsinghua.edu.cn/": {"status": 200, "final_url": "https://www.tsinghua.edu.cn/"}})[0]
    assert m.status == "needs_review" and any(f.startswith("province_conflict") for f in m.flags)


def test_merge_moe_missing_flagged_not_crash():
    m = merge_all([Seed("Nowhere University", "无此大学")], moe.load_moe(FIX / "moe_sample.csv"), [], {}, {}, [], {})[0]
    assert m.status == "unverified" and "moe_no_match" in m.flags


def test_campuschina_exact_match_counts_as_source_fuzzy_does_not():
    campus = campuschina.parse_list_html((FIX / "campuschina_list_sample.html").read_text(encoding="utf-8"))
    moe_recs = [moe.RegistryRecord(source="moe", external_id="1", name_zh="北京航空航天大学", province="Beijing", province_zh="北京市", city_zh="北京市")]
    exact = merge_all([Seed("Beihang University", "北京航空航天大学")], moe_recs, campus, {}, {}, [], {})[0]
    assert exact.sources == ["moe", "campuschina"] and exact.campuschina_key == "2999CA8A6E083864"
    assert exact.official_url == "http://www.buaa.edu.cn/" and exact.official_url_source == "campuschina"
    fuzzy = merge_all([Seed("Beihang Universityy", "北京航空航天大学")], moe_recs, campus, {}, {}, [], {})[0]
    assert "campuschina" not in fuzzy.sources and any(f.startswith("campuschina_fuzzy") for f in fuzzy.flags)


def test_domains_include_seed_and_registry():
    seed, m = _scenario({"https://www.tsinghua.edu.cn/": {"status": 200, "final_url": "https://www.tsinghua.edu.cn/"}})
    seed = Seed(seed.name, seed.name_zh, domains=("cs.tsinghua.edu.cn",), extra_email_domains=("cernet.edu.cn",))
    d = build_domains([m], [seed])["Tsinghua University"]
    assert d["domains"] == ["cs.tsinghua.edu.cn", "tsinghua.edu.cn"]
    assert d["email_domains"] == ["cernet.edu.cn", "tsinghua.edu.cn"]


# ---- seed file checks (run on the real config; the MOE check needs the real file in inputs/) ----
def test_seed_file_has_104_unique_entries_and_unique_chinese_names():
    seeds = load_seed(ROOT / "config" / "universities.yml")
    assert len(seeds) == 104 and len({s.name for s in seeds}) == 104
    assert all(s.name_zh for s in seeds) and len({s.name_zh for s in seeds}) == 104
    t = yaml.safe_load((ROOT / "config" / "universities.yml").read_text(encoding="utf-8"))["universities"]
    tsi = next(u for u in t if u["name"] == "Tsinghua University")
    assert "cernet.edu.cn" in tsi["extra_email_domains"]


def test_seed_matches_ingest_targets_when_present():
    p = ROOT / "inputs" / "ingest-targets.json"
    if not p.is_file():
        raise unittest.SkipTest("inputs/ingest-targets.json not present")
    want = json.loads(p.read_text(encoding="utf-8"))
    have = [s.name for s in load_seed(ROOT / "config" / "universities.yml")]
    # spacing/case differences (e.g. 'ElectricPower' vs 'Electric Power') are harmless: matching uses norm_en()
    assert [norm_en(x) for x in want] == [norm_en(x) for x in have]


def test_all_seed_nameZh_exist_exactly_once_in_real_moe_file():
    cands = [ROOT / "inputs" / n for n in ("moe_universities.xls", "moe_universities.csv")]
    p = next((c for c in cands if c.is_file()), None)
    if p is None:
        raise unittest.SkipTest("MOE file not in inputs/")
    idx = moe.index_by_name(moe.load_moe(p))
    for s in load_seed(ROOT / "config" / "universities.yml"):
        assert len(idx.get(s.name_zh, [])) == 1, (s.name, s.name_zh, len(idx.get(s.name_zh, [])))


def _oa_with_aliases(aliases):
    d = json.loads((FIX / "openalex_sample.json").read_text(encoding="utf-8"))["results"][0]
    d = {**d, "display_name_alternatives": aliases}
    return openalex.parse_institution(d)


def test_zh_conflict_only_when_moe_name_is_not_among_all_chinese_aliases():
    ok_checks = {"https://www.tsinghua.edu.cn/": {"status": 200, "final_url": "https://www.tsinghua.edu.cn/"}}
    seed, moe_recs = Seed("Tsinghua University", "清华大学"), moe.load_moe(FIX / "moe_sample.csv")
    # traditional form first, simplified second -> NOT a conflict
    m = merge_all([seed], moe_recs, [], {"tsinghuauniversity": [_oa_with_aliases(["清華大學", "清华大学"])]}, {}, [], ok_checks)[0]
    assert not any(f.startswith("name_zh_conflict") for f in m.flags), m.flags
    assert m.name_zh_source == "moe_exact+openalex" and m.status == "verified", (m.name_zh_source, m.flags)
    # only a different name -> conflict (and the registry is then NOT verified)
    m2 = merge_all([seed], moe_recs, [], {"tsinghuauniversity": [_oa_with_aliases(["清華大學", "THU Beijing"])]}, {}, [], ok_checks)[0]
    # the English name still matches, so the record is used, but its Chinese names do not include the MOE name
    assert any(f.startswith("name_zh_conflict") for f in m2.flags) and m2.status != "verified"


def test_diagnose_lists_unverified_and_url_reasons():
    seed, m = _scenario({"https://www.tsinghua.edu.cn/": {"status": None, "error": "ConnectTimeout"}})
    txt = diagnose([m], {"https://www.tsinghua.edu.cn/": {"status": None, "error": "ConnectTimeout"}})
    assert "NOT VERIFIED (1)" in txt and "ConnectTimeout" in txt and "url_failed" in txt


def test_urlcheck_tries_other_scheme_after_connection_error_only():
    import tempfile
    from profinder_pipeline.registry.urlcheck import check_urls

    class R:
        def __init__(self, code, url, text=""):
            self.status_code, self.url, self.text = code, url, text

    class C:
        def __init__(self, behaviour):
            self.calls, self.b = [], behaviour

        def get(self, url, **kw):
            self.calls.append(url)
            if url.endswith("/robots.txt"):
                return R(404, url, "<html>not found")
            out = self.b(url)
            if isinstance(out, Exception):
                raise out
            return out

    tmp = Path(tempfile.mkdtemp()) / "c.json"
    c = C(lambda u: ConnectionError() if u.startswith("https://") else R(200, "http://x.edu.cn/"))
    res = check_urls(["https://x.edu.cn/"], tmp, c, delay=0)
    assert res["https://x.edu.cn/"]["status"] == 200 and res["https://x.edu.cn/"]["final_url"] == "http://x.edu.cn/"
    c2 = C(lambda u: R(403, u))
    res2 = check_urls(["https://y.edu.cn/"], tmp, c2, delay=0)
    assert res2["https://y.edu.cn/"]["status"] == 403 and not any(x.startswith("http://y") for x in c2.calls)   # no swap after an HTTP status
    res3 = check_urls(["https://x.edu.cn/"], tmp, C(lambda u: R(500, u)), delay=0, retry_failed=True)   # 200 stays cached
    assert res3["https://x.edu.cn/"]["status"] == 200


def test_moe_municipality_city_overrides_administering_province():
    import tempfile
    rows = ("附件1：,,,,,,\n名单,,,,,,\n序号,学校名称,学校标识码,主管部门,所在地,办学层次,备注\n"
            "河北省（2所）,,,,,,\n1,河北工业大学,4113010080,河北省,天津市,本科,\n2,河北大学,4113010075,河北省,保定市,本科,\n")
    p = Path(tempfile.mkdtemp()) / "m.csv"
    p.write_text(rows, encoding="utf-8")
    by = {r.name_zh: r for r in moe.load_moe(p)}
    assert by["河北工业大学"].province == "Tianjin" and by["河北工业大学"].city_zh == "天津市"
    assert by["河北大学"].province == "Hebei"


def test_stdout_utf8_reconfigure_exists_in_main():
    import inspect
    from profinder_pipeline.registry import run
    assert "reconfigure" in inspect.getsource(run.main)