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
RESUME_DIR = os.path.expanduser('~/Downloads/Claude Resumes')
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

    # applications = APPLIED lines written to the pass log
    for e in ev:
        if e.get('log_text'):
            for m in re.finditer(r'APPLIED\s+([^\n(/]+?)(?:\s*\(|\s+via|\s+-|\n|$)', e['log_text']):
                line_end = e['log_text'].find('\n', m.end()); line = e['log_text'][m.start():line_end if line_end > 0 else None]
                jid = re.search(r'\b(\d{9,11})\b', line)
                if re.fullmatch(r'[\d\s]+', m.group(1).strip()) or 'Tally' in e['log_text'][max(0, m.start()-40):m.start()]:
                    continue  # "APPLIED 3 (...)" tally lines are counts, not companies
                apps.append(dict(t=e['t'], company=m.group(1).strip()[:60], linkedin_url=(f'https://www.linkedin.com/jobs/view/{jid.group(1)}/' if jid else None)))

    # pass-level checks
    for e in ev:
        if e.get('log_text') and re.search(r'PASS PAUSED budget:', e['log_text']):
            F('MEDIUM', e['t'], 'Worker paused a pass citing token budget (allowed escape; verify it was genuine)', e['log_text'][e['log_text'].find('PASS PAUSED'):][:160])
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
        comp = a['company'].lower().split()[0] if a['company'] else ''
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
        # Gmail: every real submission produces a confirmation email (Andy, 2026-09-11)
        age_min = (time.time() - a['t']) / 60
        mail = G.confirmed(a['company'], a['t'], gmail_threads) or G.confirmed(a['company'], a['t'], G.targeted([a['company']]))
        checks['gmail_confirmation'] = bool(mail)
        a['gmail'] = dict(subject=mail.get('subject'), from_=mail.get('from'), date=mail.get('date')) if mail else None
        if not mail and age_min > 90:
            F('CRITICAL', a['t'], f"{a['company']}: no confirmation email in Gmail {int(age_min)} min after APPLIED", 'either the application never actually went through, or the worker mis-reported it')
        elif not mail and age_min > 30:
            F('HIGH', a['t'], f"{a['company']}: no confirmation email yet ({int(age_min)} min)", '')
        files = [f for f in glob.glob(os.path.join(RESUME_DIR, '*.pdf')) if comp and comp in os.path.basename(f).lower()]
        val = None
        if files:
            f = max(files, key=os.path.getmtime)
            val = V.validate(f, core, check_dupes=True)
            val['file'] = os.path.basename(f)
        a['checks'] = checks
        a['validation'] = val
        missing = [k for k, v in checks.items() if not v and k != 'gmail_confirmation']
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
    results = [audit_transcript(p, since, core) for p in paths]

    new_files = [f for f in glob.glob(os.path.join(RESUME_DIR, '*.pdf')) if os.path.getmtime(f) >= since]
    file_results = []
    for f in sorted(new_files, key=os.path.getmtime):
        r = V.validate(f, core, check_dupes=True)
        r['file'] = os.path.basename(f)
        r['mtime'] = os.path.getmtime(f)
        file_results.append(r)

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
        for r in results:
            for a in r['applications']:
                if a.get('gmail') is None and (time.time() - a['t']) / 60 > 90:
                    fp = os.path.join(RESUME_DIR, a['validation']['file']) if a.get('validation') else None
                    if fp and fp not in seen:
                        seen.add(fp); must_pass.append(dict(company=a['company'], resume_path=fp, linkedin_url=a.get('linkedin_url'), resubmit=True,
                                                            reason='no Gmail confirmation: verify on the ATS; if it was never submitted, apply again via the LinkedIn listing'))
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
    json.dump(dict(last_run=time.time()), open(STATE, 'w'))
    print(json.dumps(summary, default=str))
    for f in sorted(all_f, key=lambda f: SEV_ORDER.index(f['sev']))[:30]:
        print(f"  [{f['sev']}] {fmt(f['t'])} {f['what']} :: {f['detail'][:140]}")
    print(f"SUMMARY: {summary['applications']} apps, {summary['findings']} findings ({summary['critical']} critical), "
          f"{summary['resumes_failing']}/{summary['resumes_written']} resumes failing" + (", WORKER HALTED" if halted else ""))


if __name__ == '__main__':
    main()
