#!/bin/sh
# One-time setup on the Mac Pro. Creates the launchd job definition and the two Desktop buttons.
# It does NOT start trading, load the job, or touch the switch.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
PY=$(command -v python3 || true)
[ -n "$PY" ] || { echo "python3 not found"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "Need Python 3.11 or newer (found: $("$PY" -V))."; exit 1; }
LABEL=com.grisolia.spy-trading
DIR="$HOME/.spy-research/launchd"
PLIST="$DIR/$LABEL.plist"
DESK="${DESKTOP_DIR:-$HOME/Desktop}"
mkdir -p "$DIR" "$REPO/logs" "$REPO/data" "$DESK"

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>-m</string><string>engine.main</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>$REPO/src</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$REPO/logs/engine.out.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/engine.err.log</string>
</dict></plist>
PL

cat > "$DESK/Trading START.command" <<CMD
#!/bin/sh
# Starts the engine. It always comes up with the switch OFF. Arm it yourself with ./arm paper or ./arm live.
UIDN=\$(id -u)
launchctl bootout gui/\$UIDN/$LABEL 2>/dev/null
launchctl bootstrap gui/\$UIDN "$PLIST" && echo "Engine started." || echo "Could not start the engine."
sleep 4
cd "$REPO" && ./status
echo
echo "To trade: open Terminal, cd \"$REPO\", then ./arm paper  or  ./arm live"
printf "Press Return to close"; read _
CMD

cat > "$DESK/Trading STOP.command" <<CMD
#!/bin/sh
# Switch OFF, cancel all open orders, then stop the engine.
cd "$REPO" && ./halt
launchctl bootout gui/\$(id -u)/$LABEL 2>/dev/null && echo "Engine stopped."
./status
printf "Press Return to close"; read _
CMD
chmod +x "$DESK/Trading START.command" "$DESK/Trading STOP.command"
echo "Installed. Buttons are on your Desktop. The engine is NOT running yet."
