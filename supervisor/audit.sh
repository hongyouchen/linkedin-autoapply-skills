#!/bin/zsh
# Runs the deterministic audit, then notifies Andy when there is something to see. Scheduled by launchd every 5 minutes.
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"
BASE="$HOME/.claude/autoapply"
cd "$BASE" || exit 1
# never let two runs overlap (a 5-minute cadence on a slow day)
if ! mkdir "$BASE/.audit.lock" 2>/dev/null; then
  # stale lock older than 10 minutes gets cleared
  if [ -n "$(find "$BASE/.audit.lock" -mmin +10 2>/dev/null)" ]; then rmdir "$BASE/.audit.lock"; mkdir "$BASE/.audit.lock" || exit 0; else exit 0; fi
fi
trap 'rmdir "$BASE/.audit.lock" 2>/dev/null' EXIT
OUT=$(/usr/bin/python3 "$BASE/bin/audit.py" "$@" 2>&1)
echo "$(date '+%Y-%m-%d %H:%M') ----" >> "$BASE/reports/audit_runs.log"
echo "$OUT" >> "$BASE/reports/audit_runs.log"
MSG=$(echo "$OUT" | grep '^SUMMARY: ' | tail -1 | sed 's/^SUMMARY: //')
[ -z "$MSG" ] && MSG="audit failed; see reports/audit_runs.log"
mkdir -p "$HOME/Desktop/Autoapply Audits" && cp "$BASE/reports/latest.html" "$HOME/Desktop/Autoapply Audits/latest.html" 2>/dev/null
# notify only when the window had activity: findings, resumes written, a halt, or a failure
if echo "$MSG" | grep -Eqv '^0 apps, 0 findings \(0 critical\), 0/0 resumes failing$'; then
  /usr/bin/osascript -e "display notification \"$MSG\" with title \"Autoapply audit\" subtitle \"Desktop/Autoapply Audits/latest.html\"" 2>/dev/null
fi
echo "$MSG"
