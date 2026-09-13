#!/usr/bin/env python3
"""Hook launcher: runs guard.py or stop_guard.py and decides what happens if the hook itself is broken.

guard.py (PreToolUse) fails CLOSED for worker sessions: if it has a syntax error or crashes, the worker's tool call is
blocked with a clear message, instead of every call silently passing. Non-worker sessions (the supervisor) are allowed
through, so a broken guard can still be repaired.
stop_guard.py (Stop) fails OPEN: a broken stop hook must never trap a session in an endless stop loop.
Usage: hook_launcher.py guard.py | stop_guard.py   (hook JSON on stdin)
"""
import sys, os, json, subprocess
BASE = os.environ.get('AUTOAPPLY_BASE') or os.path.expanduser('~/.claude/autoapply')
target = sys.argv[1] if len(sys.argv) > 1 else 'guard.py'
fail_closed = target == 'guard.py'
raw = sys.stdin.read()

def is_worker_session():
    try: sid = json.loads(raw).get('session_id', '')
    except Exception: return False
    try: return bool(sid) and sid in open(os.path.join(BASE, 'worker_sessions.txt')).read()
    except Exception: return False

def broken(why):
    msg = f'AUTOAPPLY SUPERVISOR IS BROKEN ({target}: {why}).'
    if fail_closed and is_worker_session():
        sys.stderr.write(msg + ' Worker actions are blocked until it is repaired; write a FLAG line and stop.\n'); sys.exit(2)
    sys.stderr.write(msg + ' Allowed (non-worker session or stop hook).\n'); sys.exit(0)

path = os.path.join(BASE, 'bin', target)
try:
    compile(open(path).read(), path, 'exec')
except Exception as e:
    broken(f'{type(e).__name__}: {e}')
try:
    r = subprocess.run([sys.executable, path], input=raw, capture_output=True, text=True, timeout=170)
except Exception as e:
    broken(f'{type(e).__name__}: {e}')
sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
if r.returncode in (0, 2):
    sys.exit(r.returncode)
broken(f'exited with code {r.returncode}: {r.stderr.strip()[-200:]}')
