#!/usr/bin/env python3
"""Independent auditor for the autoapply worker.

Reads worker transcripts + ledger + resume folder, checks every application in the window
against the process checklist, writes a JSON + HTML report, and drops a HALT flag on critical
violations (the PreToolUse guard honors HALT and stops the worker's browser tools).

Usage: audit.py [--since-hours N] [--no-halt] [--transcript PATH]
"""
import sys, os, re, json, glob, time, datetime, base64

BASE = os.path.expanduser('~/.claude/autoapply')
sys.path.insert(0, os.path.join(BASE, 'bin'))
from extract_events import extract  # noqa: E402
import validate_resume as V  # noqa: E402
import gmail_confirm as G  # noqa: E402

PROJ = os.path.expanduser('~/.claude/projects/-Users-hongyouchen')
RESUME_DIR = V.RESUME_DIR  # real folder when readable, else the guard-maintained mirror
REPORTS = os.path.join(BASE, 'reports')
STATE = os.path.join(BASE, 'audit_state.json')
ATS_RE = re.compile(r'(ashbyhq\.com|greenhouse\.io|rippling\.com|lever\.co|myworkdayjobs\.com|workable\.com|smartrecruiters\.com)', re.I)
SEV_ORDER = ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')


def first_user_text(p):
    try:
        with open(p, 'rb') as f:
            head = f.read(400000).decode('utf-8', 'ignore')
    except Exception:
        return ''
    for line in head.splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get('type') != 'user':
            continue
        c = o.get('message', {}).get('content')
        return c if isinstance(c, str) else ' '.join(x.get('text', '') for x in c if isinstance(x, dict) and x.get('type') == 'text')
    return ''


def is_worker_transcript(p):
    sid = os.path.basename(p).replace('.jsonl', '')
    try:
        if sid in open(os.path.join(BASE, 'worker_sessions.txt')).read():
            return True
    except Exception:
        pass
    return bool(re.search(r'recurring LinkedIn autoapply pass|auto-?apply campaign|autoapply_process\.md|Run the .{0,40}autoapply', first_user_text(p), re.I))


def fmt(t):
    return datetime.datetime.fromtimestamp(t).strftime('%m-%d %H:%M')


def audit_transcript(path, since, core):
    ev = extract(path, since)
    findings, apps = [], []

    def F(sev, t, what, detail=''):
        findings.append(dict(sev=sev, t=t, what=what, detail=detail, transcript=os.path.basename(path)))

    # applications, from two sources:
    #  (1) the ledger's structured {"event":"submitted"} records (primary; worker writes one per application)
    #  (2) APPLIED lines appended to the pass log, in the worker's formats:
    #      "PASS <id> | APPLIED | Company - Title | url ..."   "APPLIED: Company - Title ..."   "- APPLIED Company ... via"
    #      Lines that are grep/sed/cut/awk reads of the log, or page/pass summaries, are ignored.
    APPLIED_RES = [
        re.compile(r'PASS \S+ \| APPLIED \| (?P<co>[^|\n\'"]+?)\s+[-\u2013\u2014]\s'),
        re.compile(r'(?:^|[\'"\s])APPLIED:\s+(?P<co>[^|\n\'"(]+?)\s+[-\u2013\u2014]\s'),
        re.compile(r'(?:^|\n)\s*-\s+APPLIED\s+(?P<co>[A-Z][^|\n\'"(]+?)\s+(?:\(|via\s)'),
    ]
    seen_apps = set()
    def _key(co): 
        w = re.sub(r'[^a-z0-9 ]', ' ', co.lower()).split()
        return w[0] if w else ''
    t_lo = min((e['t'] for e in ev), default=since); t_hi = max((e['t'] for e in ev), default=time.time())
    try:
        if not ev: raise FileNotFoundError  # a transcript with no activity in the window owns no applications
        for l in open(os.path.join(BASE, 'ledger.jsonl')):
            try: le = json.loads(l)
            except Exception: continue
            if le.get('event') != 'submitted' or not le.get('company'): continue
            if not (max(since, t_lo - 60) <= le.get('ts', 0) <= t_hi + 60): continue
            k = (_key(le['company']), os.path.basename(le.get('resume_path', '')) or le.get('title', ''))
            if k in seen_apps: continue
            seen_apps.add(k)
            jid = re.search(r'(\d{9,11})', le.get('linkedin_url', '') or '')
            apps.append(dict(t=le['ts'], company=le['company'][:60], title=le.get('title', ''), resume_path=le.get('resume_path'),
                             linkedin_url=(f'https://www.linkedin.com/jobs/view/{jid.group(1)}/' if jid else None), source='ledger'))
    except FileNotFoundError:
        pass
    ledger_cos = {_key(a['company']) for a in apps}
    for e in ev:
        lt = e.get('log_text')
        if not lt or 'cron_pass_log' not in lt: continue
        for line in lt.split('\n'):
            if 'APPLIED' not in line or re.search(r'\b(grep|sed|cut|awk)\b|COMPLETE|Tally', line): continue
            for rx in APPLIED_RES:
                m = rx.search(line)
                if not m: continue
                co = m.group('co').strip()
                if not re.search(r'[A-Za-z]{2}', co) or _key(co) in ledger_cos: break
                jid = re.search(r'jobs/view/(\d{9,11})|\b(\d{10})\b', line)
                jid = (jid.group(1) or jid.group(2)) if jid else None
                k = (_key(co), jid)
                if k in seen_apps: break
                seen_apps.add(k); ledger_cos.add(_key(co))
                apps.append(dict(t=e['t'], company=co[:60], linkedin_url=(f'https://www.linkedin.com/jobs/view/{jid}/' if jid else None), source='log'))
                break
    apps.sort(key=lambda a: a['t'])

    # pass-level checks
    for e in ev:
        if e.get('log_text') and re.search(r'PASS PAUSED budget:', e['log_text']):
            later = [x for x in ev if x['t'] > e['t'] + 20 and x['t'] < e['t'] + 1800 and x['tool'] not in ('RESULT', 'USER_TEXT')]
            if later:
                F('CRITICAL', e['t'], f'FALSE budget pause: worker claimed tokens were exhausted, then made {len(later)} more tool calls within 30 min', e['log_text'][e['log_text'].find('PASS PAUSED'):][:160])
            else:
                F('MEDIUM', e['t'], 'Worker paused a pass citing token budget (only accepted stop reason; verified no activity followed)', e['log_text'][e['log_text'].find('PASS PAUSED'):][:160])
        if e['tool'] == 'Bash' and re.search(r'(curl|wget|requests\.get|urllib)[^\n]{0,200}linkedin\.com', e.get('cmd', ''), re.I):
            F('CRITICAL', e['t'], 'Fetched LinkedIn pages with curl/HTTP instead of the browser (Andy: read listings in the browser, job by job)', e['cmd'][:160])
        if e['tool'] == 'javascript_tool' and re.search(r'jobs-guest|fetch\([^)]*linkedin', e.get('js', ''), re.I):
            F('CRITICAL', e['t'], 'Fetched LinkedIn data via page JavaScript instead of reading listings in the browser', e['js'][:160])
        if e['tool'] == 'read_network_requests' and re.search(r'voyager|jobs|linkedin', e.get('pattern', ''), re.I):
            F('MEDIUM', e['t'], 'Read LinkedIn API responses from the browser network log instead of the rendered page (JD must come from get_page_text)', e.get('pattern', ''))
        if e['tool'] == 'AskUserQuestion':
            F('HIGH', e['t'], 'Stopped pass to ask a question', e.get('q', ''))
        if e['tool'] in ('Write', 'Edit') and e.get('employers', 0) >= 2 and not e['path'].lower().endswith(('.html', '.htm', '.md')):
            F('CRITICAL', e['t'], 'Resume generator / batch content file written', e['path'])
        if e['tool'] in ('Write', 'Edit') and e.get('resume_html', 0) > 2:
            F('CRITICAL', e['t'], 'Multiple resumes in one HTML file', e['path'])
        if e['tool'] == 'Bash' and re.search(r'python3?\s+\S*gen\w*\.py', e.get('cmd', '')):
            F('CRITICAL', e['t'], 'Ran a resume generator script', e['cmd'][:120])
        if e['tool'] == 'navigate' and ATS_RE.search(e.get('url', '')):
            F('CRITICAL', e['t'], 'Direct navigation to ATS URL (bypassed LinkedIn Apply)', e['url'][:120])
        if e['tool'] == 'javascript_tool' and re.search(r'location\.(href|assign|replace)|window\.open', e.get('js', '')) and ATS_RE.search(e.get('js', '')):
            F('CRITICAL', e['t'], 'JS navigation to ATS URL', e['js'][:120])
        if e['tool'] == 'ASSISTANT_TEXT' and re.search(r"(won'?t|did ?n'?t|does ?n'?t|not) (render|load)|skeleton", e['text'], re.I):
            F('LOW', e['t'], 'Claimed a listing did not load (verify: these claims were wrong twice)', e['text'][:200])
    pages = sorted({int(m.group(1)) for e in ev if e['tool'] == 'navigate' for m in [re.search(r'[?&]start=(\d+)', e.get('url', ''))] if m})

    # batch authoring: several distinct resume HTML files touched (Write or Edit) inside one 90s window,
    # or a render command that renders more than one file
    html_touch = [e for e in ev if e['tool'] in ('Write', 'Edit') and e['path'].lower().endswith(('.html', '.htm'))]
    for i, e in enumerate(html_touch):
        window = {x['path'] for x in html_touch[i:] if x['t'] - e['t'] <= 90}
        if len(window) >= 3:
            F('HIGH', e['t'], f'{len(window)} different resume HTML files edited within 90s (batch authoring, not one at a time)', ', '.join(sorted(os.path.basename(p) for p in window))[:200])
            break
    for e in ev:
        if e['tool'] == 'Bash' and e.get('render') and (re.search(r'\bfor\b.*\b(do|in)\b', e.get('cmd', '')) or e.get('cmd', '').count('print-to-pdf') > 1):
            F('HIGH', e['t'], 'Rendered several resumes in one command (batch)', e['cmd'][:160])

    # per-application checks
    gmail_threads = G.confirmations() if apps else []
    prev_t = since
    for a in apps:
        w = [e for e in ev if prev_t < e['t'] <= a['t'] + 900]
        wb = [e for e in w if e['t'] <= a['t']]
        comp = _key(a['company'])
        checks = {
            'core_reread': any(e['tool'] == 'Read' and 'resume core.pdf' in e.get('path', '') and a['t'] - e['t'] < 7200 for e in ev if e['t'] <= a['t']),
            'jd_read': any(e['tool'] in ('get_page_text', 'find', 'read_page') for e in wb) and any(e['tool'] == 'navigate' and 'linkedin.com/jobs' in e.get('url', '') for e in wb),
            'resume_authored': any(e['tool'] == 'Write' and e['path'].lower().endswith('.html') for e in wb),
            'pdf_visually_checked': any(e['tool'] == 'Read' and e.get('path', '').lower().endswith('.pdf') and 'claude resumes' in e.get('path', '').lower() for e in wb),
            'resume_uploaded': any(e['tool'] == 'file_upload' and any('Claude Resumes' in p for p in e.get('paths', [])) for e in wb),
            'linkedin_yes_confirmed': any(e['tool'] == 'find' and re.search(r'finish applying|did you finish|\byes\b', e.get('query', ''), re.I) for e in w)
            or any(e['tool'] == 'ASSISTANT_TEXT' and re.search(r'finish applying.*yes|clicked yes|applied badge|shows applied', e['text'], re.I) for e in w),
            'success_screen_reported': any(e['tool'] == 'ASSISTANT_TEXT' and re.search(r'application (was )?submitted|submitted successfully|thank you for (applying|your application)|confirmation (page|screen)|/confirmation', e['text'], re.I) for e in w),
        }
        # Gmail: every real submission produces a confirmation email (Andy, 2026-09-11). A FAILED lookup is "unknown", never "missing".
        age_min = (time.time() - a['t']) / 60
        mail, lookup_ok = None, gmail_threads is not None
        try:
            if lookup_ok: mail = G.confirmed(a['company'], a['t'], gmail_threads)
            if not mail:
                tgt = G.targeted([a['company']])
                if tgt is None: lookup_ok = False
                else: mail = G.confirmed(a['company'], a['t'], tgt)
        except Exception as ex:
            lookup_ok = False
            F('LOW', a['t'], f"{a['company']}: Gmail lookup error ({ex})", '')
        checks['gmail_confirmation'] = bool(mail) if lookup_ok or mail else None
        a['gmail'] = dict(subject=mail.get('subject'), from_=mail.get('from'), date=mail.get('date')) if mail else None
        a['gmail_lookup_ok'] = lookup_ok
        a['gmail_pending'] = not mail
        rp = a.get('resume_path')
        rp_resolved = V.resolve(rp) if rp else None
        files = [rp_resolved] if rp_resolved and os.path.exists(rp_resolved) else [f for f in glob.glob(os.path.join(RESUME_DIR, '*.pdf')) if comp and comp in os.path.basename(f).lower()]
        val = None
        if files:
            f = max(files, key=os.path.getmtime)
            val = V.validate(f, core, check_dupes=True)
            val['file'] = os.path.basename(f)
        a['checks'] = checks
        a['validation'] = val
        missing = [k for k, v in checks.items() if v is False and k != 'gmail_confirmation']
        if val and not val['ok']:
            F('CRITICAL', a['t'], f"Applied to {a['company']} with a resume that FAILS validation", val['file'] + ': ' + ' | '.join(val['fails'])[:400])
        if not checks['resume_uploaded'] and not checks['jd_read']:
            F('HIGH', a['t'], f"{a['company']}: no JD read and no resume upload seen before APPLIED", '')
        elif missing:
            F('MEDIUM', a['t'], f"{a['company']}: missing evidence for {', '.join(missing)}", '')
        prev_t = a['t']
    return dict(transcript=os.path.basename(path), events=len(ev), applications=apps, findings=findings, pages_visited=pages)


def render_png(pdf):
    try:
        d = V.fitz.open(pdf)
        pix = d[0].get_pixmap(matrix=V.fitz.Matrix(0.6, 0.6))
        return base64.b64encode(pix.tobytes('png')).decode()
    except Exception:
        return ''


def main():
    args = sys.argv[1:]
    hours = float(args[args.index('--since-hours') + 1]) if '--since-hours' in args else None
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    since = time.time() - hours * 3600 if hours else st.get('last_run', time.time() - 6 * 3600)
    if '--transcript' in args:
        paths = [args[args.index('--transcript') + 1]]
    else:
        paths = [p for p in glob.glob(os.path.join(PROJ, '*.jsonl')) if os.path.getmtime(p) >= since - 60 and is_worker_transcript(p)]
    core = V.load_core()
    results = []
    for p in paths:
        try:
            results.append(audit_transcript(p, since, core))
        except Exception as ex:
            import traceback
            results.append(dict(transcript=os.path.basename(p), events=0, applications=[], pages_visited=[],
                                findings=[dict(sev='HIGH', t=time.time(), transcript=os.path.basename(p), what='Auditor error on this transcript; checks incomplete this run',
                                               detail=f'{type(ex).__name__}: {ex} | ' + traceback.format_exc().splitlines()[-3][:150])]))

    new_files = [f for f in glob.glob(os.path.join(RESUME_DIR, '*.pdf')) if os.path.getmtime(f) >= since]
    file_results = []
    for f in sorted(new_files, key=os.path.getmtime):
        r = V.validate(f, core, check_dupes=True)
        r['file'] = os.path.basename(f)
        r['mtime'] = os.path.getmtime(f)
        file_results.append(r)

    # worker idle: no tool activity for 20+ minutes without an accepted stop reason in the log
    try:
        tail = [l.rstrip() for l in open(os.path.join(BASE, 'cron_pass_log.txt'), errors='ignore') if l.strip()][-50:]
    except Exception: tail = []
    accepted_stop = any(re.match(r'PASS (PAUSED budget:|TERMINATED)', l) for l in tail[-3:])
    worker_paths = [p for p in glob.glob(os.path.join(PROJ, '*.jsonl')) if is_worker_transcript(p)]
    last_act = max((os.path.getmtime(p) for p in worker_paths), default=0)
    idle_min = (time.time() - last_act) / 60 if last_act else None
    if idle_min is not None and idle_min > 20 and not accepted_stop and not os.path.exists(os.path.join(BASE, 'HALT')):
        results.append(dict(transcript='(all workers)', events=0, applications=[], pages_visited=[],
                            findings=[dict(sev='HIGH', t=last_act, transcript='(all workers)', what=f'Worker idle for {int(idle_min)} min with no budget pause or termination logged',
                                           detail='no worker session is running; the Stop hook only holds a live session, so re-prompt the worker (or re-create its cron) to resume')]))
    try:
        gl = [json.loads(l) for l in open(os.path.join(BASE, 'guard_log.jsonl')) if l.strip()]
        stops = [g for g in gl if g.get('kind', '').startswith('STOP_BLOCKED') and g.get('ts', 0) >= since]
        if len(stops) >= 5:
            results.append(dict(transcript='(guard)', events=0, applications=[], pages_visited=[],
                                findings=[dict(sev='HIGH', t=stops[-1]['ts'], transcript='(guard)', what=f'Worker tried to stop {len(stops)} times in this window (each refused by the Stop hook)', detail='it is looping on stopping instead of applying; check latest.html for what it is stuck on')]))
    except Exception: pass
    # the same application can surface from two worker transcripts; keep the first
    _seen = set()
    for r in results:
        keep = []
        for a in r['applications']:
            k = (re.sub(r'[^a-z0-9]', '', a['company'].lower())[:20], os.path.basename(a.get('resume_path') or '') or a.get('title', '') or a.get('linkedin_url') or '')
            if k in _seen:
                r['findings'] = [f for f in r['findings'] if not (abs(f['t'] - a['t']) < 1 and a['company'] in f['what'])]
                continue
            _seen.add(k); keep.append(a)
        r['applications'] = keep
    # ---- Gmail pending-confirmation tracker: carried across runs, because each run only sees a 5-minute window ----
    pending = st.get('pending', {})
    for r in results:
        for a in r['applications']:
            if a.get('gmail_pending'):
                k = f"{a['company']}|{os.path.basename(a.get('resume_path') or '') or int(a['t'])}"
                pending.setdefault(k, dict(company=a['company'], ts=a['t'], resume_path=a.get('resume_path'), linkedin_url=a.get('linkedin_url')))
    gm_f, escalated, now = [], [], time.time()
    threads = G.confirmations() if pending else []
    for k, pe in list(pending.items()):
        mail = None
        try:
            mail = G.confirmed(pe['company'], pe['ts'], threads or []) or G.confirmed(pe['company'], pe['ts'], G.targeted([pe['company']]) or [])
        except Exception:
            pass
        age = (now - pe['ts']) / 60
        if mail:
            pending.pop(k, None); continue
        if age > 24 * 60:
            gm_f.append(dict(sev='HIGH', t=pe['ts'], transcript='(gmail)', what=f"{pe['company']}: no confirmation email after 24h and it could not be verified; dropped from tracking", detail=''))
            pending.pop(k, None); continue
        if age > 90 and threads is not None and G.cache_fetched() >= pe['ts'] + 1800 and G.covered(pe['company']):
            gm_f.append(dict(sev='CRITICAL', t=pe['ts'], transcript='(gmail)', what=f"{pe['company']}: no confirmation email in Gmail {int(age)} min after APPLIED", detail='company-specific search found nothing: either the application never went through, or the worker mis-reported it'))
            escalated.append(pe)
            if '--no-halt' not in args: pending.pop(k, None)
        elif age > 30:
            gm_f.append(dict(sev='LOW', t=pe['ts'], transcript='(gmail)', what=f"{pe['company']}: confirmation email pending ({int(age)} min)", detail=''))
    if gm_f:
        results.append(dict(transcript='(gmail)', events=0, applications=[], pages_visited=[], findings=gm_f))
    all_f = [f for r in results for f in r['findings']]
    crit = [f for f in all_f if f['sev'] == 'CRITICAL']
    summary = dict(run_at=time.time(), since=since, transcripts=[r['transcript'] for r in results],
                   applications=sum(len(r['applications']) for r in results), findings=len(all_f), critical=len(crit),
                   resumes_written=len(file_results), resumes_failing=sum(1 for r in file_results if not r['ok']),
                   pages=[r['pages_visited'] for r in results])
    halted = False
    if crit and '--no-halt' not in args:
        halt_id = datetime.datetime.now().strftime('%Y%m%d-%H%M')
        must_pass, seen = [], set()
        for r in results:
            for a in r['applications']:
                if a.get('validation') and not a['validation']['ok']:
                    fp = os.path.join(RESUME_DIR, a['validation']['file'])
                    if fp in seen: continue
                    seen.add(fp)
                    must_pass.append(dict(company=a['company'], resume_path=fp, linkedin_url=a.get('linkedin_url'), resubmit=True))
        for r in file_results:
            fp = os.path.join(RESUME_DIR, r['file'])
            if not r['ok'] and fp not in seen:
                seen.add(fp); must_pass.append(dict(company=V.company_of(fp), resume_path=fp, linkedin_url=None, resubmit=False))
        for esc in escalated:
            fp = V.resolve(esc['resume_path']) if esc.get('resume_path') else None
            if fp and fp not in seen:
                seen.add(fp); must_pass.append(dict(company=esc['company'], resume_path=esc['resume_path'], linkedin_url=esc.get('linkedin_url'), resubmit=True,
                                                    reason='no Gmail confirmation after 90 min (company-specific search): verify on the ATS; if it was never submitted, apply again via the LinkedIn listing'))
        must_flag = [c['what'] + ': ' + c['detail'][:120] for c in crit if 'FAILS validation' not in c['what'] and 'no confirmation email' not in c['what']]
        halt = dict(id=halt_id, ts=time.time(), critical=[c['what'] + ': ' + c['detail'][:150] for c in crit[:10]],
                    must_pass=must_pass, must_flag=must_flag,
                    how_to_clear=(f"For each must_pass entry: 1) rebuild the resume individually from resume core.pdf with its JD open until "
                                  f"`python3 ~/.claude/autoapply/bin/validate_resume.py <resume_path>` passes (browser stays blocked until ALL must_pass resumes pass). "
                                  f"2) If resubmit is true: open the LinkedIn listing (linkedin_url, or find it by company+title; search-results pages stay blocked), "
                                  f"click the job title, click the blue 'Go to company site' link under Application status, write a fresh ledger entry, upload the rebuilt resume, "
                                  f"submit the application again, then append a ledger line {{\"event\":\"submitted\",\"resume_path\":...,\"company\":...,\"resubmission\":true}} "
                                  f"and a log line 'RESUBMITTED <company>'. 3) If must_flag is non-empty, append one line "
                                  f"'REMEDIATED HALT {halt_id}: <what happened, which listings, what you did>' to ~/.claude/autoapply/cron_pass_log.txt. "
                                  f"4) Run `python3 ~/.claude/autoapply/bin/clear_halt.py`; it removes HALT only when every condition holds. Then continue the pass with the next listing."))
        json.dump(halt, open(os.path.join(BASE, 'HALT'), 'w'), indent=1)
        halted = True
    summary['halted'] = halted

    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    out = dict(summary=summary, transcripts=results, resumes=file_results)
    json.dump(out, open(os.path.join(REPORTS, f'audit_{stamp}.json'), 'w'), indent=1, default=str)

    worst = sorted([r for r in file_results if not r['ok']], key=lambda r: (r['info'].get('fill', 1), r['info'].get('specifics_present', 1)))[:6]
    h = [f"<title>Autoapply audit {stamp}</title>"
         "<style>body{font-family:-apple-system,Helvetica,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px}"
         ".CRITICAL{color:#b00020;font-weight:600}.HIGH{color:#c65a00}.MEDIUM{color:#7a6400}.LOW{color:#555}"
         "img{border:1px solid #ccc;max-width:100%}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}"
         "td,th{padding:4px 8px;text-align:left;border-bottom:1px solid #eee;font-size:13px;vertical-align:top}</style>",
         f"<h1>Autoapply audit {stamp}</h1><p>Window: {fmt(since)} to {fmt(time.time())}. Transcripts: {', '.join(summary['transcripts']) or 'none active'}.</p>",
         f"<p><b>{summary['applications']} applications</b>, <b>{summary['findings']} findings</b> ({summary['critical']} critical), "
         f"{summary['resumes_written']} resumes written, {summary['resumes_failing']} failing validation. "
         f"{'<b class=CRITICAL>WORKER HALTED.</b>' if halted else ''}</p>",
         "<h2>Findings</h2><table><tr><th>Sev</th><th>When</th><th>What</th><th>Detail</th></tr>"]
    for f in sorted(all_f, key=lambda f: SEV_ORDER.index(f['sev'])):
        h.append(f"<tr><td class={f['sev']}>{f['sev']}</td><td>{fmt(f['t'])}</td><td>{f['what']}</td><td>{f['detail'][:300]}</td></tr>")
    h.append("</table><h2>Applications</h2><table><tr><th>When</th><th>Company</th><th>Evidence</th><th>Resume</th></tr>")
    for r in results:
        for a in r['applications']:
            ch = a.get('checks', {})
            v = a.get('validation')
            present = ', '.join(k for k, x in ch.items() if x) or '-'
            missing = ', '.join(k for k, x in ch.items() if not x) or 'none'
            res = 'PASS' if v and v['ok'] else (('<span class=CRITICAL>FAIL</span> ' + '; '.join(v['fails'])[:200]) if v else 'no file found')
            h.append(f"<tr><td>{fmt(a['t'])}</td><td>{a['company']}</td><td>{present}<br><span class=HIGH>missing: {missing}</span></td><td>{res}</td></tr>")
    h.append("</table>")
    if worst:
        h.append("<h2>Lowest-scoring resumes written in this window</h2><div class=grid>")
        for r in worst:
            png = render_png(os.path.join(RESUME_DIR, r['file']))
            h.append(f"<div><b>{r['file']}</b><br><small>{'; '.join(r['fails'])[:250]}</small><br><img src='data:image/png;base64,{png}'></div>")
        h.append("</div>")
    html = '\n'.join(h)
    open(os.path.join(REPORTS, f'audit_{stamp}.html'), 'w').write(html)
    open(os.path.join(REPORTS, 'latest.html'), 'w').write(html)
    with open(os.path.join(REPORTS, 'audit_log.txt'), 'a') as f:
        f.write(f"{fmt(time.time())} apps={summary['applications']} findings={summary['findings']} critical={summary['critical']} "
                f"resumes={summary['resumes_failing']}/{summary['resumes_written']} failing halted={halted}\n")
    json.dump(dict(last_run=time.time(), pending=pending), open(STATE, 'w'), indent=1)
    print(json.dumps(summary, default=str))
    for f in sorted(all_f, key=lambda f: SEV_ORDER.index(f['sev']))[:30]:
        print(f"  [{f['sev']}] {fmt(f['t'])} {f['what']} :: {f['detail'][:140]}")
    print(f"SUMMARY: {summary['applications']} apps, {summary['findings']} findings ({summary['critical']} critical), "
          f"{summary['resumes_failing']}/{summary['resumes_written']} resumes failing" + (", WORKER HALTED" if halted else ""))


if __name__ == '__main__':
    main()
