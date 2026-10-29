"""Builds 1-minute bars from timestamped underlying prices seen by the engine."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from data.bars import Bar


class BarBuilder:
    def __init__(self, tz, step: timedelta = timedelta(minutes=1)) -> None:
        self.tz, self.step = tz, step
        self.bars: List[Bar] = []
        self._cur: Optional[Bar] = None

    def add(self, ts: datetime, price: float) -> None:
        ts = ts.astimezone(self.tz)
        start = ts.replace(second=0, microsecond=0)
        if self._cur is None or start > self._cur.start:
            if self._cur is not None:
                self.bars.append(self._cur)
            self._cur = Bar(start, price, price, price, price)
        elif start == self._cur.start:
            c = self._cur
            self._cur = Bar(c.start, c.open, max(c.high, price), min(c.low, price), price)

    def completed(self, now: datetime) -> List[Bar]:
        """Bars whose minute has ended by `now` (the in-progress bar is excluded)."""
        cutoff = now.astimezone(self.tz)
        done = list(self.bars)
        if self._cur is not None and self._cur.start + self.step <= cutoff:
            done.append(self._cur)
        return done
