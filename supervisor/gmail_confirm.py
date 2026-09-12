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
import shutil
CLAUDE = shutil.which('claude') or next((c for c in [os.path.expanduser('~/.local/bin/claude'), '/opt/homebrew/bin/claude', '/usr/local/bin/claude'] if os.path.exists(c)), 'claude')

def _run_search(q):
    """Returns a list of message dicts, or None if the lookup itself failed (so callers never read failure as 'no email')."""
    cmd = [CLAUDE, '-p', '--model', 'claude-haiku-4-5-20251001', '--tools', TOOL, '--allowedTools', TOOL, '--output-format', 'json', PROMPT.format(q=q)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
        o = json.loads(r.stdout)
        if o.get('is_error'): raise RuntimeError(str(o.get('result'))[:200])
        res = str(o.get('result', ''))
        if '"error"' in res and 'gmail' in res.lower(): raise RuntimeError(res[:200])
        m = re.search(r'\[.*\]', res, re.S)
        if not m: raise RuntimeError('no JSON array in result: ' + res[:150])
        threads = json.loads(m.group(0))
        for t in threads: t['ts'] = _parse_ts(t.get('date', ''))
        return threads
    except Exception as e:
        try: open(os.path.join(BASE, 'reports', 'gmail_errors.log'), 'a').write(f"{time.ctime()} {type(e).__name__}: {e}\n")
        except Exception: pass
        return None
QUERY = 'newer_than:{d}d (application OR applying OR applied OR "security code for your application")'
PROMPT = ('Use the Gmail search tool exactly once with query: {q} and pageSize 50. '
          'Return ONLY a JSON array, no prose, no code fence: '
          '[{{"from":"sender address","subject":"...","date":"ISO 8601","snippet":"first 120 chars"}}]. '
          'IMPORTANT: threads contain a "messages" list; emit ONE entry PER MESSAGE (every message of every thread), '
          'each with that message\'s own date, not one entry per thread. '
          'If the tool is unavailable return {{"error":"no gmail tool"}}.')

def _parse_ts(s):
    try: return datetime.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except Exception: return 0

def confirmations(days=3, force=False):
    try: c = json.load(open(CACHE))
    except Exception: c = {}
    if not force and c.get('days') == days and time.time() - c.get('fetched', 0) < TTL:
        return c.get('threads', [])
    threads = _run_search(QUERY.format(d=days))
    if threads is None: return None
    json.dump(dict(days=days, fetched=time.time(), threads=threads), open(CACHE, 'w'))
    return threads

def targeted(companies, days=3):
    """fallback: a search naming the companies directly. None if the lookup failed."""
    keys = set()
    for c in companies:
        words = re.sub(r'[^A-Za-z0-9 ]', ' ', (c or '').split('(')[0]).split()
        if words and not words[0].isdigit() and len(words[0]) > 1: keys.add(words[0])
    keys = sorted(keys)
    if not keys: return []
    tag = 'targeted:' + ','.join(keys)
    try: c = json.load(open(CACHE + '.targeted'))
    except Exception: c = {}
    ent = c.get(tag)
    if ent and time.time() - ent['fetched'] < TTL: return ent['threads']
    threads = _run_search(f'newer_than:{days}d (' + ' OR '.join(f'"{k}"' for k in keys) + ')')
    if threads is None: return None
    c[tag] = dict(fetched=time.time(), threads=threads); json.dump(c, open(CACHE + '.targeted', 'w'))
    return threads

def confirmed(company, since_ts, threads=None):
    words = re.sub(r'[^a-z0-9 ]', ' ', (company or '').lower()).split()
    key = words[0] if words else ''
    if not key: return None
    threads = threads if threads is not None else (confirmations() or [])
    for t in threads:
        if re.search(r'security code|verification code|verify your (email|identity)|one-time|passcode|confirm your email', t.get('subject', ''), re.I):
            continue  # a login/verification email is not proof the application went through
        if not re.search(r'appl(y|ied|ication|ying)|your interest|candidate|received your|next steps', t.get('subject', '') + ' ' + t.get('snippet', ''), re.I):
            continue  # marketing / account mail from the same company is not a confirmation
        blob = (t.get('from', '') + ' ' + t.get('subject', '') + ' ' + t.get('snippet', '')).lower().replace(' ', '')
        if key in blob and t.get('ts', 0) >= since_ts - 300:
            return t
    return None

if __name__ == '__main__':
    comp = sys.argv[1]; since = float(sys.argv[2]) if len(sys.argv) > 2 else time.time() - 86400
    t = confirmed(comp, since) or confirmed(comp, since, targeted([comp]))
    print(json.dumps(t) if t else f'NO CONFIRMATION for {comp} since {time.ctime(since)}')
    sys.exit(0 if t else 1)
