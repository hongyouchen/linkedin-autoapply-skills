#!/usr/bin/env python3
"""PreToolUse guard for the LinkedIn autoapply worker. Reads hook JSON on stdin.
Blocks (exit 2 + reason on stderr) when a call would violate a standing rule.
Only applies worker-scoped rules to sessions that look like autoapply worker sessions;
the resume-upload validator applies everywhere.
"""
import sys, os, json, re, time, subprocess

BASE = os.path.expanduser('~/.claude/autoapply')
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

def ledger_entries():
    out = []
    try:
        for line in open(LEDGER):
            line = line.strip()
            if line:
                try: out.append(json.loads(line))
                except Exception: pass
    except FileNotFoundError: pass
    return out

HALT_CACHE = os.path.join(BASE, '.halt_cache.json')

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

def check_resume_upload(paths, h):
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
        ents = [e for e in ledger_entries() if os.path.abspath(e.get('resume_path', '')) == os.path.abspath(p)]
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
        fn = os.path.basename(p).lower()
        if comp and comp.split()[0] not in fn: deny(f'ledger company {comp!r} does not match resume filename {fn!r}')
        if time.time() - e.get('ts', 0) > 4 * 3600: deny('ledger entry is older than 4 hours; write a fresh entry for this application.')
        # stamp
        try:
            with open(LEDGER, 'a') as f: f.write(json.dumps(dict(ts=time.time(), event='validated_upload', resume_path=p, company=e.get('company'), session=h.get('session_id'))) + '\n')
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

def main():
    h = json.load(sys.stdin)
    sync_mirror()
    tool = h.get('tool_name', '')
    inp = h.get('tool_input', {}) or {}
    strings = list(walk(inp))
    worker = is_worker(h)

    # --- HALT flag: remediation mode. Browser opens only once every must_pass resume passes, and then only for
    #     the listings being resubmitted; the flag itself can only be removed by clear_halt.py ---
    halt = None
    if worker and os.path.exists(HALT):
        try: halt = json.load(open(HALT))
        except Exception: halt = dict(id='legacy', must_pass=[], critical=[open(HALT).read()[:200]], how_to_clear='')
        why = f"HALT {halt.get('id')}: " + ' || '.join(halt.get('critical', [])[:3]) + '. ' + halt.get('how_to_clear', '')
        if tool == 'Bash' and re.search(r'\bHALT\b', str(inp.get('command', ''))) and 'clear_halt.py' not in str(inp.get('command', '')):
            deny('the HALT file may only be removed by clear_halt.py after the listed conditions are met. ' + why)
        if tool in ('Write', 'Edit') and str(inp.get('file_path', '')).rstrip('/').endswith('/HALT'):
            deny('the HALT file may only be removed by clear_halt.py after the listed conditions are met. ' + why)
        if tool.startswith('mcp__claude-in-chrome__') or tool.startswith('mcp__computer-use__'):
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
        check_resume_upload(inp.get('paths', []) or [], h)
    if tool == 'mcp__claude-in-chrome__browser_batch':
        for a in inp.get('actions', []) or []:
            if a.get('name') in ('file_upload', 'mcp__claude-in-chrome__file_upload'):
                check_resume_upload((a.get('input') or {}).get('paths', []) or [], h)

    if not worker:
        return

    # --- evidence integrity: the worker may not write hook-owned records ---
    blob = json.dumps(inp)
    if tool in ('Bash', 'Write', 'Edit') and re.search(r'validated_upload|ALLOW_SUBMIT|ALLOW_UPLOAD|GO_TO_COMPANY_SITE|guard_log\.jsonl|\.halt_cache', blob):
        deny('these records are written by the supervisor hooks only; the worker may not create or edit them.')

    # --- hook-observed remediation step: clicking the LinkedIn "Go to company site" link ---
    if any(re.search(r'go to company site', sv, re.I) for _, sv in strings):
        log('GO_TO_COMPANY_SITE')

    # --- No direct ATS navigation (must come from LinkedIn's Apply button) ---
    for path, s in strings:
        if ATS_RE.search(s):
            if tool in ('mcp__claude-in-chrome__navigate', 'mcp__claude-in-chrome__browser_batch', 'mcp__claude-in-chrome__javascript_tool', 'Bash', 'mcp__claude-in-chrome__tabs_create_mcp'):
                if tool == 'mcp__claude-in-chrome__browser_batch' and not re.search(r'/actions\[\d+\]/input/url$', path) and 'javascript' not in path:
                    continue
                if tool == 'Bash' and not re.search(r'\bopen\b|curl|wget', s): continue
                deny(f'direct navigation to an ATS URL ({ATS_RE.search(s).group(0)[:80]}) is not allowed. Reach the application form only by clicking the Apply button on the LinkedIn listing (job_apply_via_linkedin). To reload an ATS tab, press the browser reload key (cmd+r) via the computer tool instead.')

    # --- Listings and JDs are read in the browser, job by job (Andy, 2026-09-11: "block it, go through the browser job by job") ---
    SCRAPE_RE = re.compile(r'jobs-guest|(curl|wget|requests\.get|requests\.post|urllib|httpx|aiohttp|fetch\()[^\n]{0,300}linkedin\.com|linkedin\.com[^\n]{0,300}(curl|wget|requests\.|urllib|httpx)', re.I)
    if tool in ('Bash', 'Write', 'Edit', 'mcp__claude-in-chrome__javascript_tool'):
        if any(SCRAPE_RE.search(sv) for _, sv in strings):
            deny('fetching LinkedIn listings or job descriptions with curl/HTTP/fetch (including the jobs-guest API) is not allowed. '
                 'Read the search results and every listing IN THE BROWSER, page by page and job by job: navigate to the listing, wait ~5s, get_page_text, '
                 'reload up to 3 times if still loading (autoapply_process.md). Save that page text as the JD file.')

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
        if 'print-to-pdf' in cmd and (re.search(r'\bfor\b.*\b(do|in)\b', cmd) or cmd.count('print-to-pdf') > 1 or len(re.findall(r'\S+\.html?\b', cmd)) > 1):
            deny('this command renders more than one resume. Resumes are authored and rendered ONE AT A TIME, each with its JD open and visually checked before the next is started (hard_rule_no_resume_shortcuts rule 4). Render a single HTML file per command.')
        if re.search(r'\bcp\b[^|;&]*\.html?\b[^|;&]*\.html?\b', cmd) and re.search(r'\bfor\b', cmd):
            deny('copying a base resume HTML to multiple files is template authoring; start each resume from resume core.pdf content individually.')

    # --- Never stop a pass to ask ---
    if tool == 'AskUserQuestion':
        deny('AskUserQuestion is not allowed during an autoapply pass. Write the question/judgment call into cron_pass_log.txt as "FLAG: ..." and keep going (feedback_never_stop_passes).')

    # --- Submit gate: a validated upload must exist for the current application ---
    if tool in ('mcp__claude-in-chrome__find', 'mcp__claude-in-chrome__browser_batch', 'mcp__claude-in-chrome__javascript_tool', 'mcp__claude-in-chrome__computer'):
        submit_like = any(re.search(r'\bsubmit(?!ted)', s, re.I) and 'linkedin.com' not in s for p, s in strings if p.endswith('/query') or p.endswith('/text'))
        if submit_like:
            ents = ledger_entries()
            recent = [e for e in ents if e.get('event') == 'validated_upload' and time.time() - e.get('ts', 0) < 3 * 3600]
            submitted = {e.get('resume_path') for e in ents if e.get('event') == 'submitted'}
            open_apps = [e for e in recent if e.get('resume_path') not in submitted]
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
