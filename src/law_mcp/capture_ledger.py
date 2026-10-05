"""Capture ledger — official response provenance anchors (P1-B/P1-C).

One canonical row per distinct response payload (``response_sha256``):

    {url, request, response_sha256, first_seen, retrieved_at,
     raw_path, bytes, observation_count}

Invariants:
- ``first_seen`` is IMMUTABLE once set. Re-recording the same payload never
  changes it (only ``retrieved_at`` and ``observation_count`` advance).
- A new payload gets its own row with ``first_seen == retrieved_at`` at first
  observation.
- Timestamps are observations of the caller's clock; see ``clock_source`` in
  the adapter meta (P1-C) — the ledger does not claim an attested clock.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class CaptureLedger:
    """Canonical-row-per-sha JSONL ledger with immutable ``first_seen``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows: dict[str, dict[str, Any]] = {}
        with self._locked():
            self._load()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # ponytail: one lock per ledger; use SQLite if full-file rewrites become costly.
        with self.path.with_suffix(self.path.suffix + ".lock").open("a+b") as lock:
            if sys.platform == "win32":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if sys.platform == "win32":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _load(self) -> None:
        self._rows = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._rows[row["response_sha256"]] = row

    def record(
        self,
        *,
        url: str,
        request: dict[str, Any],
        raw: bytes,
        retrieved_at: str | None = None,
        raw_path: str = "",
    ) -> dict[str, Any]:
        with self._locked():
            self._load()
            sha = hashlib.sha256(raw).hexdigest()
            now = retrieved_at or datetime.now(timezone.utc).isoformat()
            row = self._rows.get(sha)
            if row is None:
                row = {
                    "url": url,
                    "request": request,
                    "response_sha256": sha,
                    "first_seen": now,
                    "retrieved_at": now,
                    "raw_path": raw_path,
                    "bytes": len(raw),
                    "observation_count": 1,
                }
            else:
                # first_seen is immutable; only the observation metadata advances.
                row = {
                    **row,
                    "retrieved_at": now,
                    "observation_count": row.get("observation_count", 1) + 1,
                }
                if raw_path:
                    row["raw_path"] = raw_path
            self._rows[sha] = row
            self._flush()
            return dict(row)

    def seen(self, response_sha256: str) -> dict[str, Any] | None:
        with self._locked():
            self._load()
            row = self._rows.get(response_sha256)
            return dict(row) if row else None

    def rows(self) -> list[dict[str, Any]]:
        with self._locked():
            self._load()
            return [dict(r) for r in self._rows.values()]

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(self._rows[sha], ensure_ascii=False, sort_keys=True)
            for sha in sorted(self._rows)
        ]
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                         prefix=self.path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write("\n".join(lines) + ("\n" if lines else ""))
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                stream.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
