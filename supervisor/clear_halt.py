#!/usr/bin/env python3
"""Clears the HALT flag only when every condition it lists is met. Run by the worker after remediating.
Exit 0 = cleared, 1 = conditions still unmet (printed), 3 = no HALT present.
"""
import sys, os, json, re, time, subprocess
BASE = os.path.expanduser('~/.claude/autoapply')
HALT = os.path.join(BASE, 'HALT')
LOG = os.path.join(BASE, 'cron_pass_log.txt')
VALIDATOR = os.path.join(BASE, 'bin', 'validate_resume.py')
sys.path.insert(0, os.path.join(BASE, 'bin'))
import gmail_confirm as G

def main():
    if not os.path.exists(HALT):
        print('no HALT present'); sys.exit(3)
    try: h = json.load(open(HALT))
    except Exception:
        h = dict(id='legacy', must_pass=[], must_flag=[open(HALT).read()[:200]])
    unmet = []
    try: ledger = [json.loads(l) for l in open(os.path.join(BASE, 'ledger.jsonl')) if l.strip()]
    except Exception: ledger = []
    try: guard = [json.loads(l) for l in open(os.path.join(BASE, 'guard_log.jsonl')) if l.strip()]
    except Exception: guard = []
    for ent in h.get('must_pass', []):
        if isinstance(ent, str): ent = dict(resume_path=ent, resubmit=False, company='')
        p = ent['resume_path']
        if not os.path.exists(p):
            unmet.append(f'resume missing: {p}'); continue
        r = subprocess.run([sys.executable, VALIDATOR, '--json', p], capture_output=True, text=True)
        try: res = json.loads(r.stdout)
        except Exception: unmet.append(f'validator error on {p}'); continue
        if not res['ok']:
            unmet.append(f'{os.path.basename(p)} still fails: ' + ' | '.join(res['fails'])[:300]); continue
        if ent.get('resubmit'):
            # evidence chain written by the hooks, not by the worker: Go-to-company-site click -> validated upload of THIS
            # file -> allowed submit of THIS file, all after the HALT; plus the worker's own 'submitted' ledger line.
            t0 = h.get('ts', 0)
            g = [x for x in guard if x.get('ts', 0) > t0]
            ap = os.path.abspath(p)
            t_site = next((x['ts'] for x in g if x.get('kind') == 'GO_TO_COMPANY_SITE'), None)
            t_up = next((x['ts'] for x in g if x.get('kind') == 'ALLOW_UPLOAD' and os.path.abspath(x.get('path', '')) == ap and (t_site is None or x['ts'] > t_site)), None)
            t_sub = next((x['ts'] for x in g if x.get('kind') == 'ALLOW_SUBMIT' and t_up and x['ts'] > t_up and ap in [os.path.abspath(q or '') for q in x.get('paths', [])]), None)
            self_rep = any(e.get('event') == 'submitted' and os.path.abspath(e.get('resume_path', '')) == ap and e.get('ts', 0) > t0 for e in ledger)
            missing = []
            if t_site is None: missing.append("no hook-observed click on LinkedIn's 'Go to company site' link")
            if t_up is None: missing.append('no hook-validated upload of the rebuilt resume after that click')
            if t_sub is None: missing.append('no hook-allowed submit after that upload')
            if not self_rep: missing.append("no ledger line {event: submitted, resubmission: true} for this file")
            if not missing:
                mail = G.confirmed(ent.get('company') or os.path.basename(p).replace('Resume - ', ''), t0, G.confirmations(force=True))
                if not mail:
                    missing.append("no confirmation email in Gmail yet for this resubmission (emails usually arrive within minutes; re-run clear_halt.py in a few minutes; if none arrives after 30 min, the submission did not go through: check the ATS tab's success screen and submit again)")
                else:
                    with open(LOG, 'a') as f: f.write(f"GMAIL CONFIRMED {ent.get('company')}: {mail.get('subject')} ({mail.get('date')})\n")
            if missing:
                unmet.append(f"{ent.get('company') or os.path.basename(p)}: rebuilt resume passes, but resubmission not evidenced: " + '; '.join(missing)
                             + ". Open the LinkedIn listing" + (f" ({ent['linkedin_url']})" if ent.get('linkedin_url') else '')
                             + ", click the job title, click 'Go to company site', upload the rebuilt resume via file_upload, submit, then record it.")
    if h.get('must_flag'):
        try: log = open(LOG, errors='ignore').read()
        except FileNotFoundError: log = ''
        m = re.findall(r'^REMEDIATED HALT ' + re.escape(h['id']) + r':\s*(.{40,})$', log, re.M)
        if not m:
            unmet.append(f'no log line "REMEDIATED HALT {h["id"]}: <what happened, which listings, what you did>" (at least 40 chars) in cron_pass_log.txt for: ' + ' || '.join(h['must_flag'])[:400])
    if unmet:
        print('HALT NOT CLEARED. Remaining:'); [print('  - ' + u) for u in unmet]; sys.exit(1)
    os.remove(HALT)
    try: prior = [l.rstrip() for l in open(LOG, errors='ignore') if re.search(r'APPLIED|SKIP|LOGGED|BLOCKED|PAGE', l)][-3:]
    except Exception: prior = []
    with open(LOG, 'a') as f: f.write(f"HALT {h['id']} CLEARED {time.strftime('%Y-%m-%d %H:%M')} after remediation; pass continues\n")
    print(f"HALT {h['id']} cleared. The pass is still in progress; you cannot end the turn until it is complete.")
    print("Continue with the NEXT unevaluated listing after these last log entries (do not restart from page 1):")
    for l in prior: print('   ' + l[:160])

if __name__ == '__main__':
    main()
