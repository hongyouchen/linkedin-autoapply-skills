#!/usr/bin/env python3
"""Gmail confirmation lookup for applications, via a headless Claude call restricted to the Gmail search tool.

confirmations(days) -> list of {from, subject, date, snippet, ts}   (cached ~4 minutes)
confirmed(company, since_ts) -> matching email dict or None
CLI: gmail_confirm.py <company> [since_unix]
"""
import sys, os, json, re, time, subprocess, datetime
BASE = os.path.expanduser('~/.claude/autoapply')
CACHE = os.path.join(BASE, 'gmail_cache.json')
TTL = 240
TOOL = 'mcp__claude_ai_Gmail__search_threads'
QUERY = 'newer_than:{d}d (application OR applying OR applied OR "security code for your application")'
PROMPT = ('Use the Gmail search tool exactly once with query: {q} and pageSize 50. '
          'Return ONLY a JSON array, no prose, no code fence: '
          '[{{"from":"sender address","subject":"...","date":"ISO 8601","snippet":"first 120 chars"}}]. '
          'Include every thread returned. If the tool is unavailable return {{"error":"no gmail tool"}}.')

def _parse_ts(s):
    try: return datetime.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except Exception: return 0

def confirmations(days=3, force=False):
    try: c = json.load(open(CACHE))
    except Exception: c = {}
    if not force and c.get('days') == days and time.time() - c.get('fetched', 0) < TTL:
        return c.get('threads', [])
    q = QUERY.format(d=days)
    cmd = ['claude', '-p', '--model', 'claude-haiku-4-5-20251001', '--tools', TOOL, '--allowedTools', TOOL,
           '--output-format', 'json', PROMPT.format(q=q)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
        o = json.loads(r.stdout)
        res = str(o.get('result', ''))
        m = re.search(r'\[.*\]', res, re.S)
        threads = json.loads(m.group(0)) if m else []
    except Exception as e:
        threads = c.get('threads', [])  # keep last good data
        try: open(os.path.join(BASE, 'reports', 'gmail_errors.log'), 'a').write(f"{time.ctime()} {e}\n")
        except Exception: pass
        return threads
    for t in threads: t['ts'] = _parse_ts(t.get('date', ''))
    json.dump(dict(days=days, fetched=time.time(), threads=threads), open(CACHE, 'w'))
    return threads

def confirmed(company, since_ts, threads=None):
    key = re.sub(r'[^a-z0-9]', '', (company or '').lower().split()[0]) if company else ''
    if not key: return None
    threads = threads if threads is not None else confirmations()
    for t in threads:
        if re.search(r'security code|verification code|verify your (email|identity)|one-time|passcode|confirm your email', t.get('subject', ''), re.I):
            continue  # a login/verification email is not proof the application went through
        blob = (t.get('from', '') + ' ' + t.get('subject', '') + ' ' + t.get('snippet', '')).lower().replace(' ', '')
        if key in blob and t.get('ts', 0) >= since_ts - 300:
            return t
    return None

if __name__ == '__main__':
    comp = sys.argv[1]; since = float(sys.argv[2]) if len(sys.argv) > 2 else time.time() - 86400
    t = confirmed(comp, since)
    print(json.dumps(t) if t else f'NO CONFIRMATION for {comp} since {time.ctime(since)}')
    sys.exit(0 if t else 1)
