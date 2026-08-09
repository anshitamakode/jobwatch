"""Decide whether a posting is worth waking you up for, and which resume to send.

Rules, in order:
  1. Global exclude terms in the title      -> drop
  2. Location doesn't match your filters    -> drop
  3. No profile's `title_any` hits          -> drop
  4. Profile-level exclude hits             -> drop that profile
  5. Score on `boost` terms; highest-scoring profile wins and picks the resume
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .sources import Posting


def _norm(s) -> str:
    # str() because YAML happily turns `- 2` and `- 3` into ints.
    # Collapsing runs of whitespace matters: punctuation becomes spaces, so
    # "Remote - India" and "Remote, India" would otherwise normalise to
    # different numbers of spaces and a single filter term couldn't match both.
    out = re.sub(r"[^a-z0-9+#/ ]+", " ", str(s or "").lower())
    return re.sub(r"\s+", " ", out).strip()


def _hits(terms: list[str], haystack: str) -> list[str]:
    found = []
    for t in terms:
        t_norm = _norm(t).strip()
        if not t_norm:
            continue
        # word-boundary match so "go" doesn't fire on "google"
        if re.search(rf"(?<![a-z0-9]){re.escape(t_norm)}(?![a-z0-9])", haystack):
            found.append(t)
    return found


@dataclass
class Match:
    posting: Posting
    profile: str
    resume: str
    score: int
    reasons: list[str]


class Matcher:
    def __init__(self, config: dict):
        f = config.get("filters", {})
        self.global_exclude = f.get("exclude_titles", [])
        self.location_any = f.get("location_any", [])
        self.location_required = f.get("location_required", True)
        self.min_score = f.get("min_score", 0)
        self.profiles = config.get("profiles", [])

    def _location_ok(self, posting: Posting) -> bool:
        if not self.location_any:
            return True
        loc = _norm(posting.location)
        if not loc.strip():
            # Workday sometimes returns an empty location. Keep it rather than
            # silently dropping a real Bangalore role; you'll see it flagged.
            return not self.location_required
        return bool(_hits(self.location_any, loc))

    def match(self, posting: Posting) -> Match | None:
        title = _norm(posting.title)
        body = _norm(posting.description)[:20000]
        blob = f"{title} {body}"

        if _hits(self.global_exclude, title):
            return None
        if not self._location_ok(posting):
            return None

        best: Match | None = None
        for p in self.profiles:
            if not _hits(p.get("title_any", []), title):
                continue
            if _hits(p.get("exclude", []), blob):
                continue

            reasons = []
            score = 10  # base for a title hit
            # Lets a specialist profile win over a generic one. "Staff Software
            # Engineer - AI Engineer" hits both profiles; without this, a
            # Java-heavy description drags it to the backend resume.
            score += p.get("title_match_bonus", 0)
            title_boosts = _hits(p.get("boost", []), title)
            body_boosts = [b for b in _hits(p.get("boost", []), body) if b not in title_boosts]
            score += 6 * len(title_boosts)
            score += 2 * len(body_boosts)
            reasons = title_boosts + body_boosts

            for term in _hits(p.get("seniority_boost", []), title):
                score += 4
                reasons.append(str(term))

            if best is None or score > best.score:
                best = Match(
                    posting=posting,
                    profile=p["name"],
                    resume=p["resume"],
                    score=score,
                    reasons=[str(r) for r in reasons[:8]],
                )

        if best and best.score >= self.min_score:
            return best
        return None


def dedupe(matches: list[Match]) -> list[Match]:
    """Collapse the same role posted many times.

    Tide lists one job three times with different location groupings; Tekion
    posts five identical 'Senior Software Engineer' reqs. Same company + same
    title = one alert, keeping the highest-scoring copy.
    """
    best: dict[tuple[str, str], Match] = {}
    for m in matches:
        k = (m.posting.company.lower(), _norm(m.posting.title).strip())
        if k not in best or m.score > best[k].score:
            best[k] = m
    return sorted(best.values(), key=lambda m: -m.score)