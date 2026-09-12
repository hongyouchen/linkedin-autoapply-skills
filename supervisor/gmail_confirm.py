#!/usr/bin/env python3
"""Gmail confirmation lookup for applications.

Live searches run a headless Claude call restricted to the Gmail search tool. That only works from a process
that inherits a logged-in Claude Code context (the worker's guard hook, clear_halt.py run by the worker, an
interactive shell). The launchd auditor is not logged in, so audit.sh sets AUTOAPPLY_NO_LIVE=1 and the auditor
reads the cache that the guard hook refreshes in the background.

confirmations(days, force) -> list of message dicts, or None when unavailable (None is never "no email")
targeted(companies)        -> list, or None when unavailable
confirmed(company, since_ts, threads) -> matching confirmation message or None
cache_fetched()            -> unix time of the last successful broad refresh (0 if none)
CLI: gmail_confirm.py --refresh     |     gmail_confirm.py <company> [since_unix]
"""
import sys, os, json, re, time, subprocess, datetime, shutil

BASE = os.path.expanduser('~/.claude/autoapply')
CACHE = os.path.join(BASE, 'gmail_cache.json')
TCACHE = CACHE + '.targeted'
TTL = 240       # live callers reuse a cache younger than this
STALE = 1800    # cache-only callers accept a cache younger than this
TOOL = 'mcp__claude_ai_Gmail__search_threads'
CLAUDE = shutil.which('claude') or next((c for c in [os.path.expanduser('~/.local/bin/claude'), '/opt/homebrew/bin/claude', '/usr/local/bin/claude'] if os.path.exists(c)), 'claude')
QUERY = 'newer_than:{d}d (application OR applying OR applied OR "security code for your application")'
BROAD_DAYS = 1
PROMPT = ('Use the Gmail search tool exactly once with query: {q} and pageSize 50. '
          'Return ONLY a JSON array, no prose, no code fence: '
          '[{{"from":"sender address","subject":"...","date":"ISO 8601","snippet":"the COMPLETE snippet text, verbatim, not shortened"}}]. '
          'IMPORTANT: threads contain a "messages" list; emit ONE entry PER MESSAGE (every message of every thread), '
          'each with that message\'s own date, not one entry per thread. '
          'If the tool is unavailable return {{"error":"no gmail tool"}}.')

def _no_live():
    return os.environ.get('AUTOAPPLY_NO_LIVE') == '1'

def _parse_ts(s):
    try: return datetime.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except Exception: return 0

def _err(msg):
    try: open(os.path.join(BASE, 'reports', 'gmail_errors.log'), 'a').write(f"{time.ctime()} {msg}\n")
    except Exception: pass

def _load(p):
    try: return json.load(open(p))
    except Exception: return {}

def _run_search(q):
    """List of message dicts, or None if the lookup itself failed."""
    if _no_live(): return None
    cmd = [CLAUDE, '-p', '--model', 'claude-haiku-4-5-20251001', '--tools', TOOL, '--allowedTools', TOOL,
           '--output-format', 'json', PROMPT.format(q=q)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
        o = json.loads(r.stdout)
        if o.get('is_error'): raise RuntimeError(str(o.get('result'))[:200])
        res = str(o.get('result', ''))
        m = re.search(r'\[.*\]', res, re.S)
        if not m: raise RuntimeError('no JSON array in result: ' + res[:150])
        threads = json.loads(m.group(0))
        for t in threads: t['ts'] = _parse_ts(t.get('date', ''))
        return threads
    except Exception as e:
        _err(f"{type(e).__name__}: {e}")
        return None

def cache_fetched():
    return _load(CACHE).get('fetched', 0)

def confirmations(days=BROAD_DAYS, force=False):
    c = _load(CACHE); age = time.time() - c.get('fetched', 0)
    cached = c.get('threads', []) if c.get('fetched') and age < STALE else None
    if _no_live(): return cached
    if not force and c.get('days') == days and age < TTL: return c.get('threads', [])
    threads = _run_search(QUERY.format(d=days))
    if threads is None: return cached
    json.dump(dict(days=days, fetched=time.time(), threads=threads), open(CACHE, 'w'))
    return threads

def _key(company):
    words = re.sub(r'[^A-Za-z0-9 ]', ' ', (company or '').split('(')[0]).split()
    return words[0].lower() if words and not words[0].isdigit() and len(words[0]) > 1 else ''

def targeted(companies, days=3, batch=5):
    keys = sorted({k for k in (_key(c) for c in companies) if k})
    if not keys: return []
    c = _load(TCACHE)
    def union(max_age):
        ents = [e for e in c.values() if isinstance(e, dict) and time.time() - e.get('fetched', 0) < max_age]
        covered = {k for e in ents for k in e.get('keys', [])}
        return ([t for e in ents for t in e.get('threads', [])] if set(keys) <= covered else None)
    if _no_live(): return union(STALE)
    hit = union(TTL)
    if hit is not None: return hit
    fresh_keys = {k for e in c.values() if isinstance(e, dict) and time.time() - e.get('fetched', 0) < TTL for k in e.get('keys', [])}
    todo = [k for k in keys if k not in fresh_keys]
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        threads = _run_search(f'newer_than:{days}d (' + ' OR '.join(f'"{k}"' for k in chunk) + ')')
        if threads is None: continue
        c['|'.join(chunk)] = dict(keys=chunk, fetched=time.time(), threads=threads, truncated=len(threads) >= 50)
    c = {k: v for k, v in c.items() if isinstance(v, dict) and time.time() - v.get('fetched', 0) < 86400}
    json.dump(c, open(TCACHE, 'w'))
    return union(STALE)

def covered(company):
    """True only if a fresh, NON-truncated targeted search included this company's key."""
    key = _key(company)
    return any(isinstance(e, dict) and key in e.get('keys', []) and not e.get('truncated') and time.time() - e.get('fetched', 0) < STALE
               for e in _load(TCACHE).values())

def confirmed(company, since_ts, threads=None):
    key = _key(company)
    if not key: return None
    threads = threads if threads is not None else (confirmations() or [])
    for t in threads:
        subj = t.get('subject', '')
        if re.search(r'security code|verification code|verify your (email|identity)|one-time|passcode|confirm your email', subj, re.I):
            continue  # a login/verification email is not proof the application went through
        if not re.search(r'appl(y|ied|ication|ying)|your interest|candidate|received your|next steps', subj + ' ' + t.get('snippet', ''), re.I):
            continue  # marketing / account mail from the same company is not a confirmation
        blob = (t.get('from', '') + ' ' + subj + ' ' + t.get('snippet', '')).lower().replace(' ', '')
        if key in blob and t.get('ts', 0) >= since_ts - 300:
            return t
    return None

def refresh():
    """Run by the guard hook in the background (inherits the worker's logged-in context).
    Cost-bounded: one broad search, plus targeted searches only for submissions 15 min to 6 h old that are not yet
    confirmed, and each company key at most once every 30 min."""
    th = confirmations(force=True)
    try: led = [json.loads(l) for l in open(os.path.join(BASE, 'ledger.jsonl')) if l.strip()]
    except Exception: led = []
    now = time.time()
    recent = [e for e in led if e.get('event') == 'submitted' and e.get('company') and 900 < now - e.get('ts', 0) < 6 * 3600]
    tc = _load(TCACHE)
    cached_threads = [t for e in tc.values() if isinstance(e, dict) for t in e.get('threads', [])]
    need = [e for e in recent if not confirmed(e['company'], e['ts'], th or []) and not confirmed(e['company'], e['ts'], cached_threads)]
    last_searched = {}
    for e in tc.values():
        if isinstance(e, dict):
            for k in e.get('keys', []): last_searched[k] = max(last_searched.get(k, 0), e.get('fetched', 0))
    due = sorted({e['company'] for e in need if now - last_searched.get(_key(e['company']), 0) > 1800})
    if due:
        global TTL
        TTL, _ttl = 0, TTL   # force a live search for the due keys only
        try: targeted(due)
        finally: TTL = _ttl
    tc = _load(TCACHE); cached_threads = [t for e in tc.values() if isinstance(e, dict) for t in e.get('threads', [])]
    out = dict(at=time.ctime(), broad=None if th is None else len(th), searched=due, recent=len(recent),
               unconfirmed=sorted({e['company'] for e in need if not confirmed(e['company'], e['ts'], cached_threads)}))
    try: json.dump(out, open(os.path.join(BASE, 'reports', 'gmail_refresh.json'), 'w'))
    except Exception: pass
    return out

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--refresh':
        print(json.dumps(refresh())); sys.exit(0)
    comp = sys.argv[1]; since = float(sys.argv[2]) if len(sys.argv) > 2 else time.time() - 86400
    t = confirmed(comp, since) or confirmed(comp, since, targeted([comp]) or [])
    print(json.dumps(t) if t else f'NO CONFIRMATION for {comp} since {time.ctime(since)}')
    sys.exit(0 if t else 1)
