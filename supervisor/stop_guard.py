#!/usr/bin/env python3
"""Stop hook: a worker session may not end its turn while a pass is incomplete."""
import sys, os, json, re, time
BASE = os.path.expanduser('~/.claude/autoapply')
LOGF = os.path.join(BASE, 'cron_pass_log.txt')
STATE = os.path.join(BASE, 'stop_guard_state.json')
sys.path.insert(0, os.path.join(BASE, 'bin'))
from guard import is_worker, log

def main():
    h = json.load(sys.stdin)
    if not is_worker(h): return
    if os.path.exists(os.path.join(BASE, 'HALT')):
        try: hd = json.load(open(os.path.join(BASE, 'HALT'))); how = hd.get('how_to_clear', '')
        except Exception: how = ''
        log('STOP_BLOCKED_HALT')
        sys.stderr.write('AUTOAPPLY GUARD: worker is HALTED. Do not stop; remediate now. ' + how + '\n')
        sys.exit(2)
    try: lines = [l.rstrip() for l in open(LOGF, errors='ignore') if l.strip()]
    except FileNotFoundError: return
    tail = lines[-400:]
    def last(pat):
        return max((i for i, l in enumerate(tail) if re.match(pat, l)), default=-1)
    i_start = last(r'PASS START\b')
    i_end = last(r'PASS (COMPLETE|TERMINATED|PAUSED budget:)')
    i_cleared = last(r'HALT \S+ CLEARED\b')
    # a pass is in progress if a START (or a HALT clearance, which always happens mid-pass) is newer than the last end marker
    in_progress = max(i_start, i_cleared) > i_end
    if not in_progress:
        return
    after = tail[max(i_start, i_cleared):]
    # rate-limit: at most 6 blocks per hour so a stuck worker does not loop forever
    try: st = json.load(open(STATE))
    except Exception: st = {'blocks': []}
    now = time.time(); st['blocks'] = [t for t in st['blocks'] if now - t < 3600]
    if len(st['blocks']) >= 6:
        log('STOP_ALLOWED_RATELIMIT'); return
    st['blocks'].append(now); json.dump(st, open(STATE, 'w'))
    log('STOP_BLOCKED', last=after[-1][:200])
    resumed = ' A HALT was just cleared: the pass continues from the listing after the last APPLIED/SKIPPED/LOGGED line, not from page 1.' if i_cleared > i_start else ''
    sys.stderr.write('AUTOAPPLY GUARD: the current pass is not marked complete in cron_pass_log.txt (last line: '
                     + after[-1][:200] + '). Do not stop. Continue the pass from where it left off: next unevaluated listing / next results page.' + resumed + ' '
                     'When every page is done write "PASS COMPLETE <id> ..." with the tally. If Andy terminated it, write "PASS TERMINATED <id> <reason>". '
                     'Only if tokens are genuinely exhausted write "PASS PAUSED budget: <exact page/listing to resume from>".\n')
    sys.exit(2)

if __name__ == '__main__':
    try: main()
    except SystemExit: raise
    except Exception as e:
        sys.stderr.write(f'stop guard error (allowed): {e}\n'); sys.exit(0)
