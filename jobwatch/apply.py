"""Interactive apply workflow.

Cannot submit applications for you -- no ATS exposes a public submission
endpoint, and browser automation against Workday/Greenhouse breaks constantly
and violates their terms. What it CAN do is remove every step between
"there's a match" and "I'm looking at the form with the right resume ready":

  - works through matches highest-score first
  - opens each posting in your browser
  - reveals the correct resume in Finder, ready to drag into the upload field
  - copies the file path to your clipboard as a fallback
  - remembers what you applied to, so you never open the same job twice

Pair with Simplify or JobWizard for the actual field filling.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .matcher import Match


class AppliedLog:
    """Separate from seen.json: 'I saw this' and 'I applied' are different."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text() or "{}")
            except json.JSONDecodeError:
                self.data = {}

    def status(self, key: str) -> str | None:
        return (self.data.get(key) or {}).get("status")

    def mark(self, key: str, status: str, m: Match) -> None:
        import datetime as dt

        self.data[key] = {
            "status": status,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "title": m.posting.title,
            "company": m.posting.company,
            "url": m.posting.url,
            "resume": m.resume,
            "score": m.score,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.data.values():
            out[v.get("status", "?")] = out.get(v.get("status", "?"), 0) + 1
        return out


def _mac(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except Exception:
        return False


def open_url(url: str, browser: str | None = None) -> None:
    """Open in `browser` if named, else whatever the OS default is.

    On macOS `open <url>` hands off to the default browser (often Safari).
    Passing -a targets a specific app without changing your system default.
    """
    if sys.platform == "darwin":
        if browser:
            if _mac(["open", "-a", browser, url]):
                return
            print(f"    ! couldn't open {browser}, falling back to default")
        _mac(["open", url])
    elif sys.platform.startswith("linux"):
        _mac(["xdg-open", url])
    else:
        print(f"    open manually: {url}")


def reveal_resume(path: Path) -> None:
    """Select the file in Finder so it's one drag away from the upload field."""
    if not path.exists():
        print(f"    ! resume not found: {path}")
        return
    if sys.platform == "darwin":
        _mac(["open", "-R", str(path)])
        # clipboard fallback for file-picker dialogs
        try:
            subprocess.run(["pbcopy"], input=str(path).encode(), check=True)
        except Exception:
            pass


def run(
    matches: list[Match],
    resume_dir: Path,
    log: AppliedLog,
    limit: int,
    browser: str | None = None,
) -> None:
    todo = [m for m in matches if not log.status(m.posting.key)]
    if not todo:
        print("Nothing new to apply to. All current matches already logged.")
        return

    print(f"{len(todo)} unapplied match(es). Working through the top {min(limit, len(todo))}.")
    print("For each: [enter] applied · s skip · l later · q quit\n")

    done = 0
    for m in todo[:limit]:
        p = m.posting
        print("─" * 64)
        print(f"[{m.score:>3}] {p.title}")
        print(f"      {p.company} · {p.location}")
        print(f"      resume: {m.resume}")
        print(f"      {p.url}")

        open_url(p.url, browser)
        reveal_resume(resume_dir / m.resume)

        try:
            ans = input("      > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nStopping.")
            break

        if ans == "q":
            break
        elif ans == "s":
            log.mark(p.key, "skipped", m)
        elif ans == "l":
            continue  # leave unlogged so it comes back next time
        else:
            log.mark(p.key, "applied", m)
            done += 1

    log.save()
    c = log.counts()
    print(
        f"\n{done} applied this session. "
        f"Totals: {c.get('applied', 0)} applied, {c.get('skipped', 0)} skipped."
    )