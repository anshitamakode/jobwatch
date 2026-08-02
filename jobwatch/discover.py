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

    # ---- SmartRecruiters -------------------------------------------------
    if "smartrecruiters.com" in host:
        if parts:
            return {"name": parts[0], "ats": "smartrecruiters", "token": parts[0]}
        raise UnknownATS("SmartRecruiters URL with no company name in the path.")

    raise UnknownATS(
        f"Couldn't recognise the ATS behind {host}.\n"
        "Supported: Workday, Greenhouse, Lever, Ashby, SmartRecruiters.\n"
        "Tip: on the company's careers page, click a job and use the URL you "
        "land on -- most companies redirect to their real ATS host."
    )
