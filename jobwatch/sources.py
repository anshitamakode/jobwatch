"""Adapters for the public job feeds each ATS exposes.

Every adapter returns a list of Posting objects. All of these are public,
unauthenticated endpoints that the companies' own career pages call from the
browser -- we're just calling them directly instead of rendering the page.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

TIMEOUT = 25
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}


@dataclass
class Posting:
    source: str
    company: str
    job_id: str
    title: str
    location: str
    url: str
    posted_at: str | None = None
    description: str = ""

    @property
    def key(self) -> str:
        """Stable identity used for de-duplication across runs."""
        return f"{self.source}:{self.company}:{self.job_id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "company": self.company,
            "job_id": self.job_id,
            "title": self.title,
            "location": self.location,
            "url": self.url,
            "posted_at": self.posted_at,
        }


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|li|div|h\d|tr)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# --------------------------------------------------------------------------
# Greenhouse
# --------------------------------------------------------------------------
def fetch_greenhouse(entry: dict, sess: requests.Session) -> list[Posting]:
    token = entry["token"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    r = sess.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        if not loc and j.get("offices"):
            loc = ", ".join(o.get("name", "") for o in j["offices"])
        out.append(
            Posting(
                source="greenhouse",
                company=entry["name"],
                job_id=str(j.get("id")),
                title=j.get("title", ""),
                location=loc,
                url=j.get("absolute_url", ""),
                posted_at=j.get("first_published") or j.get("updated_at"),
                description=html_to_text(j.get("content")),
            )
        )
    return out


# --------------------------------------------------------------------------
# Lever
# --------------------------------------------------------------------------
def fetch_lever(entry: dict, sess: requests.Session) -> list[Posting]:
    token = entry["token"]
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    r = sess.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        posted = j.get("createdAt")
        if isinstance(posted, (int, float)):
            posted = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(posted / 1000))
        out.append(
            Posting(
                source="lever",
                company=entry["name"],
                job_id=str(j.get("id")),
                title=j.get("text", ""),
                location=cats.get("location") or cats.get("allLocations", [""])[0]
                if cats.get("allLocations")
                else cats.get("location", ""),
                url=j.get("hostedUrl", ""),
                posted_at=posted,
                description=j.get("descriptionPlain") or html_to_text(j.get("description")),
            )
        )
    return out


# --------------------------------------------------------------------------
# Ashby
# --------------------------------------------------------------------------
def fetch_ashby(entry: dict, sess: requests.Session) -> list[Posting]:
    token = entry["token"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    r = sess.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append(
            Posting(
                source="ashby",
                company=entry["name"],
                job_id=str(j.get("id")),
                title=j.get("title", ""),
                location=j.get("location") or "",
                url=j.get("jobUrl", ""),
                posted_at=j.get("publishedAt"),
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            )
        )
    return out


# --------------------------------------------------------------------------
# SmartRecruiters
# --------------------------------------------------------------------------
def fetch_smartrecruiters(entry: dict, sess: requests.Session) -> list[Posting]:
    token = entry["token"]
    out: list[Posting] = []
    offset = 0
    while True:
        url = (
            f"https://api.smartrecruiters.com/v1/companies/{token}"
            f"/postings?limit=100&offset={offset}"
        )
        r = sess.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data.get("content", [])
        for j in items:
            loc = j.get("location") or {}
            loc_str = ", ".join(
                x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x
            )
            out.append(
                Posting(
                    source="smartrecruiters",
                    company=entry["name"],
                    job_id=str(j.get("id")),
                    title=j.get("name", ""),
                    location=loc_str,
                    url=f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                    posted_at=j.get("releasedDate"),
                    description="",
                )
            )
        offset += len(items)
        if not items or offset >= data.get("totalFound", 0) or offset > 2000:
            break
    return out


# --------------------------------------------------------------------------
# Workday  (the important one -- NVIDIA, Adobe, Salesforce, Walmart, Target...)
# --------------------------------------------------------------------------
def fetch_workday(entry: dict, sess: requests.Session) -> list[Posting]:
    tenant = entry["tenant"]
    wd = entry.get("wd", "wd5")
    site = entry["site"]
    search_text = entry.get("search_text", "")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"

    out: list[Posting] = []
    offset = 0
    limit = 20
    total = None
    while True:
        payload = {
            "appliedFacets": entry.get("facets", {}),
            "limit": limit,
            "offset": offset,
            "searchText": search_text,
        }
        r = sess.post(
            api,
            json=payload,
            timeout=TIMEOUT,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        if total is None:
            total = data.get("total", 0)
        postings = data.get("jobPostings", [])
        for j in postings:
            path = j.get("externalPath", "")
            out.append(
                Posting(
                    source="workday",
                    company=entry["name"],
                    job_id=path or str(j.get("bulletFields", [""])[0]),
                    title=j.get("title", ""),
                    location=j.get("locationsText", ""),
                    url=f"{base}/en-US/{site}{path}",
                    # Workday gives relative strings like "Posted Today" --
                    # useful for display, useless for sorting. We rely on
                    # first-seen time from the local store instead.
                    posted_at=j.get("postedOn"),
                    description="",
                )
            )
        offset += len(postings)
        if not postings or offset >= (total or 0) or offset >= entry.get("max_jobs", 400):
            break
        time.sleep(0.3)
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
}


def fetch(entry: dict, sess: requests.Session | None = None) -> list[Posting]:
    sess = sess or _session()
    ats = entry["ats"]
    if ats not in FETCHERS:
        raise ValueError(f"unknown ats: {ats}")
    return FETCHERS[ats](entry, sess)
