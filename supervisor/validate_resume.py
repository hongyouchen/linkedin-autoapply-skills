#!/usr/bin/env python3
"""Mechanical resume validator. Compares a tailored resume PDF against resume core.pdf.

Usage: validate_resume.py <resume.pdf> [--json] [--sweep DIR]
Exit 0 = PASS, 1 = FAIL, 3 = error. Prints human-readable reasons (or JSON with --json).

Rules enforced (from hard_rule_no_resume_shortcuts.md):
  1. exactly one page
  2. content reaches the bottom of the page like core does (fill >= FILL_MIN)
  3. bullet count per role: Gusto and Wingman must equal core; total bullets >= core total - 1
  4. core's key specifics (numeric facts) present: >= SPECIFICS_MIN of them
  5. every work-experience bullet traces to a core bullet (difflib ratio >= TRACE_MIN)
  6. not a template duplicate: bullets+summary identical to another company's file
  7. text length >= LEN_MIN * core length
"""
import sys, os, re, json, glob, difflib, hashlib, time
import fitz

MIRROR = os.path.expanduser('~/.claude/autoapply/mirror')

def _readable(p):
    try:
        with open(p, 'rb') as f: f.read(16)
        return True
    except Exception:
        return False

def resolve(p):
    """Desktop/Downloads are TCC-protected: a launchd process cannot read them. The guard hook (which runs inside
    Claude Code, with access) mirrors them under ~/.claude/autoapply/mirror; fall back to the twin path there."""
    if _readable(p): return p
    real_core = os.path.expanduser('~/Desktop/resume core.pdf'); real_dir = os.path.expanduser('~/Downloads/Claude Resumes')
    if os.path.abspath(p) == os.path.abspath(real_core): return os.path.join(MIRROR, 'resume core.pdf')
    if os.path.abspath(p).startswith(os.path.abspath(real_dir)): return os.path.join(MIRROR, 'resumes', os.path.basename(p))
    return p

CORE = resolve(os.path.expanduser('~/Desktop/resume core.pdf'))
_RD = os.path.expanduser('~/Downloads/Claude Resumes')
def _dir_readable(d):
    try: return os.path.isdir(d) and any(True for _ in os.scandir(d))
    except Exception: return False

RESUME_DIR = _RD if _dir_readable(_RD) else os.path.join(MIRROR, 'resumes')
CACHE = os.path.expanduser('~/.claude/autoapply/resume_index.json')

FILL_MIN = 0.87       # core is 0.914; healthy tailored files 0.89-0.92; thinned 0.64-0.79
LEN_MIN = 0.80        # fraction of core text length
SPECIFICS_MIN = 0.90  # fraction of core numeric specifics that must appear
TRACE_MIN = 0.60      # min difflib ratio of a tailored bullet to its closest core bullet
MIN_CHANGED = 3       # at least this many work bullets must carry a JD-specific insertion
# Standing rule (Andy, 2026-09-12): the summary is never tailored. Exact text, body font size, one line.
STANDARD_SUMMARY = "PM with 3+ YOE in AI & Fintech who champions design, ships alongside eng, and leads xfn teams to decisive execution."
SUMMARY_RULE_SINCE = 1789250100.0  # resumes rendered before this keep their old summaries
SUMMARY_SIZE_TOL = 0.3

ROLE_KEYS = [  # (key, matcher on header line lowercased)
    ('gusto', lambda h: 'gusto' in h),
    ('wingman', lambda h: 'wingman' in h),
    ('olive', lambda h: 'olive' in h),
    ('castleton_pm', lambda h: 'castleton' in h and 'intern' not in h),
    ('castleton_intern', lambda h: 'castleton' in h and 'intern' in h),
    ('goldman', lambda h: 'goldman' in h),
    ('scottie', lambda h: 'scottie' in h),
]
WORK_ROLES = ['gusto', 'wingman', 'olive', 'castleton_pm', 'castleton_intern']
TOP_ROLES = ['gusto', 'wingman']

def lines_of(page):
    out = []
    for b in page.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            txt = ''.join(s['text'] for s in l['spans']).replace('​', '').strip()
            if not txt:
                continue
            out.append(dict(x0=l['bbox'][0], y0=l['bbox'][1], y1=l['bbox'][3], size=l['spans'][0]['size'], text=txt, glyph=txt.startswith('●')))
    out.sort(key=lambda l: (round(l['y0']), l['x0']))
    return out

def parse(pdf):
    doc = fitz.open(pdf)
    page = doc[0]
    ls = lines_of(page)
    xs = sorted(set(round(l['x0']) for l in ls))
    # header x = most common small x among lines that look like role headers; bullet x = next indent level
    body = [l for l in ls if l['size'] < 14 and not l['glyph']]
    x_counts = {}
    for l in body:
        x_counts[round(l['x0'])] = x_counts.get(round(l['x0']), 0) + 1
    common = sorted(x_counts, key=lambda x: -x_counts[x])[:2]
    header_x, bullet_x = min(common), max(common)
    roles = {}  # key -> list of bullet strings
    current = None
    summary = []
    summary_lines = []   # (text, font size)
    bullet_sizes = []
    prev_ended = True
    for l in body:
        rx = round(l['x0'])
        low = l['text'].lower()
        if abs(rx - header_x) <= 2 and l['size'] <= 12.5:
            key = next((k for k, m in ROLE_KEYS if m(low)), None)
            if key:
                current = key; roles.setdefault(key, []); prev_ended = True; continue
            if l['text'].lower().startswith(('education', 'leadership', 'skills', 'work experience', 'carnegie')):
                current = None; prev_ended = True; continue
            if current is None and not roles and not low.startswith(('andy', 'email', 'hongyou')) and '|' not in l['text']:
                summary.append(l['text']); summary_lines.append((l['text'], l['size']))
            continue
        if abs(rx - bullet_x) <= 3 and current:
            bullet_sizes.append(round(l['size'], 1))
            if prev_ended:
                roles[current].append(l['text'])
            else:
                roles[current][-1] += ' ' + l['text']
            prev_ended = bool(re.search(r'[.!?]["\')]?\s*$', l['text']))
        # core.pdf puts glyph bullets at a third x; ignore
    full = page.get_text()
    bottom = max((b[3] for b in page.get_text('blocks')), default=0) / page.rect.height
    body_size = max(set(bullet_sizes), key=bullet_sizes.count) if bullet_sizes else 0
    return dict(pages=len(doc), lines=ls, roles=roles, summary=' '.join(summary), summary_lines=summary_lines, body_size=body_size,
                text=full, length=len(full), fill=bottom)

def core_bullets_from_glyphs(pdf):
    """core.pdf has explicit ● glyphs; assign each text line to the nearest glyph above it."""
    doc = fitz.open(pdf); page = doc[0]
    ls = lines_of(page)
    # role headers: small x, size >= 10.5, non-glyph
    headers = []
    for l in ls:
        low = l['text'].lower()
        if not l['glyph'] and round(l['x0']) <= 35 and l['size'] >= 10.5:
            key = next((k for k, m in ROLE_KEYS if m(low)), None)
            headers.append((l['y0'], key))  # key None = section boundary (Education, Skills...)
    headers.sort()
    def role_at(y):
        cur = None
        for hy, k in headers:
            if hy <= y + 2: cur = k
            else: break
        return cur
    glyphs = sorted((l['y0'], role_at(l['y0'])) for l in ls if l['glyph'])
    roles = {k: [] for k, _ in ROLE_KEYS}
    bullets = []  # (glyph_y, role, [texts])
    for gy, r in glyphs:
        bullets.append([gy, r, []])
    for l in sorted(ls, key=lambda l: l['y0']):
        if l['glyph'] or round(l['x0']) < 60: continue
        cands = [b for b in bullets if b[0] <= l['y0'] + 8]  # glyph can sit a few pt below its text line
        if not cands: continue
        cands[-1][2].append(l['text'])
    for gy, r, texts in bullets:
        if r and texts: roles[r].append(' '.join(texts))
    return roles

NUM_RE = re.compile(r'(?<![A-Za-z])(?:[~<>$]?\d[\d,.]*(?:[kKxX%]|\+|M\+|K)?)')
def specifics(text):
    toks = set()
    for m in NUM_RE.finditer(text):
        t = m.group(0).strip('.,')
        if re.fullmatch(r'\d{4}', t):  # years
            continue
        if len(t) >= 2:
            toks.add(t.lower())
    return toks

def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()

def load_core():
    p = parse(CORE)
    roles = core_bullets_from_glyphs(CORE)
    p['roles'] = roles
    p['specifics'] = specifics(' '.join(b for r in WORK_ROLES for b in roles.get(r, [])))
    return p

def index_others(exclude):
    """hash of normalized bullets+summary for every other file in RESUME_DIR (cached by mtime)."""
    try: cache = json.load(open(CACHE))
    except Exception: cache = {}
    changed = False
    out = {}
    for f in glob.glob(os.path.join(RESUME_DIR, '*.pdf')):
        if os.path.abspath(f) == os.path.abspath(exclude): continue
        m = os.path.getmtime(f)
        ent = cache.get(f)
        if not ent or ent.get('mtime') != m:
            try:
                p = parse(f)
                h = hashlib.sha1(norm(p['summary'] + ' ' + ' '.join(b for r in WORK_ROLES for b in p['roles'].get(r, []))).encode()).hexdigest()
            except Exception:
                h = None
            ent = dict(mtime=m, hash=h); cache[f] = ent; changed = True
        out[f] = ent['hash']
    if changed:
        try: json.dump(cache, open(CACHE, 'w'))
        except Exception: pass
    return out

def company_of(path):
    m = re.match(r'Resume - (.+?)(?: \(.*\))?\.pdf$', os.path.basename(path))
    return m.group(1).strip().lower() if m else ''

def validate(pdf, core=None, check_dupes=True):
    core = core or load_core()
    fails, warns, info = [], [], {}
    pdf = resolve(pdf)
    try:
        p = parse(pdf)
    except Exception as e:
        return dict(ok=False, fails=[f'cannot parse PDF: {e}'], warns=[], info={})
    info['pages'] = p['pages']; info['fill'] = round(p['fill'], 3); info['length'] = p['length']
    # fixed summary: exact sentence, one line, same font size as the body (resumes rendered after the rule took effect)
    try: mt = os.path.getmtime(pdf)
    except Exception: mt = time.time()
    if mt >= SUMMARY_RULE_SINCE:
        def _ns(x): return re.sub(r'\s+', ' ', x.replace('\u2019', "'").replace('\u2013', '-').replace('\u2014', '-').replace('&amp;', '&')).strip()
        sl = p.get('summary_lines', [])
        got = _ns(' '.join(t for t, _ in sl))
        info['summary'] = got; info['summary_lines'] = len(sl)
        if got != _ns(STANDARD_SUMMARY):
            fails.append(f'summary must be exactly "{STANDARD_SUMMARY}" (standing rule: never tailor the summary); found "{got[:140]}"')
        elif len(sl) != 1:
            fails.append(f"summary wraps onto {len(sl)} lines; it must fit on one line at body font size (tighten letter-spacing or margins slightly, never shrink the summary font)")
        if sl and p.get('body_size') and any(abs(sz - p['body_size']) > SUMMARY_SIZE_TOL for _, sz in sl):
            fails.append(f"summary font size {sl[0][1]:.1f}pt differs from body text {p['body_size']:.1f}pt; it must be the same size")
    if p['pages'] != 1: fails.append(f"page count {p['pages']} != 1")
    if p['fill'] < FILL_MIN: fails.append(f"page fill {p['fill']:.2f} < {FILL_MIN} (core {core['fill']:.2f}); bottom of page is empty")
    if p['length'] < LEN_MIN * core['length']: fails.append(f"text length {p['length']} < {LEN_MIN:.0%} of core ({core['length']})")
    # bullets
    counts = {r: len(p['roles'].get(r, [])) for r in WORK_ROLES}
    ccounts = {r: len(core['roles'].get(r, [])) for r in WORK_ROLES}
    info['bullets'] = counts; info['core_bullets'] = ccounts
    for r in TOP_ROLES:
        if counts[r] != ccounts[r]: fails.append(f"{r} has {counts[r]} bullets, core has {ccounts[r]} (top roles must match exactly)")
    if sum(counts.values()) < sum(ccounts.values()) - 1:
        fails.append(f"total work bullets {sum(counts.values())} < core total {sum(ccounts.values())} - 1")
    for r in WORK_ROLES:
        if counts[r] == 0: fails.append(f"role {r} missing entirely")
    # specifics
    have = specifics(p['text'])
    missing = sorted(t for t in core['specifics'] if t not in have)
    frac = 1 - len(missing) / max(1, len(core['specifics']))
    info['specifics_present'] = round(frac, 2); info['specifics_missing'] = missing
    if frac < SPECIFICS_MIN: fails.append(f"only {frac:.0%} of core's numeric specifics present; missing: {', '.join(missing)}")
    # traceability
    core_b = [b for r in WORK_ROLES for b in core['roles'].get(r, [])]
    untraced = []
    for r in WORK_ROLES:
        for b in p['roles'].get(r, []):
            best = max((difflib.SequenceMatcher(None, norm(b), norm(cb)).ratio() for cb in core_b), default=0)
            if best < TRACE_MIN: untraced.append((r, round(best, 2), b[:90]))
    info['untraced_bullets'] = untraced
    if untraced: fails.append("bullets rewritten from scratch (no close match in core): " + '; '.join(f"[{r} {s}] {t}" for r, s, t in untraced))
    # tailoring depth: the keyword-gap technique touches one bullet per missing keyword, so a resume whose
    # bullets are all verbatim core with only the summary swapped is a template, not a tailored resume.
    core_norm = {norm(b) for b in core_b}
    changed = sum(1 for r in WORK_ROLES for b in p['roles'].get(r, []) if norm(b) not in core_norm)
    info['bullets_changed_vs_core'] = changed
    if changed < MIN_CHANGED: fails.append(f"only {changed} work bullet(s) differ from core (need >= {MIN_CHANGED}); this is core.pdf with a swapped summary, not a per-JD tailoring. Insert this JD's missing keywords into bullets (one per bullet).")
    # dupes
    if check_dupes:
        h = hashlib.sha1(norm(p['summary'] + ' ' + ' '.join(b for r in WORK_ROLES for b in p['roles'].get(r, []))).encode()).hexdigest()
        me = company_of(pdf)
        dupes = [os.path.basename(f) for f, oh in index_others(pdf).items() if oh == h and company_of(f) != me]
        info['duplicates'] = dupes
        if dupes: fails.append("template duplicate: identical summary+bullets to " + ', '.join(dupes[:5]))
    return dict(ok=not fails, fails=fails, warns=warns, info=info)

def main():
    args = sys.argv[1:]
    as_json = '--json' in args
    if '--sweep' in args:
        d = args[args.index('--sweep') + 1]
        core = load_core()
        rows = []
        for f in sorted(glob.glob(os.path.join(d, '*.pdf'))):
            r = validate(f, core, check_dupes=False); r['file'] = os.path.basename(f); r['mtime'] = os.path.getmtime(f); rows.append(r)
        print(json.dumps(rows))
        return
    pdf = [a for a in args if not a.startswith('--')]
    if not pdf: print(__doc__); sys.exit(3)
    r = validate(pdf[0])
    if as_json: print(json.dumps(r, indent=1))
    else:
        print(('PASS' if r['ok'] else 'FAIL') + f": {os.path.basename(pdf[0])}")
        for f in r['fails']: print('  - ' + f)
        print('  info: ' + json.dumps({k: v for k, v in r['info'].items() if k not in ('untraced_bullets',)}))
    sys.exit(0 if r['ok'] else 1)

if __name__ == '__main__':
    main()
