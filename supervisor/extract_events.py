#!/usr/bin/env python3
"""Deterministic transcript -> structured event list.

Never emits tool RESULT content (page text, JD text), only tool-call structure, Andy's own
messages, and the assistant's prose, so the auditor is not exposed to web content.
"""
import sys, os, json, re, datetime


def ts(o):
    t = o.get('timestamp')
    try:
        return datetime.datetime.fromisoformat(t.replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


def sub_events(name, inp, t, out):
    name = name.replace('mcp__claude-in-chrome__', '')
    e = dict(t=t, tool=name)
    if name == 'browser_batch':
        for a in inp.get('actions', []) or []:
            sub_events(a.get('name', ''), a.get('input', {}) or {}, t, out)
        return
    if name == 'navigate':
        e['url'] = inp.get('url', '')
    elif name == 'find':
        e['query'] = inp.get('query', '')
    elif name == 'file_upload':
        e['paths'] = inp.get('paths', [])
    elif name == 'computer':
        e['action'] = inp.get('action')
        if inp.get('text'):
            e['text'] = str(inp.get('text'))[:120]
    elif name == 'form_input':
        e['value'] = str(inp.get('value'))[:80]
    elif name == 'javascript_tool':
        e['js'] = str(inp.get('text', ''))[:200]
    elif name == 'Read':
        e['path'] = inp.get('file_path', '')
    elif name in ('Write', 'Edit'):
        e['path'] = inp.get('file_path', '')
        c = str(inp.get('content', '') or inp.get('new_string', ''))
        e['len'] = len(c)
        e['resume_html'] = c.lower().count('andy chen')
        e['employers'] = sum(1 for k in ('gusto', 'wingman', 'olive capital', 'castleton') if k in c.lower())
    elif name == 'Bash':
        c = str(inp.get('command', ''))
        e['cmd'] = c[:300]
        if 'cron_pass_log' in c or 'ledger.jsonl' in c:
            e['log_text'] = c[:4000]
        e['render'] = 'print-to-pdf' in c
    elif name == 'AskUserQuestion':
        e['q'] = json.dumps(inp)[:300]
    else:
        e['input'] = json.dumps(inp)[:200]
    out.append(e)


def extract(path, since=0):
    out = []
    with open(path, errors='ignore') as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            typ = o.get('type')
            if typ not in ('user', 'assistant'):
                continue
            t = ts(o)
            if t is None or t < since:
                continue
            c = o.get('message', {}).get('content')
            if typ == 'assistant' and isinstance(c, list):
                for x in c:
                    if x.get('type') == 'tool_use':
                        sub_events(x['name'], x.get('input', {}) or {}, t, out)
                    elif x.get('type') == 'text' and x.get('text', '').strip():
                        out.append(dict(t=t, tool='ASSISTANT_TEXT', text=x['text'][:600]))
            elif typ == 'user':
                if isinstance(c, str):
                    out.append(dict(t=t, tool='USER_TEXT', text=c[:600]))
                elif isinstance(c, list):
                    for x in c:
                        if x.get('type') == 'text' and x.get('text', '').strip():
                            out.append(dict(t=t, tool='USER_TEXT', text=x['text'][:600]))
                        elif x.get('type') == 'tool_result':
                            cc = x.get('content')
                            n = len(json.dumps(cc)) if cc is not None else 0
                            out.append(dict(t=t, tool='RESULT', len=n, err=bool(x.get('is_error'))))
    return out


if __name__ == '__main__':
    p = sys.argv[1]
    since = float(sys.argv[2]) if len(sys.argv) > 2 else 0
    for e in extract(p, since):
        print(json.dumps(e))
