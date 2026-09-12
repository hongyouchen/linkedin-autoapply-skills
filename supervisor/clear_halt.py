#!/usr/bin/env python3
"""Clears the HALT flag only when every condition it lists is met. Run by the worker after remediating.
Exit 0 = cleared, 1 = conditions still unmet (printed), 3 = no HALT present.
"""
import sys, os, json, re, time, subprocess
BASE = os.path.expanduser('~/.claude/autoapply')
HALT = os.path.join(BASE, 'HALT')
LOG = os.path.join(BASE, 'cron_pass_log.txt')
VALIDATOR = os.path.join(BASE, 'bin', 'validate_resume.py')

def main():
    if not os.path.exists(HALT):
        print('no HALT present'); sys.exit(3)
    try: h = json.load(open(HALT))
    except Exception:
        h = dict(id='legacy', must_pass=[], must_flag=[open(HALT).read()[:200]])
    unmet = []
    try: ledger = [json.loads(l) for l in open(os.path.join(BASE, 'ledger.jsonl')) if l.strip()]
    except Exception: ledger = []
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
            ok = any(e.get('event') == 'submitted' and os.path.abspath(e.get('resume_path', '')) == os.path.abspath(p)
                     and e.get('ts', 0) > h.get('ts', 0) for e in ledger)
            if not ok:
                unmet.append(f"{ent.get('company') or os.path.basename(p)}: rebuilt resume passes, but no resubmission recorded in ledger.jsonl "
                             f"(need event 'submitted' for {os.path.basename(p)} dated after the HALT). Open the LinkedIn listing"
                             + (f" ({ent['linkedin_url']})" if ent.get('linkedin_url') else '') +
                             ", click the job title, click 'Go to company site', upload the rebuilt resume, submit, then record it.")
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
