"""Policy retrieval. Retriever is a Protocol so pgvector can replace the in-memory version."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from app.contracts import DetectedException, ExceptionType, PolicyCitation

DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "policies" / "time_attendance_policy.md"
)
_HEADING_RE = re.compile(r"^## (POL-TA-\d+) (.+)$", re.MULTILINE)
_STOPWORDS = frozenset(
    "a an and are as at be by can for from has have if in is it of on or per than that the "
    "to with no not each any".split()
)

_QUERY_BY_TYPE = {
    ExceptionType.MISSED_PUNCH: "missed punch clock-in without clock-out, correct clock-out "
    "to scheduled shift end, shift hours",
    ExceptionType.OVERTIME_RISK: "overtime risk, daily overtime hours, weekly overtime beyond "
    "40 hours, projected hours, reduce or reassign upcoming shifts",
    ExceptionType.SCHEDULE_CONFLICT: "schedule conflict, overlapping shifts, reassign shift "
    "with manager approval",
    ExceptionType.POLICY_BREACH: "policy breach, meal break, unpaid break minutes, day over "
    "12 hours needs manager review, minimum rest between shifts",
}


class Retriever(Protocol):
    def retrieve(self, query: str, k: int = 3) -> list[PolicyCitation]: ...


def build_query(exc: DetectedException) -> str:
    return _QUERY_BY_TYPE[exc.type]


def _tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in _STOPWORDS:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


class InMemoryRetriever:
    """Splits the policy markdown on '## POL-TA-' headings and scores each clause by the
    fraction of query words found in it (keyword overlap)."""

    def __init__(self, policy_path: Path | str | None = None, text: str | None = None):
        raw = text if text is not None else Path(policy_path or DEFAULT_POLICY_PATH).read_text()
        self.clauses: list[tuple[str, str]] = []  # (clause_id, "title\nbody")
        matches = list(_HEADING_RE.finditer(raw))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            body = raw[m.end() : end].strip()
            self.clauses.append((m.group(1), f"{m.group(2)}. {body}"))

    def retrieve(self, query: str, k: int = 3) -> list[PolicyCitation]:
        q = _tokens(query)
        if not q:
            return []
        scored = [
            PolicyCitation(
                clause_id=cid, quote=text, score=round(len(q & _tokens(text)) / len(q), 4)
            )
            for cid, text in self.clauses
        ]
        scored = [c for c in scored if c.score > 0]
        scored.sort(key=lambda c: (-c.score, c.clause_id))
        return scored[:k]


# TODO(Harsh): PgVectorRetriever(Retriever): embed the query with fastembed
# (EMBEDDING_MODEL) and run a cosine-similarity search on the pgvector policy_chunks table
# (schema agreed with Raman). It must return the same list[PolicyCitation] shape.
