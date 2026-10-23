"""Groq extractors (task 4.2). Strict Pydantic output (no dict fields), JSON repair + one repair retry, disk cache so a re-run
never spends tokens twice. Every value coming back from the model is grounded against the page text before it is used.
Model name: GROQ_MODEL from .env (a Preview model on this account; it can be discontinued at short notice)."""
import json
from pathlib import Path
from typing import Any, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ..paths import INPUTS_DIR, OUTPUT_DIR
from ..utils.keys import md5_hex
from ..validate.grounding import norm_text
from .llm_core import GroqClient, LlmError, chunk_by_items, repair_json, strip_wrappers

LLM_LOG = OUTPUT_DIR / "extract" / "llm_calls.jsonl"
LLM_CACHE_DIR = INPUTS_DIR / "cache" / "llm"
INTERESTS_PROMPT_VERSION = "interests-v1"
PEOPLE_PROMPT_VERSION = "people-v1"
T = TypeVar("T", bound=BaseModel)


class InterestsOut(BaseModel):
    """Schema sent to Groq: all fields required but nullable (strict mode)."""
    model_config = ConfigDict(extra="forbid")
    research_interests: Optional[str]
    evidence_quote: Optional[str]


class LlmPerson(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_zh: Optional[str]
    name_en: Optional[str]
    position: Optional[str]
    email: Optional[str]
    profile_url: Optional[str]
    department: Optional[str]


class PeopleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    people: list[LlmPerson]


def _strip_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def schema_of(model: type[BaseModel]) -> dict[str, Any]:
    return _strip_titles(model.model_json_schema())


INTERESTS_SYSTEM = (
    "You extract data from a Chinese university faculty profile text. Reply with JSON only. "
    "research_interests: copy the person's own research-interest text from the text verbatim "
    "(keep the original language, do not translate, summarise or rephrase). Use null if the text does not state research interests. "
    "evidence_quote: one short exact quote (max 200 characters) from the text that supports it, or null."
)
PEOPLE_SYSTEM = (
    "You extract the people listed in a university faculty directory text. Reply with JSON only: {\"people\": [...]}. "
    "One entry per person who appears in the text. Copy values exactly as written; never invent or guess; use null for anything not stated. "
    "name_zh: Chinese name; name_en: English/romanised name if written; position: the title as written (e.g. 教授); "
    "email: exactly as written; profile_url: the person's link if shown; department: the unit heading if shown."
)


def build_client(log_path: Path = LLM_LOG) -> GroqClient:
    from ..settings import get_settings   # lazy: keeps this module importable without a configured .env
    s = get_settings()
    key = getattr(s, "groq_api_key", None)
    model = getattr(s, "groq_model", None)
    if not key or not model:
        raise LlmError("not_configured", "GROQ_API_KEY and GROQ_MODEL must be set in .env")
    return GroqClient(api_key=key.get_secret_value() if hasattr(key, "get_secret_value") else str(key), model=model, log_path=log_path)


def _parse(model: type[T], text: str) -> T:
    fixed = repair_json(text)
    if fixed is None:
        raise ValueError("no JSON found in the reply")
    return model.model_validate_json(fixed)


def ask_model(client: GroqClient, model: type[T], system: str, user: str, purpose: str, ref: str, max_tokens: int = 1500) -> T:
    """One call + one repair retry. Raises LlmError / ValueError when the reply cannot be turned into `model`."""
    schema = schema_of(model)
    reply = client.chat_json(system, user, schema, purpose, purpose, ref, max_tokens)
    try:
        return _parse(model, reply.text)
    except (ValueError, ValidationError) as exc:
        repair_user = (f"{user}\n\nYour previous reply was not valid for the required JSON schema ({str(exc)[:200]}). "
                       f"Reply again with ONLY the corrected JSON.")
        reply2 = client.chat_json(system, repair_user, schema, purpose + "_repair", purpose, ref, max_tokens)
        return _parse(model, reply2.text)


# ------------------------------------------------------------------------------------------------ disk cache
def _cache_path(kind: str, version: str, model: str, text: str) -> Path:
    return LLM_CACHE_DIR / f"{kind}_{md5_hex(version + '|' + model + '|' + text)}.json"


def _cache_read(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def _cache_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------------------------------------ interests
def interests_from_text(client: GroqClient, text: str, page_text: str, ref: str) -> tuple[Optional[str], Optional[str]]:
    """-> (interests, quote). The interests must be found in `page_text` (whitespace/width-insensitive) or the result is dropped."""
    text = (text or "").strip()
    if not text:
        return None, None
    path = _cache_path("interests", INTERESTS_PROMPT_VERSION, client.model, text)
    cached = _cache_read(path)
    if cached is None:
        try:
            out = ask_model(client, InterestsOut, INTERESTS_SYSTEM, f"Text:\n{text}", "interests", ref, max_tokens=500)
        except (LlmError, ValueError, ValidationError):
            return None, None
        cached = out.model_dump()
        _cache_write(path, cached)
    interests = (cached.get("research_interests") or "").strip() or None
    if interests and norm_text(interests) not in norm_text(page_text):
        return None, None                                         # not grounded: dropped
    return interests, cached.get("evidence_quote")


# ------------------------------------------------------------------------------------------------ unknown layouts
def people_from_markdown(client: GroqClient, markdown: str, ref: str, chunk_chars: int = 3500) -> tuple[list[LlmPerson], int]:
    """Fallback for sites without a config: chunk the list text by items, one call per chunk. -> (people, failed_chunks).
    The caller grounds every person against the page text."""
    people: list[LlmPerson] = []
    failed = 0
    for i, chunk in enumerate(chunk_by_items(markdown, chunk_chars)):
        path = _cache_path("people", PEOPLE_PROMPT_VERSION, client.model, chunk)
        cached = _cache_read(path)
        if cached is None:
            try:
                out = ask_model(client, PeopleOut, PEOPLE_SYSTEM, f"Directory text:\n{chunk}", "people", f"{ref}#chunk{i}", max_tokens=2500)
            except (LlmError, ValueError, ValidationError):
                failed += 1
                continue
            cached = out.model_dump()
            _cache_write(path, cached)
        people.extend(LlmPerson.model_validate(p) for p in cached.get("people", []))
    return people, failed
