"""Browser test of the ReviewFast web page (developer check, needs Playwright and Chromium; not part of CI).

Serves site/ locally, then: runs the demo until the stopping criterion is met; creates a project, imports the Theobald_2021
records as RIS, scores them through the real page code with the gateway answered by the paper's stored probabilities
(request interception, so nothing leaves the machine), freezes the ranking, screens to the stop, screens the manual queue and
downloads the report files. Fails on any page error, console error or CSP violation. Usage:
    LD_LIBRARY_PATH=<libs> python site/test/browser_test.py [--shots DIR] [--url URL]
"""
import argparse
import functools
import http.server
import json
import os
import re
import sys
import threading

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__)); SITE = os.path.join(HERE, '..'); ROOT = os.path.join(SITE, '..')
sys.path.insert(0, ROOT)
from reviewfast import records_io  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument('--shots'); ap.add_argument('--url'); a = ap.parse_args()
th = json.load(open(os.path.join(ROOT, 'tests', 'fixtures', 'theobald_2021.json')))
prob = {r['title']: r['paper_p'] for r in th['records']}; label = {r['title']: r['label'] for r in th['records']}
head, crit = th['criteria_block'].split('\nEligibility criteria reported by the review authors:\n', 1)
title, question = head.split('\nResearch question: ', 1); title = title.removeprefix('Systematic review title: ')
ris_path = os.path.join(HERE, '_theobald.ris')
open(ris_path, 'w').write(records_io.to_ris([{'title': r['title'], 'abstract': r['abstract']} for r in th['records']]))

srv = None
if not a.url:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=SITE)
    handler.log_message = lambda *x: None
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
url = a.url or f'http://127.0.0.1:{srv.server_address[1]}/'
problems = []; requests_seen = []


def gateway(route, request):
    body = json.loads(request.post_data); qs = list(body['questions']); answers = {}
    for i, q in enumerate(qs):
        pat = r'\[Record R%d\]\nTitle: (.*)\n' % (i + 1) if len(qs) > 1 else r'\n\nTitle: (.*)\n'
        answers[q] = {'noul': prob[re.search(pat, body['state']).group(1)]}
    requests_seen.append(len(qs))
    route.fulfill(status=200, content_type='application/json', headers={'access-control-allow-origin': '*'},
                  body=json.dumps({'model': 'typesafe-ai/jev', 'answers': answers, 'usage': {'input_tokens': 455 * len(qs)},
                                   'provider_metadata': {'gateway': {'marketCost': 0.0000191 * len(qs)}}}))


def shot(page, name):
    if a.shots:
        os.makedirs(a.shots, exist_ok=True); page.screenshot(path=os.path.join(a.shots, name + '.png'), full_page=True)


with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ('light', 'dark'):
        ctx = b.new_context(viewport={'width': 1280, 'height': 900}, color_scheme=scheme, accept_downloads=True)
        page = ctx.new_page()
        page.on('pageerror', lambda e: problems.append(f'pageerror: {e}'))
        page.on('console', lambda m: problems.append(f'console {m.type}: {m.text}') if m.type in ('error', 'warning') else None)
        page.route('https://ai-gateway.vercel.sh/**', gateway)
        page.goto(url); page.wait_for_selector('#home h1'); shot(page, f'home-{scheme}')
        if scheme == 'dark':
            ctx.close(); continue
        # demo
        page.click('nav a[href="#/demo"]'); page.wait_for_selector('.record h3', timeout=30000); shot(page, 'demo-start')
        page.click('button[data-auto="all"]'); page.wait_for_selector('text=of ranked records read', timeout=60000); shot(page, 'demo-done')
        demo_text = page.inner_text('#view'); m = re.search(r'([\d.]+)%\s*of ranked records read \((\d+) of (\d+)\)', demo_text)
        f = re.search(r'(\d+) / (\d+)\s*included studies found', demo_text)
        print('demo:', m.group(0) if m else '?', '|', f.group(0) if f else '?')
        # own project
        page.click('nav a[href="#/projects"]'); page.fill('#np-t', title); page.fill('#np-q', question); page.fill('#np-c', crit)
        page.click('#newp button[type=submit]'); page.wait_for_selector('#files')
        page.set_input_files('#files', ris_path); page.wait_for_selector('#imp-log .notice'); shot(page, 'import')
        assert '70 added' in page.inner_text('#imp-log'), page.inner_text('#imp-log')
        page.click('text=Next: score the records'); page.fill('#key', 'test-key-not-sent-anywhere'); page.check('#agree')
        page.click('#go'); page.wait_for_selector('#freeze', timeout=120000); shot(page, 'scored')
        page.click('#freeze'); page.wait_for_selector('.record h3')
        n = 0
        while True:
            if page.query_selector('#stopbtn'):
                break
            t = page.inner_text('.record h3'); page.keyboard.press('i' if label[t] else 'e'); n += 1
            page.wait_for_function('n => document.querySelectorAll(".record h3").length === 0 || true', arg=n)
        shot(page, 'criterion-met'); page.click('#stopbtn'); page.wait_for_selector('text=Ranked list closed')
        page.click('#view a.btn:has-text("Screen the manual queue")'); page.wait_for_selector('.record h3')
        while page.query_selector('.record h3'):
            t = page.inner_text('.record h3'); page.keyboard.press('i' if label.get(t) else 'e')
        page.wait_for_selector('text=Manual queue done'); page.click('#view a.btn:has-text("Report")'); page.wait_for_selector('#mt'); shot(page, 'report')
        methods = page.inner_text('#mt'); assert 'We stopped screening' in methods and '—' not in methods
        with page.expect_download() as d:
            page.click('[data-dl=csv]')
        rows = open(d.value.path()).read().strip().split('\n'); assert len(rows) == 71, len(rows)
        print(f'own project: {n} ranked decisions before the criterion was met; {sum(requests_seen)} records sent in {len(requests_seen)} requests '
              f'(sizes {sorted(set(requests_seen))}); decisions.csv rows {len(rows) - 1}')
        # reload keeps the project (IndexedDB)
        page.reload(); page.click('nav a[href="#/projects"]'); page.wait_for_selector('.list-row'); assert 'stopped' in page.inner_text('#plist')
        # Python & MCP page, mobile layout
        page.click('nav a[href="#/use"]'); page.wait_for_selector('text=MCP server')
        ctx.close()
    mob = b.new_context(viewport={'width': 390, 'height': 844}); page = mob.new_page()
    page.on('pageerror', lambda e: problems.append(f'pageerror: {e}'))
    page.goto(url); page.wait_for_selector('#home h1'); shot(page, 'home-mobile')
    sw = page.evaluate('document.documentElement.scrollWidth'); assert sw <= 390, f'horizontal scroll on mobile: {sw}px'
    page.goto(url + '#/demo'); page.wait_for_selector('.record h3', timeout=30000); shot(page, 'demo-mobile')
    sw = page.evaluate('document.documentElement.scrollWidth'); assert sw <= 390, f'horizontal scroll on mobile demo: {sw}px'
    b.close()
os.remove(ris_path)
if srv:
    srv.shutdown()
if problems:
    print('PROBLEMS:'); [print(' ', x) for x in problems]; sys.exit(1)
print('browser test passed')
