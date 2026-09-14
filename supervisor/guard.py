#!/usr/bin/env python3
"""PreToolUse guard for the LinkedIn autoapply worker. Reads hook JSON on stdin.
Blocks (exit 2 + reason on stderr) when a call would violate a standing rule.
Only applies worker-scoped rules to sessions that look like autoapply worker sessions;
the resume-upload validator applies everywhere.
"""
import sys, os, json, re, time, subprocess, datetime

BASE = os.environ.get('AUTOAPPLY_BASE') or os.path.expanduser('~/.claude/autoapply')
LEDGER = os.path.join(BASE, 'ledger.jsonl')
HALT = os.path.join(BASE, 'HALT')
LOG = os.path.join(BASE, 'guard_log.jsonl')
VALIDATOR = os.path.join(BASE, 'bin', 'validate_resume.py')
RESUME_DIR = os.path.expanduser('~/Downloads/Claude Resumes')
ATS_RE = re.compile(r'https?://[^\s"\']*(ashbyhq\.com|greenhouse\.io|rippling\.com|lever\.co|myworkdayjobs\.com|workable\.com|smartrecruiters\.com|jobvite\.com|bamboohr\.com|icims\.com|wellfound\.com|avature\.net|successfactors\.com|applytojob\.com|breezy\.hr|dover\.com)', re.I)
EMPLOYERS = ['gusto', 'wingman', 'olive capital', 'castleton', 'scottie ventures', 'goldman sachs']

def log(kind, **kw):
    try:
        with open(LOG, 'a') as f: f.write(json.dumps(dict(ts=time.time(), kind=kind, **kw)) + '\n')
    except Exception: pass

def deny(reason, **kw):
    log('BLOCK', reason=reason, **kw)
    sys.stderr.write('AUTOAPPLY GUARD BLOCKED THIS CALL: ' + reason + '\n')
    sys.exit(2)

def walk(obj, path=''):
    """yield (key_path, string) for every string in a nested JSON value."""
    if isinstance(obj, dict):
        for k, v in obj.items(): yield from walk(v, path + '/' + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj): yield from walk(v, path + f'[{i}]')
    elif isinstance(obj, str):
        yield path, obj

WORKER_RE = re.compile(r'recurring LinkedIn autoapply pass|auto-?apply campaign|autoapply_process\.md|Run the .{0,40}autoapply', re.I)

def is_worker(h):
    """A worker session is one registered in worker_sessions.txt, or whose FIRST user message is the
    autoapply cron prompt / a continuation summary of it. Only the first user message is inspected so
    other sessions that merely discuss the worker (like the supervisor) are not treated as workers."""
    sid = h.get('session_id', '')
    reg_path = os.path.join(BASE, 'worker_sessions.txt')
    try:
        reg = open(reg_path).read()
        if sid and sid in reg: return True
    except Exception: pass
    tp = h.get('transcript_path') or ''
    try:
        with open(tp, 'rb') as f: head = f.read(400000).decode('utf-8', 'ignore')
    except Exception:
        return False
    for line in head.splitlines():
        try: o = json.loads(line)
        except Exception: continue
        if o.get('type') != 'user': continue
        c = o.get('message', {}).get('content')
        txt = c if isinstance(c, str) else ' '.join(x.get('text', '') for x in c if isinstance(x, dict) and x.get('type') == 'text')
        if WORKER_RE.search(txt):
            try:
                with open(reg_path, 'a') as f: f.write(sid + '\n')
            except Exception: pass
            return True
        return False
    return False

def _as_ts(v):
    """Ledger timestamps must be numbers; accept numeric strings and ISO dates, anything else counts as 0."""
    if isinstance(v, (int, float)): return float(v)
    try: return float(v)
    except Exception: pass
    try: return datetime.datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
    except Exception: return 0.0

def ledger_entries():
    out = []
    try:
        for line in open(LEDGER):
            line = line.strip()
            if not line: continue
            try: e = json.loads(line)
            except Exception: continue
            if not isinstance(e, dict): continue
            e['ts'] = _as_ts(e.get('ts', 0))
            for _k in ('resume_path', 'jd_path'):
                if isinstance(e.get(_k), str) and e[_k].startswith('~'): e[_k] = os.path.expanduser(e[_k])
            out.append(e)
    except FileNotFoundError: pass
    return out

def halt_pending_resumes(halt):
    """names of must_pass resumes that still fail (validator results cached by file mtime)."""
    try: cache = json.load(open(HALT_CACHE))
    except Exception: cache = {}
    pending = []
    for ent in halt.get('must_pass', []):
        p = ent['resume_path'] if isinstance(ent, dict) else ent
        if not os.path.exists(p): pending.append(os.path.basename(p) + ' (missing)'); continue
        key = f"{p}@{os.path.getmtime(p)}"
        if key not in cache:
            r = subprocess.run([sys.executable, VALIDATOR, '--json', p], capture_output=True, text=True, timeout=120)
            try: cache[key] = json.loads(r.stdout)['ok']
            except Exception: cache[key] = False
            try: json.dump(cache, open(HALT_CACHE, 'w'))
            except Exception: pass
        if not cache[key]: pending.append(os.path.basename(p))
    return pending

def try_auto_clear(halt):
    """Run clear_halt.py from the hook (the harness, not the worker) at most every 20 s. The worker's own
    permission classifier refuses an agent clearing its own safety block, so the worker only writes its
    REMEDIATED line and does any resubmissions; this removes HALT once every condition holds."""
    stamp = os.path.join(BASE, '.halt_clear_try')
    try:
        if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 20: return False
        open(stamp, 'w').write(str(time.time()))
        env = dict(os.environ); env['AUTOAPPLY_NO_LIVE'] = '1'; env['AUTOAPPLY_BASE'] = BASE
        subprocess.run([sys.executable, os.path.join(BASE, 'bin', 'clear_halt.py')], env=env, capture_output=True, text=True, timeout=150)
        if not os.path.exists(HALT):
            log('HALT_AUTO_CLEARED', id=(halt or {}).get('id')); return True
    except Exception as e:
        log('HALT_AUTO_CLEAR_ERROR', err=str(e))
    return False

ACTIVE = os.path.join(BASE, 'active_resume.json')

def _resume_key(path):
    b = os.path.basename(path).lower()
    b = re.sub(r'^(wk_|redo_|resume[ _-]*-?[ _]*)', '', b)
    parts = [p for p in re.split(r'[^a-z0-9]+', b) if p]
    return parts[0] if parts else b

def _company_key_from_resume(rp):
    m = re.match(r'Resume - (.+?)(?: \(|\.pdf$)', os.path.basename(rp or ''))
    return re.sub(r'[^a-z0-9]', '', m.group(1).lower()) if m else ''

def _resume_finished(active, st):
    """finished = its resume PDF (mapped from the cp into Claude Resumes, or found via the ledger) passes the validator
    after the last HTML edit; or a closing ledger event or pass-log decision for that company; or 20 min without edits."""
    if time.time() - active.get('ts_last', 0) > 1200:
        return True
    keys = {k for k in (_resume_key(active['path']), _company_key_from_resume(active.get('resume_path'))) if k}
    def _match(co):
        co = re.sub(r'[^a-z0-9]', '', (co or '').lower())
        return bool(co) and any(co.startswith(k) or k.startswith(co) for k in keys)
    checked = st.setdefault('checked', {})
    def _passes(rp):
        if not rp or not os.path.exists(rp) or os.path.getmtime(rp) < active.get('ts_last', 0) - 2: return False
        mt = os.path.getmtime(rp); hit = checked.get(rp)
        if not hit or hit[0] != mt:
            try:
                r = subprocess.run([sys.executable, VALIDATOR, '--json', rp], capture_output=True, text=True, timeout=120)
                ok = json.loads(r.stdout).get('ok', False)
            except Exception:
                ok = False
            checked[rp] = [mt, ok]; hit = checked[rp]
        return bool(hit[1])
    if _passes(active.get('resume_path')):
        return True
    try:
        if os.path.getmtime(os.path.join(BASE, 'cron_pass_log.txt')) >= active.get('ts_start', 0):
            with open(os.path.join(BASE, 'cron_pass_log.txt'), errors='ignore') as fh:
                tail = fh.read()[-60000:].split('\n')[-400:]
            for line in reversed(tail):
                if re.search(r'\b(BLOCKED|HELD|SKIPPED|LOGGED|SKIP|APPLIED|NOT APPLIED|RESUBMITTED)\b', line) and any(k in re.sub(r'[^a-z0-9]', '', line.lower()) for k in keys):
                    return True
    except Exception:
        pass
    for e in reversed(ledger_entries()):
        if not _match(e.get('company')): continue
        if e['ts'] < active.get('ts_start', 0) - 6 * 3600: break
        if e.get('event') in ('blocked', 'held', 'abandoned', 'skipped', 'submitted', 'validated_upload'): return True
        if _passes(e.get('resume_path')): return True
    return False

def check_one_resume_at_a_time(path, command=''):
    try: st = json.load(open(ACTIVE))
    except Exception: st = {}
    now = time.time(); a = st.get('active')
    same = a and os.path.basename(a['path']) == os.path.basename(path)  # identity is the file name; relative and absolute spellings are the same resume
    if a and not same:
        fin = _resume_finished(a, st)
        if not fin and command:
            # the same command appends a blocked/abandoned/skipped event for the active resume's company before starting the next one
            _k = {k for k in (_resume_key(a['path']), _company_key_from_resume(a.get('resume_path'))) if k}
            for _ev in re.finditer(r'\b(blocked|held|abandoned|skipped)\b', command, re.I):
                _win = re.sub(r'[^a-z0-9]', '', command[max(0, _ev.start() - 300):_ev.end() + 300].lower())
                if 'ledgerjsonl' in re.sub(r'[^a-z0-9]', '', command.lower()) and any(k in _win for k in _k):
                    fin = True; break
        json.dump(st, open(ACTIVE, 'w'))
        if not fin:
            deny(f"one resume at a time (hard_rule_no_resume_shortcuts rule 4): {os.path.basename(a['path'])} is still in progress. "
                 f"Finish it first: render it, look at the PDF, copy it into Claude Resumes and make sure validate_resume.py passes. "
                 f"If that application cannot go ahead, record it in ledger.jsonl as an event blocked (with its company) and then start "
                 f"{os.path.basename(path)}.")
    if same:
        a['ts_last'] = now
        if os.path.isabs(path): a['path'] = path
    else:
        if a:
            _h = st.setdefault('history', {}); _h[os.path.basename(a['path'])] = a
            if len(_h) > 60: [_h.pop(k2) for k2 in sorted(_h, key=lambda k2: _h[k2].get('ts_start', 0))[:len(_h) - 60]]
        st['active'] = dict(path=path, ts_start=now, ts_last=now)
    json.dump(st, open(ACTIVE, 'w'))

def check_resume_upload(paths, h, tab_id=None):
    for p in paths:
        if not p.lower().endswith('.pdf'): continue
        if os.path.abspath(os.path.dirname(p)) != os.path.abspath(RESUME_DIR) and 'resume' not in os.path.basename(p).lower():
            continue
        if not os.path.exists(p): deny(f'resume file does not exist: {p}')
        # 1. mechanical validation against core
        r = subprocess.run([sys.executable, VALIDATOR, '--json', p], capture_output=True, text=True, timeout=120)
        try: res = json.loads(r.stdout)
        except Exception: deny(f'validator error on {os.path.basename(p)}: {r.stderr[-400:]}')
        if not res['ok']:
            deny(f'{os.path.basename(p)} FAILS resume validation against resume core.pdf: ' + ' | '.join(res['fails'])
                 + '. Rebuild it individually from core.pdf at full depth (see hard_rule_no_resume_shortcuts). Do not upload a different file to get around this.')
        # 2. ledger evidence: JD saved for this company before the resume was built
        ents = [e for e in ledger_entries() if os.path.abspath(e.get('resume_path', '')) == os.path.abspath(p)
                and e.get('event') not in ('validated_upload', 'submitted', 'blocked', 'abandoned', 'skipped')
                and (not e.get('event') or e.get('linkedin_url'))]  # application entries (any label) that carry the listing, not stamps or closing records
        if not ents:
            deny(f'no ledger entry for {os.path.basename(p)}. Before uploading, append a JSON line to {LEDGER} with: company, title, linkedin_url (linkedin.com/jobs/view/...), jd_path (saved JD text under {BASE}/jd/), resume_path, apply_path. See autoapply_process.md "Supervision protocol".')
        e = ents[-1]
        url = e.get('linkedin_url', '')
        if 'linkedin.com/jobs' not in url: deny(f'ledger entry for {os.path.basename(p)} has no LinkedIn job URL (got {url!r}); the apply flow must start from the LinkedIn listing.')
        jd = e.get('jd_path', '')
        if not jd or not os.path.exists(jd): deny(f'ledger jd_path missing or not found for {os.path.basename(p)}: {jd!r}. Save the JD text you read (get_page_text output) to a file first.')
        jdtxt = open(jd, errors='ignore').read()
        if len(jdtxt) < 400: deny(f'saved JD at {jd} is only {len(jdtxt)} chars; that is not a real job description. Re-read the listing (wait/reload per the loading rule) and save the full "About the job" text.')
        if os.path.getmtime(jd) > os.path.getmtime(p) + 1: deny(f'JD file {os.path.basename(jd)} was written AFTER the resume PDF; the JD must be read before tailoring.')
        comp = (e.get('company') or '').lower()
        # the JD must have been saved before tailoring STARTED (first copy/edit of this resume's HTML), which re-copying the PDF cannot change
        try:
            _st = json.load(open(os.path.join(BASE, 'active_resume.json')))
            _cands = [_st.get('active')] + list((_st.get('history') or {}).values())
            _ck = re.sub(r'[^a-z0-9]', '', comp) if comp else ''
            _start = None
            for _a in _cands:
                if not _a: continue
                _rp = _a.get('resume_path')
                if (_rp and os.path.abspath(_rp) == os.path.abspath(p)) or (_ck and _ck.startswith(_resume_key(_a.get('path', ''))[:6]) and _resume_key(_a.get('path', ''))):
                    _start = _a.get('ts_start'); break
            if _start and os.path.getmtime(jd) > _start + 60:
                deny(f'JD file {os.path.basename(jd)} was saved after tailoring of this resume began; save the JD text right after reading it and before the first resume edit, then re-tailor against it. Re-copying the PDF does not satisfy this check.')
        except SystemExit:
            raise
        except Exception:
            pass
        fn = os.path.basename(p).lower()
        if comp and comp.split()[0] not in fn: deny(f'ledger company {comp!r} does not match resume filename {fn!r}')
        if time.time() - _as_ts(e.get('ts', 0)) > 4 * 3600: deny('ledger entry is older than 4 hours; write a fresh entry for this application.')
        # stamp
        try:
            with open(LEDGER, 'a') as f: f.write(json.dumps(dict(ts=time.time(), event='validated_upload', resume_path=p, company=e.get('company'), session=h.get('session_id'), tab_id=tab_id)) + '\n')
        except Exception: pass
        log('ALLOW_UPLOAD', path=p, company=e.get('company'))

BULLET_PHRASES = ['document generation', 'sales outreach', 'deal tracking', 'vector search', 'trading signals',
                  'user interviews', 'icp templates', 'forecast error', 'cx cases', 'monthly page views', 'paying customers']
DOC_PATHS = ('/memory/', 'linkedin-autoapply-skills', BASE)

def looks_like_generator(text):
    """Resume CONTENT for several employers (employer names plus actual bullet phrases), not merely a doc that names them."""
    t = text.lower()
    return sum(1 for k in EMPLOYERS if k in t) >= 2 and sum(1 for k in BULLET_PHRASES if k in t) >= 2

MIRROR = os.path.join(BASE, 'mirror')

def sync_mirror():
    """Copy core.pdf and the resume folder to ~/.claude/autoapply/mirror at most once a minute, so the
    launchd auditor (which cannot read Desktop/Downloads) sees current files."""
    stamp = os.path.join(MIRROR, '.synced')
    try:
        if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 60: return
        os.makedirs(os.path.join(MIRROR, 'resumes'), exist_ok=True)
        core = os.path.expanduser('~/Desktop/resume core.pdf')
        if os.path.exists(core):
            dst = os.path.join(MIRROR, 'resume core.pdf')
            if not os.path.exists(dst) or os.path.getmtime(dst) < os.path.getmtime(core):
                subprocess.run(['cp', '-p', core, dst], timeout=20)
        if os.path.isdir(RESUME_DIR):
            subprocess.run(['rsync', '-a', '--exclude', '.*', RESUME_DIR + '/', os.path.join(MIRROR, 'resumes') + '/'], timeout=60, capture_output=True)
        open(stamp, 'w').write(str(time.time()))
    except Exception as e:
        log('MIRROR_ERROR', err=str(e))

def maybe_refresh_gmail():
    """At most every 10 min, and only when there were submissions in the last 6h, refresh the Gmail cache in a
    detached process. Hooks inherit the worker's logged-in Claude Code context; the launchd auditor does not."""
    stamp = os.path.join(BASE, '.gmail_refresh')
    try:
        if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 600: return
        if not any(e.get('event') == 'submitted' and time.time() - e.get('ts', 0) < 24 * 3600 for e in ledger_entries()): return
        open(stamp, 'w').write(str(time.time()))
        env = dict(os.environ); env.pop('AUTOAPPLY_NO_LIVE', None)
        subprocess.Popen([sys.executable, os.path.join(BASE, 'bin', 'gmail_confirm.py'), '--refresh'], env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        log('GMAIL_REFRESH_SPAWNED')
    except Exception as e:
        log('GMAIL_REFRESH_ERROR', err=str(e))

def main():
    h = json.load(sys.stdin)
    sync_mirror()
    tool = h.get('tool_name', '')
    inp = h.get('tool_input', {}) or {}
    strings = list(walk(inp))
    worker = is_worker(h)
    if worker: maybe_refresh_gmail()

    # --- HALT flag: remediation mode. Browser opens only once every must_pass resume passes, and then only for
    #     the listings being resubmitted; the flag itself can only be removed by clear_halt.py ---
    halt = None
    if worker and os.path.exists(HALT):
        try: halt = json.load(open(HALT))
        except Exception: halt = dict(id='legacy', must_pass=[], critical=[open(HALT).read()[:200]], how_to_clear='')
        why = f"HALT {halt.get('id')}: " + ' || '.join(halt.get('critical', [])[:3]) + '. ' + halt.get('how_to_clear', '')
        _c = str(inp.get('command', ''))
        _halt_write = (re.search(r'\b(rm|mv|cp|unlink|truncate|touch|chmod|ln)\b[^;&|\n]*(?<![A-Za-z_])HALT\b(?!\s+\d{8}-\d{4})', _c)
                       or re.search(r'>{1,2}\s*["\']?[^\s;&|"\']*(?<![A-Za-z_])HALT\b', _c)
                       or re.search(r'(os\.remove|os\.unlink|os\.rename|os\.replace|shutil\.\w+)\([^)]*HALT', _c)
                       or re.search(r'open\([^)]*HALT[^)]*["\'][wax]', _c))
        if tool == 'Bash' and _halt_write and 'clear_halt.py' not in _c:
            deny('the HALT file may only be removed by clear_halt.py after the listed conditions are met. ' + why)
        if tool in ('Write', 'Edit') and str(inp.get('file_path', '')).rstrip('/').endswith('/HALT'):
            deny('the HALT file may only be removed by clear_halt.py after the listed conditions are met. ' + why)
        if (tool.startswith('mcp__claude-in-chrome__') or tool.startswith('mcp__computer-use__')) and try_auto_clear(halt):
            halt = None
        elif tool.startswith('mcp__claude-in-chrome__') or tool.startswith('mcp__computer-use__'):
            resubmits = [e for e in halt.get('must_pass', []) if isinstance(e, dict) and e.get('resubmit')]
            if not resubmits:
                # nothing needs the browser to remediate: stop everything until the worker writes its account and clears
                deny('worker is HALTED; all browser/desktop actions are blocked until the HALT is cleared. Stop the current listing now. ' + why)
            pending = halt_pending_resumes(halt)
            if pending:
                deny('worker is HALTED; browser stays closed until every must_pass resume passes the validator. Still failing: '
                     + '; '.join(pending)[:500] + '. ' + why)
            # remediation browsing: no search/results pages, no new listings
            for path, sv in strings:
                if re.search(r'linkedin\.com/(jobs/(search|collections)|jobs/?\?|feed)', sv, re.I):
                    deny('HALT remediation mode: only the listings being resubmitted may be opened (their linkedin_url in HALT, or the job page reached by company+title). '
                         'No search-results browsing until clear_halt.py has cleared the HALT. ' + why)

    # --- Resume upload gate (all sessions) ---
    if tool == 'mcp__claude-in-chrome__file_upload':
        check_resume_upload(inp.get('paths', []) or [], h, inp.get('tabId'))
    if tool == 'mcp__claude-in-chrome__browser_batch':
        for a in inp.get('actions', []) or []:
            if a.get('name') in ('file_upload', 'mcp__claude-in-chrome__file_upload'):
                check_resume_upload((a.get('input') or {}).get('paths', []) or [], h, (a.get('input') or {}).get('tabId'))

    if not worker:
        return

    # --- evidence integrity: the worker may not WRITE hook-owned records (reading them, or writing elsewhere, is fine) ---
    _FILES = r'(guard_log\.jsonl|\.halt_cache\.json|\.halt_clear_try|gmail_cache\.json(?:\.targeted)?|gmail_refresh\.json|active_resume\.json|stop_guard_state\.json)'
    _STAMPS = r'(validated_upload|ALLOW_SUBMIT|ALLOW_UPLOAD|GO_TO_COMPANY_SITE|HALT_AUTO_CLEARED)'
    def _writes_to(target, c):
        return bool(re.search(r'>{1,2}\s*["\']?[^\s;&|"\']*' + target, c)
                    or re.search(r'\b(rm|mv|cp|tee|truncate|touch|unlink)\b[^;&|\n]*' + target, c)
                    or re.search(r'\bsed\s+-i\b[^;&|\n]*' + target, c)
                    or re.search(r'open\([^)]*' + target + r'[^)]*["\'][wax]\+?["\']', c)
                    or re.search(r'(os\.remove|os\.unlink|os\.rename|os\.replace|shutil\.\w+|write_text|Path)\([^)]*' + target, c))
    if tool == 'Bash':
        _c = str(inp.get('command', ''))
        if _writes_to(_FILES, _c) or (re.search(_STAMPS, _c) and _writes_to(r'ledger\.jsonl', _c)):
            deny('these records are written by the supervisor hooks only; the worker may not create or edit them (reading them is allowed). '
                 'After a successful file_upload the guard already writes the validated_upload stamp itself; to log your own note, use a different event name such as "upload_done".')
    if tool in ('Write', 'Edit'):
        _fp = str(inp.get('file_path', ''))
        if re.search(_FILES + r'$', _fp) or (_fp.endswith('ledger.jsonl') and (tool == 'Write' or re.search(_STAMPS, json.dumps(inp)))):
            deny('these records are written by the supervisor hooks only; the worker may not create or edit them (reading them is allowed). Append ledger entries with a shell command instead of rewriting the file.')

    # --- no holds/skips on office attendance Andy already accepted (standing answer: in-office up to 4 days/week, given in the worker chat 2026-09-14) ---
    _OFFICE = re.compile(r"((commit|acknowledg|willing|able to work|requires?|in-person|onsite|on-site|in-office|in office|office|hybrid|commute)[^\n]{0,70}(\d\s*(\+|-\s*\d)?\s*(x|days?)\s*(/|per|a|each)?\s*(week|wk)|\b(two|three|four)\s+days|days?\s*(per|a|/)\s*week|hybrid'?\s*(yes/no)?|mon/|tue/|office\s+commute|not acknowledged))", re.I)
    _FIVE = re.compile(r"(\b5\s*[-+]?\s*days?|\bfive[\s-]+days?|\b5x|every day|fully on-?site|on-?site full[- ]time|relocat)", re.I)
    _HOLDWORD = re.compile(r'(\bHELD\b|\bSKIPPED\b|"event"\s*:\s*"(held|skipped)")')
    _EXEMPT = re.compile(r'(USER INSTRUCTION|REOPENED|standing answer|CORRECTION)', re.I)
    def _office_hold_lines(text):
        out = []
        for ln in str(text).splitlines():
            if _HOLDWORD.search(ln) and not _EXEMPT.search(ln) and not _FIVE.search(ln):
                m = _OFFICE.search(ln)
                if m and re.search(r'(office|onsite|on-site|in-person|hybrid|commute)', m.group(0), re.I) and re.search(r'(commit|acknowledg|willing|able to|requires?|yes/no|not acknowledged)', ln, re.I):
                    out.append(ln.strip()[:200])
        return out
    _office_text = None
    if tool == 'Bash' and _writes_to(r'(cron_pass_log\.txt|ledger\.jsonl)', str(inp.get('command', ''))):
        _office_text = str(inp.get('command', ''))
    elif tool in ('Write', 'Edit') and str(inp.get('file_path', '')).endswith(('cron_pass_log.txt', 'ledger.jsonl')):
        _office_text = str(inp.get('content', '')) + '\n' + str(inp.get('new_string', ''))
    if _office_text:
        _bad = _office_hold_lines(_office_text)
        if _bad:
            deny('this hold/skip cites an office-attendance requirement of 4 days a week or less. Andy already answered that in your chat on 2026-09-14 '
                 '("i am open to 4 days in the office, go ahead and apply"): answer Yes to in-office/hybrid questions up to 4 days/week and keep applying. '
                 'Andy (2026-09-14): never assume anything he did not say. Hold or skip only for his stated reasons: 7+ YOE, Gusto, staffing/undisclosed employer, already applied, '
                 'or a form item that needs his own answer (essay, legal name, home address, consent/attestation, unsupported experience). '
                 'If one of those also applies, log only that reason. Offending line: ' + _bad[0])

    # --- hook-observed remediation step: clicking the LinkedIn "Go to company site" link ---
    if any(re.search(r'go to company site', sv, re.I) for _, sv in strings):
        log('GO_TO_COMPANY_SITE')

    # --- No direct ATS navigation (must come from LinkedIn's Apply button) ---
    for path, s in strings:
        if ATS_RE.search(s):
            if tool in ('mcp__claude-in-chrome__navigate', 'mcp__claude-in-chrome__browser_batch', 'mcp__claude-in-chrome__javascript_tool', 'Bash', 'mcp__claude-in-chrome__tabs_create_mcp'):
                if tool == 'mcp__claude-in-chrome__browser_batch' and not re.search(r'/actions\[\d+\]/input/url$', path) and 'javascript' not in path:
                    continue
                if tool == 'Bash' and not re.search(r'(?:^|[;&|(]\s*|\s)(?:open(?:\s+-a\s+(?:"[^"]+"|\S+))?|curl|wget|xdg-open)\s+[^;&|\n]*' + ATS_RE.pattern.split('*', 1)[1] if False else r'(?:^|[;&|(]\s*|\s)(?:open(?:\s+-a\s+(?:"[^"]+"|\S+))?|curl|wget|xdg-open)\s+[^;&|\n]*https?://[^\s"\']*(ashbyhq\.com|greenhouse\.io|rippling\.com|lever\.co|myworkdayjobs\.com|workable\.com|smartrecruiters\.com|jobvite\.com|bamboohr\.com|icims\.com|wellfound\.com|avature\.net|successfactors\.com|applytojob\.com|breezy\.hr|dover\.com)', s, re.I): continue
                deny(f'direct navigation to an ATS URL ({ATS_RE.search(s).group(0)[:80]}) is not allowed. Reach the application form only by clicking the Apply button on the LinkedIn listing (job_apply_via_linkedin). To reload an ATS tab, press the browser reload key (cmd+r) via the computer tool instead.')

    # --- Listings and JDs are read in the browser, job by job (Andy, 2026-09-11: "block it, go through the browser job by job") ---
    NET_RE = re.compile(r'(\bcurl\b|\bwget\b|requests\.(get|post)|urllib|httpx|aiohttp)[^\n]{0,300}(linkedin\.com|jobs-guest)|(linkedin\.com|jobs-guest)[^\n]{0,300}(\bcurl\b|\bwget\b|requests\.|urllib|httpx)', re.I)
    JS_RE = re.compile(r'jobs-guest|fetch\([^)]*linkedin|XMLHttpRequest|fetch\(\s*[\'"`]/', re.I)
    scrape = False
    if tool == 'mcp__claude-in-chrome__javascript_tool':
        scrape = bool(JS_RE.search(str(inp.get('text', ''))))
    elif tool == 'mcp__claude-in-chrome__browser_batch':
        scrape = any(str(ac.get('name', '')).endswith('javascript_tool') and JS_RE.search(str((ac.get('input') or {}).get('text', ''))) for ac in (inp.get('actions') or []))
    elif tool == 'Bash':
        scrape = bool(NET_RE.search(str(inp.get('command', ''))))
    elif tool in ('Write', 'Edit') and not str(inp.get('file_path', '')).endswith(('cron_pass_log.txt', 'ledger.jsonl', '.md')):
        scrape = bool(NET_RE.search(json.dumps(inp)))
    if scrape:
        deny('fetching LinkedIn listings or job descriptions with curl/HTTP/fetch (including the jobs-guest API) is not allowed. '
             'Read the search results and every listing IN THE BROWSER, page by page and job by job: navigate to the listing, wait ~5s, get_page_text, '
             'reload up to 3 times if still loading (autoapply_process.md). Save that page text as the JD file.')

    # --- one resume at a time: starting or editing a second resume HTML while another is unfinished ---
    if tool in ('Write', 'Edit') and str(inp.get('file_path', '')).lower().endswith(('.html', '.htm')):
        check_one_resume_at_a_time(str(inp.get('file_path')))
    if tool == 'Bash':
        # remember which resume PDF the active HTML became, so abbreviated file names (wk_jpmc_...) still match their company
        for _m2 in re.finditer(r'\bcp\s+"?([^"\s]+\.pdf)"?\s+"([^"]*/Resume - [^"]+\.pdf)"', str(inp.get('command', ''))):
            try:
                _st = json.load(open(ACTIVE)); _a = _st.get('active')
                if _a and os.path.splitext(os.path.basename(_a['path']))[0] == os.path.splitext(os.path.basename(_m2.group(1)))[0]:
                    _a['resume_path'] = _m2.group(2); json.dump(_st, open(ACTIVE, 'w'))
            except Exception:
                pass
        _m = re.search(r'\bcp\s+[^;&|\n]*?\s("?)([^\s;&|"]+\.html?)\1\s*(?:$|[;&|])', str(inp.get('command', '')))
        if _m:
            _dest = os.path.expanduser(_m.group(2))
            _cd = re.search(r'\bcd\s+"?([^\s;&|"]+)"?\s*(?:&&|;)', str(inp.get('command', ''))[:_m.start()])
            if not os.path.isabs(_dest) and _cd:
                _dest = os.path.join(os.path.expanduser(_cd.group(1)), _dest)
            check_one_resume_at_a_time(_dest, str(inp.get('command', '')))

    # --- No resume generator scripts / batch content files ---
    if tool in ('Write', 'Edit'):
        fp = str(inp.get('file_path', ''))
        content = str(inp.get('content', '') or inp.get('new_string', ''))
        ext = os.path.splitext(fp)[1].lower()
        if ext in ('.py', '.js', '.ts', '.sh', '.json', '.yaml', '.yml', '.csv', '.txt', '.md') and looks_like_generator(content) and 'cron_pass_log' not in fp and not any(d in fp for d in DOC_PATHS):
            deny(f'{os.path.basename(fp)} contains resume content for multiple employers; batch resume generators and content files are forbidden (hard_rule_no_resume_shortcuts rule 3). Author each resume as its own HTML file, one at a time, from resume core.pdf.')
        if ext in ('.html', '.htm') and content.lower().count('andy chen') > 2:
            deny('an HTML file containing more than one resume is a batch; author one resume per file.')
    if tool == 'Bash':
        cmd = str(inp.get('command', ''))
        if looks_like_generator(cmd) and re.search(r'cat\s*>|<<\s*[\'"]?\w+|tee\s|python3?\s+-c|\.py\b', cmd) and 'validate_resume' not in cmd:
            deny('this shell command writes or runs a script carrying resume content for multiple employers; generators are forbidden. Render only: headless Chrome from a single hand-authored HTML file.')
        if re.search(r'python3?\s+\S*gen\w*\.py', cmd): deny('running a resume generator script is forbidden.')
        # one resume at a time: a render loop or a multi-file render is batch authoring
        n_outputs = len(re.findall(r'--print-to-pdf=', cmd))
        n_inputs = len(set(re.findall(r'[^\s"\'=]+\.html?\b', cmd)))
        if n_outputs and (re.search(r'\bfor\b.*\b(do|in)\b', cmd) or n_outputs > 1 or n_inputs > 1):
            deny('this command renders more than one resume. Resumes are authored and rendered ONE AT A TIME, each with its JD open and visually checked before the next is started (hard_rule_no_resume_shortcuts rule 4). Render a single HTML file per command.')
        _html_dests = set(re.findall(r'\bcp\s+[^;&|\n]*?\s"?([^\s;&|"]+\.html?)"?\s*(?=$|[;&|\n])', cmd, re.M))
        _loop = re.search(r'\bfor\s+\w+\s+in\b|\bwhile\b[^\n]*\bdo\b|\bxargs\b', cmd)
        if len(_html_dests) >= 2 or (_loop and re.search(r'\bcp\b[^\n]*\.html?\b', cmd)):
            deny('copying a base resume HTML to multiple files is template authoring; start each resume from resume core.pdf content individually.')

    # --- Never stop a pass to ask ---
    if tool == 'AskUserQuestion':
        deny('AskUserQuestion is not allowed during an autoapply pass. Write the question/judgment call into cron_pass_log.txt as "FLAG: ..." and keep going (feedback_never_stop_passes).')

    # --- Submit gate: a validated upload must exist for the current application ---
    if tool in ('mcp__claude-in-chrome__find', 'mcp__claude-in-chrome__browser_batch', 'mcp__claude-in-chrome__javascript_tool', 'mcp__claude-in-chrome__computer'):
        _texts = []
        def _collect(name, ai):
            name = str(name).replace('mcp__claude-in-chrome__', '')
            if name == 'find': _texts.append(('query', str(ai.get('query', '')), ai.get('tabId')))
            elif name == 'computer' and ai.get('action') == 'type': _texts.append(('type', str(ai.get('text', '')), ai.get('tabId')))
            elif name == 'javascript_tool': _texts.append(('js', str(ai.get('text', '')), ai.get('tabId')))
        if tool.endswith('browser_batch'):
            for _ac in (inp.get('actions') or []): _collect(_ac.get('name', ''), _ac.get('input') or {})
        else:
            _collect(tool, inp)
        def _is_submit(k, t):
            if not re.search(r'\bsubmit(?!ted)', t, re.I) or 'linkedin.com' in t: return False
            if k == 'js': return bool(re.search(r'\.click\(|\.submit\(|requestSubmit|dispatchEvent\(', t))
            if k == 'query' and re.search(r'\b(every|all|each|list|inventory|questions?|fields?|inputs?|labels?)\b', t, re.I): return False
            if k == 'query' and re.search(r'\bnot\s+(?:the\s+)?["\']?submit|\bcancel\b|\bclose\b|\bdismiss\b|\bexpander\b|\bback\b', t, re.I): return False
            return True
        _submit_items = [(k, t, tab) for k, t, tab in _texts if _is_submit(k, t)]
        submit_like = bool(_submit_items)
        submit_tabs = {tab for _, _, tab in _submit_items if tab is not None}
        if submit_like:
            ents = ledger_entries()
            recent = [e for e in ents if e.get('event') == 'validated_upload' and time.time() - _as_ts(e.get('ts', 0)) < 3 * 3600]
            submitted = {e.get('resume_path') for e in ents if e.get('event') == 'submitted'}
            # an upload is closed by a later 'submitted' for that file, or a later 'blocked'/'abandoned' for that company
            closers = [e for e in ents if e.get('event') in ('blocked', 'held', 'abandoned', 'skipped')]
            def _closed(u):
                co = (u.get('company') or '').lower()
                return u.get('resume_path') in submitted or any((c.get('company') or '').lower() == co and _as_ts(c.get('ts', 0)) > _as_ts(u.get('ts', 0)) for c in closers)
            open_apps = [e for e in recent if not _closed(e)]
            # the submit must happen in the same browser tab the validated resume was uploaded in
            if submit_tabs:
                _same_tab = [e for e in open_apps if e.get('tab_id') is None or e.get('tab_id') in submit_tabs]
                if open_apps and not _same_tab:
                    deny(f"submit in tab {sorted(submit_tabs)} but the open validated upload(s) were made in tab(s) {sorted({e.get('tab_id') for e in open_apps})}. "
                         "Upload this application's own tailored resume in THIS tab with file_upload before submitting it.")
                open_apps = _same_tab
            if halt:
                mp = {os.path.abspath(e['resume_path'] if isinstance(e, dict) else e) for e in halt.get('must_pass', [])}
                open_apps = [e for e in open_apps if os.path.abspath(e.get('resume_path', '')) in mp]
                if not open_apps: deny('HALT remediation mode: only resubmissions of the must_pass resumes may be submitted, and the rebuilt resume must be uploaded through file_upload first.')
            if not open_apps:
                deny('no validated resume upload is on record for an open application (ledger shows none in the last 3h that is not already marked submitted). Upload the validated tailored resume through file_upload first. If this is a LinkedIn native modal where the resume cannot be swapped, do not submit; flag the listing as blocked per autoapply_process.md.')
            log('ALLOW_SUBMIT', open_apps=[e.get('company') for e in open_apps], paths=[e.get('resume_path') for e in open_apps])

if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log('GUARD_ERROR', err=str(e))
        sys.stderr.write(f'autoapply guard internal error (call allowed): {e}\n')
        sys.exit(0)
