"""Offline tests. No network: every ATS response is a fixture.

Run:  python -m pytest tests -q     (or)     python tests/test_smoke.py
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from jobwatch import discover, sources
from jobwatch.matcher import Matcher
from jobwatch.sources import Posting
from jobwatch.store import Store

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())


class FakeResp:
    def __init__(self, payload, ok=True, status=200):
        self._p, self.ok, self.status_code, self.text = payload, ok, status, "{}"

    def json(self):
        return self._p

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


# --------------------------------------------------------------------------
def test_url_discovery():
    cases = [
        (
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/"
            "India%2C-Bengaluru/Senior-Software-Engineer--Cloud-Platform_JR2010936/apply",
            {"ats": "workday", "tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite"},
        ),
        (
            "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs",
            {"ats": "workday", "tenant": "nvidia", "site": "NVIDIAExternalCareerSite"},
        ),
        (
            "https://job-boards.greenhouse.io/databricks/jobs/7891234",
            {"ats": "greenhouse", "token": "databricks"},
        ),
        ("https://jobs.lever.co/dream11/abc-123", {"ats": "lever", "token": "dream11"}),
        ("https://jobs.ashbyhq.com/openai/xyz", {"ats": "ashby", "token": "openai"}),
        (
            "https://jobs.smartrecruiters.com/Visa/744000",
            {"ats": "smartrecruiters", "token": "Visa"},
        ),
    ]
    for url, expected in cases:
        got = discover.parse(url)
        for k, v in expected.items():
            assert got[k] == v, f"{url}: {k} -> {got[k]!r} != {v!r}"

    # Unknown hosts no longer raise -- they fall back to the generic JSON-LD
    # scraper, which `add` then verifies by actually fetching the page.
    for other in ["https://careers.google.com/jobs/results/123", "https://example.com"]:
        assert discover.parse(other)["ats"] == "jsonld", other
    print("✓ url discovery")


# --------------------------------------------------------------------------
def test_workday_fetch():
    pages = [
        FakeResp(
            {
                "total": 3,
                "jobPostings": [
                    {
                        "title": "Senior Software Engineer, Deep Learning",
                        "externalPath": "/job/India-Bengaluru/Sr-SWE-DL_JR100",
                        "locationsText": "Bengaluru, India",
                        "postedOn": "Posted Today",
                        "bulletFields": ["JR100"],
                    },
                    {
                        "title": "ASIC Verification Engineer",
                        "externalPath": "/job/India-Pune/ASIC_JR101",
                        "locationsText": "Pune, India",
                        "postedOn": "Posted Today",
                        "bulletFields": ["JR101"],
                    },
                ],
            }
        ),
        FakeResp(
            {
                "total": 3,
                "jobPostings": [
                    {
                        "title": "Senior Software Engineer, Cloud Platform",
                        "externalPath": "/job/India-Bengaluru/Cloud_JR102",
                        "locationsText": "Bengaluru, India",
                        "postedOn": "Posted Today",
                        "bulletFields": ["JR102"],
                    }
                ],
            }
        ),
    ]
    sess = mock.Mock()
    sess.post.side_effect = pages
    entry = {
        "name": "nvidia",
        "ats": "workday",
        "tenant": "nvidia",
        "wd": "wd5",
        "site": "NVIDIAExternalCareerSite",
    }
    out = sources.fetch_workday(entry, sess)
    assert len(out) == 3, len(out)
    assert out[0].url == (
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
        "/job/India-Bengaluru/Sr-SWE-DL_JR100"
    ), out[0].url
    assert out[0].key == "workday:nvidia:/job/India-Bengaluru/Sr-SWE-DL_JR100"
    print("✓ workday fetch + pagination + url construction")


def test_greenhouse_fetch():
    sess = mock.Mock()
    sess.get.return_value = FakeResp(
        {
            "jobs": [
                {
                    "id": 7891234,
                    "title": "Software Engineer, Backend",
                    "location": {"name": "Bengaluru, India"},
                    "absolute_url": "https://boards.greenhouse.io/databricks/jobs/7891234",
                    "updated_at": "2026-08-03T04:00:00Z",
                    "content": "&lt;p&gt;You will build &lt;strong&gt;Java&lt;/strong&gt; "
                    "microservices on Kafka.&lt;/p&gt;",
                }
            ]
        }
    )
    out = sources.fetch_greenhouse({"name": "databricks", "token": "databricks"}, sess)
    assert len(out) == 1
    assert "Java microservices on Kafka" in out[0].description, out[0].description
    print("✓ greenhouse fetch + html unescape")


# --------------------------------------------------------------------------
def test_matcher():
    m = Matcher(CFG)

    def mk(title, loc="Bengaluru, India", desc=""):
        return Posting("workday", "nvidia", title, title, loc, "u", None, desc)

    # should match, AI/ML resume
    r = m.match(mk("Senior Software Engineer, Deep Learning",
                   desc="LLM agents RAG in Python with PyTorch"))
    assert r and r.profile == "ai_ml", r
    assert r.resume.endswith("AI_ML.pdf")

    # should match, backend resume
    r = m.match(mk("Senior Java Backend Engineer",
                   desc="Spring Boot microservices Kafka distributed systems"))
    assert r and r.profile == "backend_java", r
    assert r.resume == "Resume_Anshita_Makode.pdf"

    # below min_score: a bare title with no real signal
    assert m.match(mk("Software Engineer 3")) is None

    # explicitly unwanted
    assert m.match(mk("Senior Software Engineer, Cloud Platform")) is None
    assert m.match(mk("Site Reliability Engineer")) is None
    assert m.match(mk("Engineering Manager, Backend")) is None
    assert m.match(mk("ASIC Verification Engineer")) is None
    assert m.match(mk("Software Engineering Intern")) is None

    # wrong geography
    assert m.match(mk("Senior ML Engineer", loc="Santa Clara, CA")) is None

    # empty location survives (location_required: false)
    assert m.match(mk("Senior ML Engineer, LLM",
                      desc="RAG agents pytorch python", loc="")) is not None

    # word-boundary sanity: "go" must not fire on "Google"
    from jobwatch.matcher import _hits, _norm

    assert _hits(["go"], _norm("Google Cloud")) == []
    assert _hits(["java"], _norm("JavaScript developer")) == []

    print("✓ matcher: routing, exclusions, geography, word boundaries")


# --------------------------------------------------------------------------
def test_store_dedup():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "seen.json"
        s = Store(p)
        assert s.is_new("a")
        s.remember("a", {"title": "x"})
        s.save()
        s2 = Store(p)
        assert not s2.is_new("a")
        assert s2.is_new("b")

        # aged entries get pruned
        s2.data["old"] = {"first_seen": "2020-01-01T00:00:00+00:00"}
        assert s2.prune() == 1
        assert "a" in s2.data
    print("✓ store: persistence, dedup, pruning")


def test_config_is_valid_yaml():
    assert CFG["profiles"] and CFG["filters"]
    names = {p["name"] for p in CFG["profiles"]}
    assert names == {"ai_ml", "backend_java"}, names
    print("✓ config parses")




# --------------------------------------------------------------------------
def test_new_adapters():
    from jobwatch import sources as S

    # Workable
    sess = mock.Mock()
    sess.get.return_value = FakeResp(
        {"jobs": [{"shortcode": "ABC1", "title": "Backend Engineer",
                   "city": "Bengaluru", "country": "India",
                   "url": "https://apply.workable.com/acme/j/ABC1/",
                   "description": "&lt;p&gt;Java and Kafka&lt;/p&gt;"}]}
    )
    out = S.fetch_workable({"name": "acme", "token": "acme"}, sess)
    assert out[0].location == "Bengaluru, India", out[0].location
    assert "Java and Kafka" in out[0].description

    # Recruitee
    sess = mock.Mock()
    sess.get.return_value = FakeResp(
        {"offers": [{"id": 77, "title": "ML Engineer", "city": "Bangalore",
                     "country": "India",
                     "careers_url": "https://acme.recruitee.com/o/ml"}]}
    )
    out = S.fetch_recruitee({"name": "acme", "token": "acme"}, sess)
    assert out[0].key == "recruitee:acme:77", out[0].key

    # Eightfold
    sess = mock.Mock()
    sess.get.return_value = FakeResp(
        {"count": 1, "positions": [
            {"id": 55, "name": "SDE 3", "location": "Bangalore, India",
             "canonicalPositionUrl": "https://x.eightfold.ai/careers/job?id=55"}]}
    )
    out = S.fetch_eightfold({"name": "flipkart", "token": "flipkart"}, sess)
    assert out[0].title == "SDE 3"
    print("✓ workable / recruitee / eightfold adapters")


def test_jsonld_generic():
    from jobwatch import sources as S

    html = '''
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"JobPosting",
     "title":"Senior AI Engineer","datePosted":"2026-08-03",
     "identifier":{"@type":"PropertyValue","value":"REQ-991"},
     "url":"https://careers.acme.com/jobs/991",
     "description":"<p>Build <b>LLM</b> agents.</p>",
     "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
        "addressLocality":"Bengaluru","addressCountry":"India"}}}
    </script>
    <script type="application/ld+json">
    {"@type":"ItemList","itemListElement":[
      {"@type":"JobPosting","title":"Backend Engineer",
       "url":"https://careers.acme.com/jobs/992",
       "jobLocation":{"address":{"addressLocality":"Bangalore"}}}]}
    </script>
    </head></html>'''

    class R:
        text = html
        ok = True
        def raise_for_status(self): pass

    sess = mock.Mock()
    sess.get.return_value = R()
    out = S.fetch_jsonld({"name": "acme", "url": "https://careers.acme.com"}, sess)
    assert len(out) == 2, [p.title for p in out]
    assert out[0].job_id == "REQ-991", out[0].job_id
    assert out[0].location == "Bengaluru, India", out[0].location
    assert "LLM" in out[0].description
    # nested inside ItemList must still be found
    assert out[1].title == "Backend Engineer"
    print("✓ generic JSON-LD scraper (incl. nested ItemList)")


def test_new_url_patterns():
    cases = [
        ("https://apply.workable.com/acme/j/ABC1/", {"ats": "workable", "token": "acme"}),
        ("https://acme.recruitee.com/o/be", {"ats": "recruitee", "token": "acme"}),
        ("https://flipkart.eightfold.ai/careers/job?id=1",
         {"ats": "eightfold", "token": "flipkart"}),
        ("https://careers.phonepe.com/jobs/1", {"ats": "jsonld"}),
        ("https://www.zoho.com/careers/", {"ats": "jsonld"}),
    ]
    for url, exp in cases:
        got = discover.parse(url)
        for k, v in exp.items():
            assert got[k] == v, f"{url}: {k}={got[k]!r} != {v!r}"
    # known ATSs must still win over the fallback
    assert discover.parse(
        "https://job-boards.greenhouse.io/tide/jobs/1")["ats"] == "greenhouse"
    print("✓ new url patterns + fallback ordering")


if __name__ == "__main__":
    for fn in [
        test_config_is_valid_yaml,
        test_url_discovery,
        test_workday_fetch,
        test_greenhouse_fetch,
        test_matcher,
        test_store_dedup,
        test_new_adapters,
        test_jsonld_generic,
        test_new_url_patterns,
    ]:
        fn()
    print("\nAll good.")