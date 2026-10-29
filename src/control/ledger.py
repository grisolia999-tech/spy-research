"""Append-only, hash-chained trade ledger (logs/trades.jsonl).

One JSON object per line: seq, ts, prev (hash of previous line), event fields, hash.
Opened with O_APPEND and fsynced per line. `verify()` detects edited, removed or reordered lines.
The ledger only ever appends; nothing in this repo rewrites or truncates it.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, Tuple

GENESIS = "0" * 64


def _digest(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


class Ledger:
    def __init__(self, path, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.path, self._clock, self._lock = Path(path), clock, threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _tail(self) -> Tuple[int, str]:
        seq, h = 0, GENESIS
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    seq, h = rec["seq"], rec["hash"]
        return seq, h

    def append(self, event: str, **fields) -> dict:
        with self._lock:
            seq, prev = self._tail()
            body = {"seq": seq + 1, "ts": self._clock().isoformat(), "event": event, **fields, "prev": prev}
            rec = dict(body, hash=_digest(prev, body))
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (json.dumps(rec, sort_keys=True) + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            return rec

    def records(self) -> Iterator[dict]:
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    yield json.loads(line)

    def verify(self) -> Optional[str]:
        """Return None if the chain is intact, else a description of the first problem."""
        prev, n = GENESIS, 0
        for rec in self.records():
            n += 1
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec.get("seq") != n:
                return f"line {n}: sequence break"
            if rec.get("prev") != prev or _digest(prev, body) != rec.get("hash"):
                return f"line {n}: hash mismatch (ledger was edited)"
            prev = rec["hash"]
        return None
