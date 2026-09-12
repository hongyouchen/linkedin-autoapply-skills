#!/usr/bin/env python3
"""Stop hook: a worker session may not end its turn. The only accepted reasons to stop are a genuine
token-budget exhaustion (logged as 'PASS PAUSED budget: ...') or Andy terminating the pass
('PASS TERMINATED ...'). After a pass completes, the next pass starts immediately (cadence rule 3)."""
import sys, os, json, re, time
BASE = os.path.expanduser('~/.claude/autoapply')
LOGF = os.path.join(BASE, 'cron_pass_log.txt')
sys.path.insert(0, os.path.join(BASE, 'bin'))
from guard import is_worker, log

REMINDER = ('AUTOAPPLY GUARD: do not stop. Andy\'s standing rule: keep applying; the only acceptable reason to stop is running out of tokens. ')

def main():
    h = json.load(sys.stdin)
    if not is_worker(h): return
    if os.path.exists(os.path.join(BASE, 'HALT')):
        try: how = json.load(open(os.path.join(BASE, 'HALT'))).get('how_to_clear', '')
        except Exception: how = ''
        log('STOP_BLOCKED_HALT')
        sys.stderr.write(REMINDER + 'The worker is HALTED: remediate now. ' + how + '\n'); sys.exit(2)
    try: lines = [l.rstrip() for l in open(LOGF, errors='ignore') if l.strip()]
    except FileNotFoundError: lines = []
    tail = lines[-400:]
    def last(pat): return max((i for i, l in enumerate(tail) if re.match(pat, l)), default=-1)
    i_start, i_complete = last(r'PASS START\b'), last(r'PASS COMPLETE\b')
    i_cleared, i_paused, i_term = last(r'HALT \S+ CLEARED\b'), last(r'PASS PAUSED budget:'), last(r'PASS TERMINATED\b')
    i_progress = max(i_start, i_cleared, i_complete)
    # accepted exits: a budget pause or Andy's termination that is newer than any progress marker
    if i_paused > i_progress:
        log('STOP_ALLOWED_BUDGET', line=tail[i_paused][:200]); return
    if i_term > i_progress:
        log('STOP_ALLOWED_TERMINATED', line=tail[i_term][:200]); return
    last_line = tail[-1][:200] if tail else '(empty log)'
    if i_complete >= i_progress and i_complete >= 0:
        msg = (REMINDER + f'The last pass is COMPLETE ({tail[i_complete][:120]}). Start the next full pass NOW: write "PASS START <id>" and navigate to page 1 of the search '
               '(cadence rule 3: passes run back-to-back, gated only by finishing the previous one).')
    elif i_cleared > i_start:
        msg = (REMINDER + 'A HALT was just cleared; the pass continues from the listing after the last APPLIED/SKIPPED/LOGGED line, not from page 1. Last log line: ' + last_line)
    else:
        msg = (REMINDER + 'The current pass is not complete (last log line: ' + last_line + '). Continue with the next unevaluated listing / next results page; '
               'write "PASS COMPLETE <id> applied=N logged=N skipped=N blocked=N" only after every results page is done, then start the next pass.')
    msg += ' If tokens are GENUINELY exhausted, write "PASS PAUSED budget: <exact page and listing to resume from>" to cron_pass_log.txt; a false budget pause is detected by the auditor and is treated as a critical violation. Never use AskUserQuestion.'
    log('STOP_BLOCKED', last=last_line)
    sys.stderr.write(msg + '\n'); sys.exit(2)

if __name__ == '__main__':
    try: main()
    except SystemExit: raise
    except Exception as e:
        sys.stderr.write(f'stop guard error (allowed): {e}\n'); sys.exit(0)
