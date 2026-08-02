"""Remembers which postings we've already alerted on.

Deliberately a plain JSON file: it commits cleanly in a GitHub Actions
workflow, you can eyeball it, and you can delete a line to re-alert on a job.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path


class Store:
    def __init__(self, path: str | Path, retain_days: int = 90):
        self.path = Path(path)
        self.retain_days = retain_days
        self.data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text() or "{}")
            except json.JSONDecodeError:
                self.data = {}

    def is_new(self, key: str) -> bool:
        return key not in self.data

    def remember(self, key: str, meta: dict) -> None:
        self.data[key] = {
            "first_seen": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            **meta,
        }

    def prune(self) -> int:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=self.retain_days)
        drop = []
        for k, v in self.data.items():
            try:
                seen = dt.datetime.fromisoformat(v.get("first_seen", ""))
            except ValueError:
                continue
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=dt.timezone.utc)
            if seen < cutoff:
                drop.append(k)
        for k in drop:
            del self.data[k]
        return len(drop)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, indent=1, sort_keys=True)
        # atomic write so a crashed run can't corrupt the file
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent))
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
        os.replace(tmp, self.path)

    def __len__(self) -> int:
        return len(self.data)
