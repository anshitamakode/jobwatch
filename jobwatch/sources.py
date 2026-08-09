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


# --------------------------------------------------------------------------
# Workable  (very common for startups)
# --------------------------------------------------------------------------
def fetch_workable(entry: dict, sess: requests.Session) -> list[Posting]:
    token = entry["token"]
    url = f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
    r = sess.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = ", ".join(
            x for x in [j.get("city"), j.get("state"), j.get("country")] if x
        ) or j.get("location", "")
        out.append(
            Posting(
                source="workable",
                company=entry["name"],
                job_id=str(j.get("shortcode") or j.get("id")),
                title=j.get("title", ""),
                location=loc,
                url=j.get("url") or j.get("application_url", ""),
                posted_at=j.get("published_on") or j.get("created_at"),
                description=html_to_text(j.get("description")),
            )
        )
    return out


# --------------------------------------------------------------------------
# Recruitee
# --------------------------------------------------------------------------
def fetch_recruitee(entry: dict, sess: requests.Session) -> list[Posting]:
    token = entry["token"]
    url = f"https://{token}.recruitee.com/api/offers/"
    r = sess.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("offers", []):
        loc = ", ".join(x for x in [j.get("city"), j.get("country")] if x) or j.get(
            "location", ""
        )
        out.append(
            Posting(
                source="recruitee",
                company=entry["name"],
                job_id=str(j.get("id")),
                title=j.get("title", ""),
                location=loc,
                url=j.get("careers_url") or j.get("careers_apply_url", ""),
                posted_at=j.get("published_at"),
                description=html_to_text(j.get("description")),
            )
        )
    return out


# --------------------------------------------------------------------------
# Eightfold  (used by several large Indian enterprises)
# --------------------------------------------------------------------------
def fetch_eightfold(entry: dict, sess: requests.Session) -> list[Posting]:
    token = entry["token"]
    domain = entry.get("domain", f"{token}.com")
    out: list[Posting] = []
    start = 0
    while True:
        url = (
            f"https://{token}.eightfold.ai/api/apply/v2/jobs"
            f"?domain={domain}&start={start}&num=100"
        )
        r = sess.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        positions = data.get("positions", [])
        for j in positions:
            loc = j.get("location") or ", ".join(j.get("locations", []) or [])
            out.append(
                Posting(
                    source="eightfold",
                    company=entry["name"],
                    job_id=str(j.get("id")),
                    title=j.get("name", ""),
                    location=loc,
                    url=j.get("canonicalPositionUrl")
                    or f"https://{token}.eightfold.ai/careers/job?id={j.get('id')}",
                    posted_at=str(j.get("t_create", "")) or None,
                    description=html_to_text(j.get("job_description")),
                )
            )
        start += len(positions)
        if not positions or start >= data.get("count", 0) or start >= entry.get(
            "max_jobs", 500
        ):
            break
        time.sleep(0.3)
    return out


# --------------------------------------------------------------------------
# Generic: schema.org JobPosting embedded as JSON-LD
#
# Google requires this markup to index a job in Google Jobs, so most career
# pages carry it regardless of what ATS is underneath. This is the catch-all
# for companies running their own portal.
# --------------------------------------------------------------------------
def _walk_jsonld(node, found: list) -> None:
    if isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "JobPosting" in types:
            found.append(node)
        for v in node.values():
            _walk_jsonld(v, found)
    elif isinstance(node, list):
        for v in node:
            _walk_jsonld(v, found)


def fetch_jsonld(entry: dict, sess: requests.Session) -> list[Posting]:
    import json as _json

    url = entry["url"]
    r = sess.get(url, timeout=TIMEOUT, headers={"Accept": "text/html,*/*"})
    r.raise_for_status()
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        r.text,
        re.S | re.I,
    )
    found: list = []
    for b in blocks:
        try:
            _walk_jsonld(_json.loads(b.strip()), found)
        except (ValueError, TypeError):
            continue

    out = []
    for j in found:
        loc = ""
        jl = j.get("jobLocation")
        jl = jl[0] if isinstance(jl, list) and jl else jl
        if isinstance(jl, dict):
            addr = jl.get("address") or {}
            if isinstance(addr, dict):
                loc = ", ".join(
                    x
                    for x in [
                        addr.get("addressLocality"),
                        addr.get("addressRegion"),
                        addr.get("addressCountry")
                        if isinstance(addr.get("addressCountry"), str)
                        else (addr.get("addressCountry") or {}).get("name"),
                    ]
                    if x
                )
        ident = j.get("identifier")
        if isinstance(ident, dict):
            ident = ident.get("value")
        job_url = j.get("url") or j.get("sameAs") or url
        out.append(
            Posting(
                source="jsonld",
                company=entry["name"],
                job_id=str(ident or job_url or j.get("title")),
                title=j.get("title", ""),
                location=loc,
                url=job_url,
                posted_at=j.get("datePosted"),
                description=html_to_text(j.get("description")),
            )
        )
    return out


# Registered here rather than in the literal above because these adapters are
# defined further down the file.
FETCHERS.update(
    {
        "workable": fetch_workable,
        "recruitee": fetch_recruitee,
        "eightfold": fetch_eightfold,
        "jsonld": fetch_jsonld,
    }
)


# --------------------------------------------------------------------------
# Oracle Recruiting Cloud (Oracle HCM)
#
# Used by many large enterprises -- Amex, Expedia, banks, telecoms. Like
# Workday, every company runs its own instance, so each is added separately.
# The site code is the CX_n in the careers URL path.
# --------------------------------------------------------------------------
def fetch_oraclecloud(entry: dict, sess: requests.Session) -> list[Posting]:
    host = entry["host"]
    site = entry.get("site", "CX_1")
    keyword = entry.get("keyword", "")
    base = f"https://{host}"
    out: list[Posting] = []
    offset = 0
    limit = 200
    total = None

    while True:
        finder = (
            f"findReqs;siteNumber={site},limit={limit},offset={offset},"
            "sortBy=POSTING_DATES_DESC"
        )
        if keyword:
            finder += f",keyword={keyword}"
        url = (
            f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList.secondaryLocations"
            f"&finder={finder}"
        )
        r = sess.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        items = r.json().get("items") or []
        if not items:
            break
        block = items[0]
        if total is None:
            total = block.get("TotalJobsCount") or block.get("SearchHitCount") or 0
        reqs = block.get("requisitionList") or []
        for j in reqs:
            jid = str(j.get("Id") or j.get("RequisitionNumber") or "")
            loc = j.get("PrimaryLocation") or ""
            secondary = j.get("secondaryLocations") or []
            if secondary:
                extra = ", ".join(
                    x.get("Name", "") for x in secondary if isinstance(x, dict)
                )
                loc = f"{loc}, {extra}" if loc else extra
            out.append(
                Posting(
                    source="oraclecloud",
                    company=entry["name"],
                    job_id=jid,
                    title=j.get("Title", ""),
                    location=loc,
                    url=f"{base}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}",
                    posted_at=j.get("PostedDate"),
                    description=html_to_text(j.get("ShortDescriptionStr")),
                )
            )
        offset += len(reqs)
        if not reqs or offset >= (total or 0) or offset >= entry.get("max_jobs", 600):
            break
        time.sleep(0.3)
    return out


FETCHERS["oraclecloud"] = fetch_oraclecloud


# --------------------------------------------------------------------------
# Keka  (very common among Indian product companies and startups)
#
# Keka's careers pages are server-rendered, so the listing page is scraped
# rather than hit via API. Job detail pages carry the full description.
# --------------------------------------------------------------------------
def fetch_keka(entry: dict, sess: requests.Session) -> list[Posting]:
    tenant = entry["tenant"]
    base = f"https://{tenant}.keka.com"

    # The JSON API is the happy path; the HTML listing is the fallback.
    for api in (
        f"{base}/careers/api/embedjobs",
        f"{base}/careers/api/jobs",
    ):
        try:
            r = sess.get(api, timeout=TIMEOUT)
            if r.ok and "json" in r.headers.get("Content-Type", ""):
                data = r.json()
                rows = data if isinstance(data, list) else (
                    data.get("data") or data.get("jobs") or []
                )
                if rows:
                    out = []
                    for j in rows:
                        jid = str(j.get("id") or j.get("jobId") or "")
                        out.append(
                            Posting(
                                source="keka",
                                company=entry["name"],
                                job_id=jid,
                                title=j.get("title") or j.get("jobTitle", ""),
                                location=j.get("location")
                                or j.get("locationName", ""),
                                url=f"{base}/careers/jobdetails/{jid}",
                                posted_at=j.get("createdOn") or j.get("postedOn"),
                                description=html_to_text(
                                    j.get("description") or j.get("jobDescription")
                                ),
                            )
                        )
                    return out
        except Exception:
            pass

    # Fallback: scrape job ids out of the listing page.
    r = sess.get(f"{base}/careers/", timeout=TIMEOUT,
                 headers={"Accept": "text/html,*/*"})
    r.raise_for_status()
    ids = dict.fromkeys(re.findall(r"/careers/jobdetails/(\d+)", r.text))
    out = []
    for jid in list(ids)[: entry.get("max_jobs", 100)]:
        url = f"{base}/careers/jobdetails/{jid}"
        try:
            d = sess.get(url, timeout=TIMEOUT,
                         headers={"Accept": "text/html,*/*"})
            text = html_to_text(d.text)
        except Exception:
            text = ""
        # first non-empty line after the company name is the title
        lines = [x.strip() for x in text.split("\n") if x.strip()]
        title = lines[1] if len(lines) > 1 else ""
        location = lines[2] if len(lines) > 2 else ""
        out.append(
            Posting(
                source="keka",
                company=entry["name"],
                job_id=jid,
                title=title,
                location=location,
                url=url,
                description=text[:8000],
            )
        )
        time.sleep(0.2)
    return out


# --------------------------------------------------------------------------
# Rippling ATS  (ats.rippling.com)
# --------------------------------------------------------------------------
def fetch_ripplingats(entry: dict, sess: requests.Session) -> list[Posting]:
    token = entry["token"]
    for api in (
        f"https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs",
        f"https://ats.rippling.com/api/v1/board/{token}/jobs",
    ):
        try:
            r = sess.get(api, timeout=TIMEOUT)
            if not r.ok:
                continue
            data = r.json()
            rows = data if isinstance(data, list) else (
                data.get("jobs") or data.get("data") or []
            )
            if not rows:
                continue
            out = []
            for j in rows:
                jid = str(j.get("uuid") or j.get("id") or "")
                loc = j.get("workLocation") or j.get("location") or ""
                if isinstance(loc, dict):
                    loc = loc.get("label") or loc.get("name", "")
                out.append(
                    Posting(
                        source="ripplingats",
                        company=entry["name"],
                        job_id=jid,
                        title=j.get("name") or j.get("title", ""),
                        location=loc,
                        url=f"https://ats.rippling.com/{token}/jobs/{jid}",
                        posted_at=j.get("createdAt"),
                        description=html_to_text(
                            j.get("description") or j.get("jobDescription")
                        ),
                    )
                )
            return out
        except Exception:
            continue
    raise RuntimeError("no working Rippling ATS endpoint")
FETCHERS["keka"] = fetch_keka
FETCHERS["ripplingats"] = fetch_ripplingats