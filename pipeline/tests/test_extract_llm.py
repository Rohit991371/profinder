"""Groq extractors with a fake transport (no network). Needs pydantic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import json

from profinder_pipeline.extract import llm as llm_mod
from profinder_pipeline.extract.llm import InterestsOut, LlmPerson, PeopleOut, ask_model, interests_from_text, people_from_markdown, schema_of
from profinder_pipeline.extract.llm_core import GroqClient, TokenLimiter


def _client(replies, tmp_path):
    calls = []

    def transport(payload, headers, timeout):
        calls.append(payload)
        text = replies.pop(0)
        return 200, {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 50, "completion_tokens": 20}}, {}
    t = [0.0]
    cl = GroqClient(api_key="k", model="m", transport=transport, limiter=TokenLimiter(clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)),
                    log_path=tmp_path / "log.jsonl", sleep=lambda s: None)
    return cl, calls


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_schemas_have_no_free_form_dicts():
    for model in (InterestsOut, PeopleOut):
        for node in _walk(schema_of(model)):
            if node.get("type") == "object":
                assert "properties" in node and node.get("additionalProperties") is False


def test_interests_grounded_cached_and_ungrounded_dropped(tmp_path):
    llm_mod.LLM_CACHE_DIR = tmp_path / "cache"
    page = "陈榕，主要研究领域为操作系统、分布式系统等基础系统软件。在操作系统领域发表论文"
    good = json.dumps({"research_interests": "操作系统、分布式系统等基础系统软件", "evidence_quote": "主要研究领域为操作系统"}, ensure_ascii=False)
    cl, calls = _client([good], tmp_path)
    assert interests_from_text(cl, page, page, "u")[0] == "操作系统、分布式系统等基础系统软件"
    assert interests_from_text(cl, page, page, "u")[0] == "操作系统、分布式系统等基础系统软件" and len(calls) == 1      # second time: cache, no call
    made_up = json.dumps({"research_interests": "量子计算", "evidence_quote": None}, ensure_ascii=False)
    cl2, _ = _client([made_up], tmp_path)
    assert interests_from_text(cl2, "另一段文字 abc", "另一段文字 abc", "u2") == (None, None)                          # not on the page


def test_repair_retry_after_invalid_json(tmp_path):
    cl, calls = _client(["I cannot do that", json.dumps({"research_interests": None, "evidence_quote": None})], tmp_path)
    out = ask_model(cl, InterestsOut, "s", "u", "interests", "r")
    assert out.research_interests is None and len(calls) == 2 and "previous reply" in calls[1]["messages"][1]["content"]


def test_people_in_chunks_and_failed_chunk_counted(tmp_path):
    llm_mod.LLM_CACHE_DIR = tmp_path / "cache2"
    person = {"name_zh": "王明", "name_en": None, "position": "教授", "email": "wm@x.edu.cn", "profile_url": None, "department": None}
    text = "\n\n".join(f"- 王明{i} 教授 wm{i}@x.edu.cn " + "x" * 400 for i in range(12))
    replies = [json.dumps({"people": [person]}), "totally broken", "still broken", json.dumps({"people": [person, person]})]
    cl, _ = _client(replies, tmp_path)
    people, failed = people_from_markdown(cl, text, "ref", chunk_chars=2500)
    assert failed == 1 and len(people) == 3 and all(isinstance(p, LlmPerson) for p in people)
