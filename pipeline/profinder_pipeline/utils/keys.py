"""MD5 identity keys (doc 01 section 9). Parts are joined with '|'. `university_ref` is the app University.id once
known, otherwise the normalised university name; use the same convention for a given university within one run."""
import hashlib
from typing import Optional

from .text import normalise_key_text
from .urls import canonical_url


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()  # identity key / cache key only, not security


def url_hash(url: str) -> str:
    """Page-cache key: MD5 of the canonical URL."""
    return md5_hex(canonical_url(url))


def university_key(agency_number: Optional[str], name: str) -> str:
    return md5_hex((agency_number or "").strip() or normalise_key_text(name))


def professor_key(university_ref: str, profile_url: Optional[str], profile_is_personal: bool,
                  name_zh: Optional[str], name_romanised: Optional[str], department: Optional[str]) -> str:
    if profile_is_personal and profile_url:
        return md5_hex(f"{university_ref}|{canonical_url(profile_url)}")
    who = normalise_key_text(name_zh) or normalise_key_text(name_romanised)
    if not who:
        raise ValueError("professor_key needs a profile URL, nameZh or a romanised name")
    return md5_hex(f"{university_ref}|{who}|{normalise_key_text(department)}")


def program_key(university_ref: str, degree: str, major: str) -> str:
    return md5_hex(f"{university_ref}|{normalise_key_text(degree)}|{normalise_key_text(major)}")


def scholarship_key(university_ref: str, name: str) -> str:
    return md5_hex(f"{university_ref}|{normalise_key_text(name)}")
