#!/bin/zsh
# Runs the deterministic audit, then notifies Andy. Scheduled by launchd every 2 hours.
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"
BASE="$HOME/.claude/autoapply"
cd "$BASE" || exit 1
OUT=$(/usr/bin/python3 "$BASE/bin/audit.py" "$@" 2>&1)
echo "$(date '+%Y-%m-%d %H:%M') ----" >> "$BASE/reports/audit_runs.log"
echo "$OUT" >> "$BASE/reports/audit_runs.log"
MSG=$(echo "$OUT" | grep '^SUMMARY: ' | tail -1 | sed 's/^SUMMARY: //')
[ -z "$MSG" ] && MSG="audit failed; see reports/audit_runs.log"
/usr/bin/osascript -e "display notification \"$MSG\" with title \"Autoapply audit\" subtitle \"Desktop/Autoapply Audits/latest.html\"" 2>/dev/null
mkdir -p "$HOME/Desktop/Autoapply Audits" && cp "$BASE/reports/latest.html" "$HOME/Desktop/Autoapply Audits/latest.html" 2>/dev/null
echo "$MSG"
