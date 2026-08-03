"""Turn a career-page or job-posting URL into a config entry.

This is the piece that means you never have to guess an API token or a
Workday tenant number. Copy any job link, run `jobwatch add <url>`, and it
works out which ATS it is and what the feed parameters are.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

LOCALE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,4})?$")


class UnknownATS(Exception):
    pass


def parse(url: str) -> dict:
    u = urlparse(url if "://" in url else "https://" + url)
    host = (u.netloc or "").lower()
    parts = [p for p in (u.path or "").split("/") if p]

    # ---- Workday ---------------------------------------------------------
    # https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/...
    # https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs
    m = re.match(r"^(?P<tenant>[^.]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com$", host)
    if m:
        tenant, wd = m.group("tenant"), m.group("wd")
        site = None
        if len(parts) >= 4 and parts[0] == "wday" and parts[1] == "cxs":
            tenant, site = parts[2], parts[3]
        else:
            for p in parts:
                if LOCALE.match(p):
                    continue
                site = p
                break
        if not site:
            raise UnknownATS(
                "Found a Workday host but no career-site name in the path. "
                "Open the company's job search page and copy that URL instead."
            )
        return {
            "name": tenant,
            "ats": "workday",
            "tenant": tenant,
            "wd": wd,
            "site": site,
        }

    # ---- Greenhouse ------------------------------------------------------
    # boards.greenhouse.io/<token>/jobs/123 | job-boards.greenhouse.io/<token>/jobs/123
    if "greenhouse.io" in host:
        if parts:
            return {"name": parts[0], "ats": "greenhouse", "token": parts[0]}
        raise UnknownATS("Greenhouse URL with no board token in the path.")

    # ---- Lever -----------------------------------------------------------
    if "lever.co" in host:
        if parts:
            return {"name": parts[0], "ats": "lever", "token": parts[0]}
        raise UnknownATS("Lever URL with no company token in the path.")

    # ---- Ashby -----------------------------------------------------------
    if "ashbyhq.com" in host:
        if parts:
            return {"name": parts[0], "ats": "ashby", "token": parts[0]}
        raise UnknownATS("Ashby URL with no board name in the path.")

    # ---- Workable --------------------------------------------------------
    # apply.workable.com/<token>/  |  <token>.workable.com
    if "workable.com" in host:
        if host.startswith("apply.") and parts:
            return {"name": parts[0], "ats": "workable", "token": parts[0]}
        sub = host.split(".")[0]
        if sub not in ("www", "apply"):
            return {"name": sub, "ats": "workable", "token": sub}
        raise UnknownATS("Workable URL with no account name.")

    # ---- Recruitee -------------------------------------------------------
    m = re.match(r"^(?P<t>[^.]+)\.recruitee\.com$", host)
    if m:
        return {"name": m.group("t"), "ats": "recruitee", "token": m.group("t")}

    # ---- Eightfold -------------------------------------------------------
    m = re.match(r"^(?P<t>[^.]+)\.eightfold\.ai$", host)
    if m:
        t = m.group("t")
        return {"name": t, "ats": "eightfold", "token": t, "domain": f"{t}.com"}

    # ---- SmartRecruiters -------------------------------------------------
    if "smartrecruiters.com" in host:
        if parts:
            return {"name": parts[0], "ats": "smartrecruiters", "token": parts[0]}
        raise UnknownATS("SmartRecruiters URL with no company name in the path.")

    # ---- Fallback: treat it as a careers page with JSON-LD ---------------
    # Most career pages embed schema.org JobPosting markup because Google
    # requires it for job indexing. Works regardless of the ATS underneath.
    return {
        "name": re.sub(r"^(www|careers|jobs|carrers)\.", "", host).split(".")[0],
        "ats": "jsonld",
        "url": url if "://" in url else "https://" + url,
    }