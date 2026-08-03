from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

import yaml

from . import discover, notify
from .matcher import Matcher, dedupe
from .sources import Posting, _session, fetch
from .store import Store

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path: Path) -> dict | list:
    if not path.exists():
        return {} if path.name == "config.yaml" else []
    return yaml.safe_load(path.read_text()) or ({} if path.name == "config.yaml" else [])


def save_companies(path: Path, companies: list) -> None:
    path.write_text(yaml.safe_dump(companies, sort_keys=False, allow_unicode=True))


# --------------------------------------------------------------------------
def cmd_poll(args) -> int:
    cfg = load_yaml(args.config)
    companies = load_yaml(args.companies)
    if not companies:
        print("No companies configured. Add one with:  jobwatch add <job-url>")
        return 1

    matcher = Matcher(cfg)
    store = Store(args.state, retain_days=cfg.get("retain_days", 90))
    first_run = len(store) == 0

    sess = _session()
    all_new: list = []
    errors: list[tuple[str, str]] = []
    total_seen = 0

    for entry in companies:
        if entry.get("paused"):
            continue
        try:
            postings = fetch(entry, sess)
        except Exception as e:  # one bad company must not kill the run
            errors.append((entry.get("name", "?"), f"{type(e).__name__}: {e}"))
            continue

        total_seen += len(postings)
        for p in postings:
            if not store.is_new(p.key):
                continue
            m = matcher.match(p)
            store.remember(p.key, p.as_dict())
            if m:
                all_new.append(m)
        time.sleep(cfg.get("politeness_seconds", 1.0))

    all_new = dedupe(all_new)
    store.prune()
    store.save()

    if first_run and not args.notify_on_first_run:
        print(
            f"Baseline run: indexed {total_seen} existing postings across "
            f"{len(companies)} companies. {len(all_new)} would have matched.\n"
            "Alerts start from the next run so you don't get spammed with a "
            "backlog. (Use --notify-on-first-run to override.)"
        )
        if args.verbose:
            notify.print_console(all_new)
    elif all_new:
        notify.dispatch(all_new, cfg, quiet=args.quiet)
        print(f"{len(all_new)} new match(es) out of {total_seen} postings scanned.")
    else:
        print(f"No new matches. Scanned {total_seen} postings across {len(companies)} companies.")

    for name, err in errors:
        print(f"  ! {name}: {err}", file=sys.stderr)
    if errors and len(errors) == len(companies):
        return 1
    return 0


# --------------------------------------------------------------------------
def cmd_add(args) -> int:
    try:
        entry = discover.parse(args.url)
    except discover.UnknownATS as e:
        print(f"✗ {e}")
        return 1

    if args.name:
        entry["name"] = args.name

    print(f"Detected: {entry['ats']} → {entry}")
    print("Verifying the feed responds...")
    try:
        postings = fetch(entry, _session())
    except Exception as e:
        print(f"✗ Feed request failed: {type(e).__name__}: {e}")
        print("  Not adding. Double-check the URL, or the board may be private.")
        return 1

    if not postings:
        print("✗ Feed responded but returned 0 postings.")
        print("  Usually means the page renders its job list in JavaScript,")
        print("  so there is nothing to read server-side. Not adding -- an")
        print("  empty feed costs runtime on every poll and can never match.")
        return 1

    print(f"✓ {len(postings)} postings visible.")
    for p in postings[:5]:
        print(f"    - {p.title}  [{p.location}]")

    companies = load_yaml(args.companies)
    if any(
        c.get("ats") == entry["ats"]
        and c.get("token", c.get("tenant")) == entry.get("token", entry.get("tenant"))
        for c in companies
    ):
        print("Already in companies.yaml — nothing to do.")
        return 0
    companies.append(entry)
    save_companies(args.companies, companies)
    print(f"✓ Added '{entry['name']}' to {args.companies}")
    return 0


# --------------------------------------------------------------------------
def cmd_check(args) -> int:
    """Dry run: fetch everything, show what WOULD match, touch no state."""
    cfg = load_yaml(args.config)
    companies = load_yaml(args.companies)
    matcher = Matcher(cfg)
    sess = _session()
    matches, scanned = [], 0
    for entry in companies:
        if entry.get("paused"):
            continue
        try:
            postings = fetch(entry, sess)
        except Exception as e:
            print(f"  ! {entry.get('name')}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        scanned += len(postings)
        for p in postings:
            m = matcher.match(p)
            if m:
                matches.append(m)
        time.sleep(0.5)
    deduped = dedupe(matches)
    notify.print_console(deduped)
    print(
        f"\n{len(deduped)} unique match ({len(matches)} before dedupe) "
        f"/ {scanned} scanned. (Dry run — state untouched.)"
    )
    return 0


# --------------------------------------------------------------------------
def cmd_prune(args) -> int:
    """Drop companies whose feed is dead or returns nothing.

    Empty feeds are pure cost: polled every run, incapable of ever matching.
    """
    companies = load_yaml(args.companies)
    sess = _session()
    keep, drop = [], []
    for entry in companies:
        name = entry.get("name", "?")
        try:
            postings = fetch(entry, sess)
        except Exception as e:
            drop.append((name, f"{type(e).__name__}"))
            continue
        if not postings:
            drop.append((name, "0 postings"))
        else:
            keep.append(entry)
            print(f"  ✓ {name:<22} {len(postings)}")
        time.sleep(0.3)

    for name, why in drop:
        print(f"  ✗ {name:<22} {why}")

    if not drop:
        print("\nNothing to prune.")
        return 0
    if args.dry_run:
        print(f"\n{len(drop)} would be removed, {len(keep)} kept. (Dry run.)")
        return 0
    save_companies(args.companies, keep)
    print(f"\nRemoved {len(drop)}, kept {len(keep)}.")
    return 0


# --------------------------------------------------------------------------
def cmd_test_notify(args) -> int:
    cfg = load_yaml(args.config)
    from .matcher import Match

    fake = Match(
        posting=Posting(
            source="workday",
            company="nvidia",
            job_id="/job/test",
            title="Senior Software Engineer, Deep Learning",
            location="Bengaluru, India",
            url="https://example.com/job",
            description="",
        ),
        profile="ai_ml",
        resume="Resume_Anshita_Makode_AI_ML.pdf",
        score=42,
        reasons=["llm", "python", "senior"],
    )
    notify.dispatch([fake], cfg)
    print("Sent a test alert. If nothing arrived, check your env vars.")
    return 0


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jobwatch", description="Instant ATS job alerts.")
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--companies", type=Path, default=ROOT / "companies.yaml")
    ap.add_argument("--state", type=Path, default=ROOT / "state" / "seen.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("poll", help="Fetch, diff against state, alert on new matches.")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--notify-on-first-run", action="store_true")
    p.set_defaults(func=cmd_poll)

    p = sub.add_parser("add", help="Add a company from any job/careers URL.")
    p.add_argument("url")
    p.add_argument("--name", help="Override the display name.")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("check", help="Dry run: show current matches, change nothing.")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("prune", help="Remove companies with dead or empty feeds.")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("test-notify", help="Send one fake alert to verify delivery.")
    p.set_defaults(func=cmd_test_notify)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())