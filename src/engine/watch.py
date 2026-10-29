"""Schwab login watch: warn 24h before the refresh token expires; on expiry halt and say why."""
from __future__ import annotations

from datetime import datetime, timedelta

WARN_BEFORE_S = 24 * 3600


class TokenWatch:
    def __init__(self, auth, system, notifier, tz) -> None:
        self.auth, self.system, self.notifier, self.tz = auth, system, notifier, tz
        self._warned_on = None
        self._expired_notified_hour = None

    def check(self, now: datetime) -> str:
        """Returns 'ok', 'warned' or 'expired'."""
        try:
            left = self.auth.seconds_until_refresh_expiry(now.timestamp())
        except Exception as e:
            left = None
            err = str(e)
        else:
            err = "no saved login"
        if left is None or left <= 0:
            hour = now.replace(minute=0, second=0, microsecond=0)
            if self._expired_notified_hour != hour:
                self._expired_notified_hour = hour
                try:
                    cancelled = self.system.halt(f"Schwab login expired ({err})")
                except Exception as e:
                    cancelled = f"cancel failed: {e}"
                self.system.ledger.append("token_expired", detail=err, cancelled=cancelled)
                self.notifier.send("Trading HALTED", "Schwab login expired. Switch is OFF. Run ./reauth.")
            return "expired"
        if left <= WARN_BEFORE_S:
            day = now.astimezone(self.tz).date()
            if self._warned_on != day:
                self._warned_on = day
                hrs = left / 3600
                self.system.ledger.append("token_expiry_warning", hours_left=round(hrs, 1))
                self.notifier.send("Schwab login expires soon", f"About {hrs:.0f}h left. Run ./reauth.")
            return "warned"
        return "ok"
