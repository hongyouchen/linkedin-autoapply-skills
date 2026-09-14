#!/usr/bin/env python3
"""Stop hook: a worker session may not end its turn, except for a genuine token-budget pause ('PASS PAUSED budget: ...')
or Andy terminating the pass ('PASS TERMINATED ...'). After a pass completes, the next pass starts immediately.
Loop protection: refusals are rate-limited so a worker that genuinely cannot progress (for example a HALT it is not
permitted to clear) is not bounced forever, burning tokens. Under a HALT: one refusal per 10 minutes. Otherwise: three
refusals per 10 minutes. When released, a FLAG line records that the worker looked stuck."""
import sys, os, json, re, time
BASE = os.environ.get('AUTOAPPLY_BASE') or os.path.expanduser('~/.claude/autoapply')
LOGF = os.path.join(BASE, 'cron_pass_log.txt')
STATE = os.path.join(BASE, 'stop_guard_state.json')
sys.path.insert(0, os.path.join(BASE, 'bin'))
from guard import is_worker, log

REMINDER = "AUTOAPPLY GUARD: do not stop. Andy's standing rule: keep applying; the only acceptable reason to stop is running out of tokens. "
WINDOW = 600

def _state():
    try: st = json.load(open(STATE))
    except Exception: st = {}
    now = time.time()
    st['blocks'] = [t for t in st.get('blocks', []) if now - t < WINDOW]
    return st

def _release(st, why):
    now = time.time()
    if now - st.get('last_flag', 0) > 1800:
        try:
            with open(LOGF, 'a') as f:
                f.write(f"FLAG: supervisor released the stop hook at {time.strftime('%Y-%m-%d %H:%M')} after repeated refusals; the worker appears stuck ({why}). Needs Andy.\n")
        except Exception: pass
        st['last_flag'] = now
    json.dump(st, open(STATE, 'w'))
    log('STOP_ALLOWED_RATELIMIT', why=why)

def _block(st, msg):
    st['blocks'].append(time.time()); json.dump(st, open(STATE, 'w'))
    log('STOP_BLOCKED', msg=msg[:160])
    sys.stderr.write(msg + '\n'); sys.exit(2)

def main():
    h = json.load(sys.stdin)
    if not is_worker(h): return
    st = _state()
    if os.path.exists(os.path.join(BASE, 'HALT')):
        try: hd = json.load(open(os.path.join(BASE, 'HALT'))); how = hd.get('how_to_clear', ''); hid = hd.get('id')
        except Exception: how, hid = '', '?'
        if len(st['blocks']) >= 1:
            return _release(st, f'HALT {hid} still present')
        return _block(st, REMINDER + f'The worker is HALTED ({hid}): write the REMEDIATED line and do any listed resubmissions; the supervisor clears HALT automatically once conditions hold. ' + how)
    try: lines = [l.rstrip() for l in open(LOGF, errors='ignore') if l.strip()]
    except FileNotFoundError: lines = []
    tail = lines[-400:]
    def last(pat): return max((i for i, l in enumerate(tail) if re.match(pat, l)), default=-1)
    i_start, i_complete = last(r'(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?Z?\s+)?PASS START\b'), last(r'(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?Z?\s+)?PASS COMPLETE\b')
    i_cleared, i_paused, i_term = last(r'(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?Z?\s+)?HALT \S+ CLEARED\b'), last(r'(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?Z?\s+)?PASS PAUSED budget:'), last(r'(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?Z?\s+)?PASS TERMINATED\b')
    i_progress = max(i_start, i_cleared, i_complete)
    if i_paused > i_progress:
        log('STOP_ALLOWED_BUDGET', line=tail[i_paused][:200]); return
    if i_term > i_progress:
        log('STOP_ALLOWED_TERMINATED', line=tail[i_term][:200]); return
    # LinkedIn security checkpoint (2026-09-14): stopping protects Andy's account; allowed until a later line shows work resumed
    i_ckpt = max((i for i, l in enumerate(tail) if 'STOPPED-LINKEDIN-CHECKPOINT' in l), default=-1)
    if i_ckpt >= 0 and not any(re.search(r'CHECKPOINT CLEARED|\bRESUMED\b|\bAPPLIED\b|\bSUBMITTED\b|PASS START\b', l) for l in tail[i_ckpt + 1:]):
        log('STOP_ALLOWED_LINKEDIN_CHECKPOINT', line=tail[i_ckpt][:200]); return
    if len(st['blocks']) >= 3:
        return _release(st, 'three stop refusals in 10 minutes without progress')
    last_line = tail[-1][:200] if tail else '(empty log)'
    # a pass suspended for a priority pass (e.g. Andy's past-24h pass) is resumed, not restarted from page 1
    _susp = None
    if i_complete >= i_progress and i_complete >= 0:
        for _i in range(i_complete - 1, -1, -1):
            _m = re.search(r'PASS (\d{3,4})\b[^\n]*\bSUSPENDED\b[^\n]*?(?:resume at\s*(.*))?$', tail[_i], re.I)
            if _m:
                _pid = _m.group(1)
                if not any(re.search(r'PASS ' + _pid + r'\b[^\n]*\b(RESUMED|COMPLETE)\b', l) or re.match(r'(?:\S+\s+)?PASS COMPLETE ' + _pid + r'\b', l) for l in tail[_i + 1:]):
                    _susp = (_pid, (_m.group(2) or tail[_i])[:160])
                break
    if _susp:
        msg = REMINDER + f'The priority pass is COMPLETE. Now resume suspended PASS {_susp[0]} (do not start from page 1): write "PASS {_susp[0]} | RESUMED" and continue at: {_susp[1]}'
    elif i_complete >= i_progress and i_complete >= 0:
        msg = REMINDER + f'The last pass is COMPLETE ({tail[i_complete][:120]}). Start the next full pass NOW: write "PASS START <id>" and navigate to page 1.'
    elif i_cleared > i_start:
        msg = REMINDER + 'A HALT was just cleared; continue from the listing after the last APPLIED/SKIPPED/LOGGED line, not from page 1. Last log line: ' + last_line
    else:
        msg = REMINDER + 'The current pass is not complete (last log line: ' + last_line + '). Continue with the next unevaluated listing / next results page.'
    msg += ' If tokens are GENUINELY exhausted, write "PASS PAUSED budget: <exact page and listing>" to cron_pass_log.txt. Never use AskUserQuestion.'
    _block(st, msg)

if __name__ == '__main__':
    try: main()
    except SystemExit: raise
    except Exception as e:
        sys.stderr.write(f'stop guard error (allowed): {e}\n'); sys.exit(0)
