# Autoapply supervisor: runbook

Everything lives in `~/.claude/autoapply/`. The guard hooks are already live in `~/.claude/settings.json`.

## Commands to run once (the two things I could not do from the sandbox)

1. Start the 5-minute auditor:

```
cp ~/.claude/autoapply/com.andy.autoapply-audit.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.andy.autoapply-audit.plist
```

Check it is running: `launchctl list | grep autoapply` (a line with the label means loaded).
Run one audit by hand at any time: `zsh ~/.claude/autoapply/bin/audit.sh`

2. Push the skills repo (the worker's instruction source, now with the supervisor docs and scripts):

```
cd ~/linkedin-autoapply-skills && git push
```

## Where to look

- `~/Desktop/Autoapply Audits/latest.html`: latest audit (findings, per-application evidence, Gmail confirmation, images of the worst resumes). A macOS notification fires only when a window had activity.
- `~/.claude/autoapply/reports/audit_log.txt`: one line per audit run.
- `~/.claude/autoapply/guard_log.jsonl`: every block the guard made, with the reason the worker was shown.
- `~/.claude/autoapply/HALT`: exists only while the worker is halted. You never need to touch it; the worker clears it by remediating (rebuild, resubmit, Gmail confirmation) and running `clear_halt.py`.
- `~/.claude/autoapply/ledger.jsonl`: per-application entries the worker writes, plus hook-written `validated_upload` stamps.
- `~/.claude/autoapply/cron_pass_log.txt`: the canonical pass log (moved out of the temp scratchpad).

## To stop everything

```
launchctl unload ~/Library/LaunchAgents/com.andy.autoapply-audit.plist
```

and remove the two `autoapply` hook entries from `~/.claude/settings.json` (a backup of the pre-hook file is in `~/.claude/backups/`).

## Tuning knobs (constants at the top of each script)

- `bin/validate_resume.py`: `FILL_MIN` (0.87), `LEN_MIN` (0.80), `SPECIFICS_MIN` (0.90), `TRACE_MIN` (0.60), `MIN_CHANGED` (3).
- `bin/audit.py`: Gmail grace: HIGH after 30 min, CRITICAL (halt) after 90 min without a confirmation email.
- `com.andy.autoapply-audit.plist`: `StartInterval` seconds (300).
