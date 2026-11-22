#!/bin/sh
# Opens the scanner UI full screen once the app is up. Run at login by
# bumrc-kiosk.desktop (see README). Exit the browser with Alt+F4.
URL="${SCANNER_URL:-http://localhost:8080}"

# keep the screen on
xset s off 2>/dev/null
xset s noblank 2>/dev/null
xset -dpms 2>/dev/null

# wait (up to 3 minutes) for the container to start serving
i=0
until curl -fs "$URL/api/status" >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -gt 180 ] && break
    sleep 1
done

# flags: full screen, no "restore session" or error popups, no disk cache,
# fewer background processes to save RAM on the 4GB Jetson
exec chromium-browser \
    --kiosk --incognito --noerrdialogs --disable-infobars \
    --no-first-run --disable-session-crashed-bubble --disable-translate \
    --disk-cache-dir=/dev/null --disable-background-networking \
    --disable-sync --disable-extensions --renderer-process-limit=2 \
    --check-for-update-interval=31536000 \
    "$URL"
