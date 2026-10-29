"""Desktop notifications on macOS (osascript). Failure to notify is logged, never fatal."""
from __future__ import annotations

import subprocess
from typing import Callable


class Notifier:
    def __init__(self, ledger=None, runner: Callable = subprocess.run) -> None:
        self.ledger, self._run = ledger, runner

    def send(self, title: str, message: str) -> None:
        script = 'display notification "{}" with title "{}"'.format(
            message.replace('"', "'").replace("\\", ""), title.replace('"', "'").replace("\\", ""))
        try:
            self._run(["osascript", "-e", script], capture_output=True, timeout=10)
        except Exception as e:
            if self.ledger:
                self.ledger.append("notify_failed", title=title, error=str(e))
