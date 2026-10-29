"""Three-state trading switch stored in config/switch.json: off | paper | live.

Rules enforced here:
  * Missing, unreadable or invalid file => OFF. A `live` arm expires at `valid_until` => OFF.
  * Moving to `paper` or `live` requires actor "owner", which only ./arm (an interactive
    terminal session with CONFIRM for live) uses. The system may only ever move to OFF.
  * Every start calls reset_to_off(), so a crash, reboot or deploy always comes back OFF.
Agents and tools never edit this file; ./arm and ./halt are the only writers.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

MODES = ("off", "paper", "live")


class SwitchError(Exception):
    pass


class Switch:
    def __init__(self, path, ledger=None, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.path, self.ledger, self._clock = Path(path), ledger, clock

    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def mode(self) -> str:
        d = self._read()
        m = d.get("mode")
        if m not in MODES:
            return "off"
        vu = d.get("valid_until")
        if m == "live":
            try:
                if vu is None or self._clock() >= datetime.fromisoformat(vu):
                    return "off"
            except ValueError:
                return "off"
        return m

    def set(self, mode: str, actor: str, reason: str = "", valid_until: Optional[datetime] = None) -> None:
        if mode not in MODES:
            raise SwitchError(f"unknown mode {mode!r}")
        if mode != "off" and actor != "owner":
            raise SwitchError("only the owner (via ./arm) can enable paper or live")
        if actor not in ("owner", "system"):
            raise SwitchError(f"unknown actor {actor!r}")
        if mode == "live" and valid_until is None:
            raise SwitchError("live requires a valid_until time")
        old = self.mode()
        rec = {"mode": mode, "changed_at": self._clock().isoformat(), "changed_by": actor, "reason": reason,
               "valid_until": valid_until.isoformat() if valid_until else None}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=2))
        os.replace(tmp, self.path)
        if self.ledger:
            self.ledger.append("switch_change", mode=mode, previous=old, actor=actor, reason=reason,
                               valid_until=rec["valid_until"])

    def reset_to_off(self, reason: str) -> None:
        self.set("off", "system", reason)
