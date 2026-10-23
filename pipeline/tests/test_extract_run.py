"""Offline end-to-end of extract_university with a temporary page cache (no network, no database). Needs pydantic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import dataclasses
import json

from profinder_pipeline.extract import report as report_mod
from profinder_pipeline.extract import run as run_mod
from profinder_pipeline.extract.sitecfg import load_site_configs
from profinder_pipeline.fetch.cache import PageCache
from extract_helpers import ECNU, FIX, SJTU, TSINGHUA, html


def _setup(tmp_path, monkeypatch=None):
    run_mod.PAGE_CACHE_DIR = tmp_path / "pages"
    run_mod.OUT_DIR = tmp_path / "out"
    report_mod.OUT_DIR = tmp_path / "out"
    cache = PageCache(run_mod.PAGE_CACHE_DIR)
    cfgs = load_site_configs()
    for uni, fx in ((TSINGHUA, "tsinghua_list.html"), (ECNU, "ecnu_list.html"), (SJTU, "sjtu_list_rendered.html")):
        cache.write(cfgs[uni].list_url, cfgs[uni].list_url, html(fx), 200, "static", "utf-8", "h")
    for url, fx in (("https://www.cs.sjtu.edu.cn/jiaoshiml/chenrong.html", "sjtu_profile_chen_rong.html"),
                    ("https://www.cs.tsinghua.edu.cn/info/1111/3490.htm", "tsinghua_profile_feng_jianhua.html")):
        cache.write(url, url, html(fx), 200, "static", "utf-8", "h")
    return cfgs


def test_end_to_end_counts_and_outputs(tmp_path):
    cfgs = _setup(tmp_path)
    t = run_mod.extract_university(cfgs[TSINGHUA], None, False, 0)
    assert t["decisions"] == {"auto_accept": 115, "needs_review": 8, "rejected": 12} and t["pending_profile"] == 0
    first = t["_candidates"][0].payload
    assert first.researchInterests == "数据库管理系统，数据安全与隐私保护，信息检索" and first.researchAreas == ["Databases", "Privacy"]
    rows = [json.loads(ln) for ln in (tmp_path / "out" / "tsinghua.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 135
    e = run_mod.extract_university(cfgs[ECNU], None, False, 0)
    assert e["decisions"] == {"auto_accept": 62, "needs_review": 11, "rejected": 2}


def test_profile_required_site_waits_for_pages(tmp_path):
    cfgs = _setup(tmp_path)
    s = run_mod.extract_university(cfgs[SJTU], None, False, 0)
    assert s["people_parsed"] == 290 and s["candidates"] == 1 and s["pending_profile"] == 289    # only one profile page cached


def test_layout_change_sends_everything_to_review(tmp_path):
    cfgs = _setup(tmp_path)
    broken = dataclasses.replace(cfgs[TSINGHUA], list_spec={**cfgs[TSINGHUA].list_spec, "min_items": 1000})
    r = run_mod.extract_university(broken, None, False, 0)
    assert r["decisions"]["auto_accept"] == 0 and r["decisions"]["needs_review"] > 0


def test_count_drift_flag_on_second_run(tmp_path):
    cfgs = _setup(tmp_path)
    run_mod.extract_university(cfgs[TSINGHUA], None, False, 0)
    stats = json.loads((tmp_path / "out" / "tsinghua_stats.json").read_text(encoding="utf-8"))
    stats["people_parsed"] = 300                                     # pretend the previous run saw 300 people
    (tmp_path / "out" / "tsinghua_stats.json").write_text(json.dumps(stats), encoding="utf-8")
    r = run_mod.extract_university(cfgs[TSINGHUA], None, False, 0)
    assert r["decisions"]["auto_accept"] == 0


def test_recall_report_logic(tmp_path):
    gt = {"records": {"Tsinghua University": [
        {"name": "冯建华", "position": "教授", "email": "fengjh@tsinghua.edu.cn", "profileUrl": "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm"},
        {"name": "已离职", "position": "教授", "email": "gone@tsinghua.edu.cn", "profileUrl": "https://www.cs.tsinghua.edu.cn/info/1111/9999.htm"},
        {"name": "李山山", "position": "工程师", "email": "x@tsinghua.edu.cn", "profileUrl": "https://www.cs.tsinghua.edu.cn/info/1111/8888.htm"}]}}
    gtp = tmp_path / "gt.json"
    gtp.write_text(json.dumps(gt, ensure_ascii=False), encoding="utf-8")
    report_mod.GROUND_TRUTH = gtp
    cands = [{"decision": "auto_accept", "payload": {"nameZh": "冯建华", "profileUrl": "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm", "email": "fengjh@tsinghua.edu.cn"}}]
    page = "冯建华 /info/1111/3490.htm"
    r = report_mod.recall_vs_ground_truth(cands, page)
    assert r["faculty"] == 2 and r["found"] == 1 and len(r["absent_from_page"]) == 1 and r["extractor_missed"] == []
    assert r["recall_present"] == 1.0 and r["recall_all"] == 0.5 and r["email_same"] == 1
    r2 = report_mod.recall_vs_ground_truth([], page)
    assert r2["found"] == 0 and len(r2["extractor_missed"]) == 1 and r2["recall_present"] == 0.0     # name is on the page but not extracted: a real miss
