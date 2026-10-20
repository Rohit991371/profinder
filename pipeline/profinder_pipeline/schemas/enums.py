"""Controlled vocabularies. Values mirror doc 01 section 6 and doc 02 section 2; the SQL CHECK constraints in
db/migrations/001_ingest_schema.sql must contain the same strings (a test enforces this)."""
from enum import StrEnum


class EntityType(StrEnum):
    university = "university"
    professor = "professor"
    program = "program"
    scholarship = "scholarship"
    publication = "publication"


class ProposedStatus(StrEnum):
    verified = "verified"
    needs_review = "needs_review"
    unverified = "unverified"
    outdated = "outdated"          # proposal only (doc 02 section 7)


class Decision(StrEnum):
    auto_accept = "auto_accept"
    needs_review = "needs_review"
    rejected = "rejected"
    human_approved = "human_approved"
    human_rejected = "human_rejected"


class Action(StrEnum):
    insert = "insert"
    enrich = "enrich"
    propose = "propose"
    noop = "noop"


class WriteMode(StrEnum):
    additive = "additive"
    insert_only = "insert-only"    # no delete mode exists, by design


class Extractor(StrEnum):
    css = "css"
    llm = "llm"
    api = "api"
    manual = "manual"


class RoleClass(StrEnum):
    faculty = "faculty"
    non_faculty = "non_faculty"
    unknown = "unknown"


class DataStatus(StrEnum):
    """Professor / University / Scholarship `dataStatus` (lowercase). Legacy 'needs_verification' is read, never written."""
    verified = "verified"
    unverified = "unverified"
    needs_review = "needs_review"
    outdated = "outdated"


class VerificationStatus(StrEnum):
    """`verificationStatus` (UPPERCASE) on all four entities."""
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    PENDING_REVIEW = "PENDING_REVIEW"
    POTENTIALLY_OUTDATED = "POTENTIALLY_OUTDATED"


class TargetKind(StrEnum):
    faculty_list = "faculty_list"
    faculty_profile = "faculty_profile"
    admissions = "admissions"
    programs = "programs"
    scholarship = "scholarship"
    registry = "registry"


class TargetStatus(StrEnum):
    pending = "pending"
    ok = "ok"
    failed = "failed"
    blocked = "blocked"
    no_page_found = "no_page_found"


class RobotsStatus(StrEnum):
    allowed = "allowed"
    disallowed = "disallowed"
    unknown = "unknown"


class DiscoveredBy(StrEnum):
    seed = "seed"
    link_follow = "link_follow"
    search = "search"
    manual = "manual"


class RenderMode(StrEnum):
    static = "static"
    playwright = "playwright"


class RunKind(StrEnum):
    registry = "registry"
    discover = "discover"
    crawl = "crawl"
    extract = "extract"
    release = "release"
    load = "load"


class RunStatus(StrEnum):
    running = "running"
    ok = "ok"
    failed = "failed"
    partial = "partial"


class ReleaseStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    loaded = "loaded"
    abandoned = "abandoned"


# The only research areas a professor may be linked to (the app's 24 ResearchArea rows).
RESEARCH_AREAS = (
    "AI Security", "Algorithms", "Artificial Intelligence", "Blockchain Security", "Cloud Security",
    "Computer Networks", "Computer Science", "Computer Vision", "Cryptography", "Cybersecurity",
    "Data Science", "Databases", "Deep Learning", "Digital Forensics", "Distributed Systems",
    "Generative AI", "Human-Computer Interaction", "Machine Learning", "Natural Language Processing",
    "Network Security", "Operating Systems", "Privacy", "Robotics", "Software Engineering",
)
