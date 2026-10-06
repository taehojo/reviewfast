"""Command line: reviewfast serve | new | import | score | rank | status | export."""
import argparse
import json
import os
import sys
import threading
from pathlib import Path

from . import __version__, records_io, report, scoring
from .jev_client import GATEWAY_URL, JevClient, estimate_cost
from .project import Project

DEFAULT_DIR = os.environ.get('REVIEWFAST_PROJECTS', str(Path.home() / 'reviewfast-projects'))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='reviewfast', description='Title and abstract screening ranked by Jev, stopped by a statistical criterion.')
    ap.add_argument('--version', action='version', version=__version__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('serve', help='start the local web app'); s.add_argument('--dir', default=DEFAULT_DIR); s.add_argument('--port', type=int, default=8765)
    s.add_argument('--no-browser', action='store_true')
    n = sub.add_parser('new', help='create a project file'); n.add_argument('project'); n.add_argument('--title', required=True)
    n.add_argument('--question', required=True); n.add_argument('--criteria-file', required=True)
    i = sub.add_parser('import', help='import RIS, CSV, .nbib or PubMed XML'); i.add_argument('project'); i.add_argument('files', nargs='+')
    c = sub.add_parser('score', help='score unscored records (key from AI_GATEWAY_API_KEY)'); c.add_argument('project')
    c.add_argument('--yes', action='store_true', help='confirm the data notice without prompting')
    r = sub.add_parser('rank', help='freeze the ranking'); r.add_argument('project')
    t = sub.add_parser('status', help='counts and stopping status'); t.add_argument('project')
    e = sub.add_parser('export', help='write decisions.csv, included.ris, methods.txt and archive.zip'); e.add_argument('project'); e.add_argument('--out', default='.')
    a = ap.parse_args(argv)

    if a.cmd == 'serve':
        from .server import serve
        return serve(a.dir, port=a.port, open_browser=not a.no_browser)
    if a.cmd == 'new':
        Project.create(a.project, a.title, a.question, Path(a.criteria_file).read_text()).close(); print('created', a.project); return
    p = Project(a.project)
    if not p.meta('created'):
        sys.exit(f'{a.project} is not a reviewfast project.')
    if a.cmd == 'import':
        for f in a.files:
            fmt, recs = records_io.parse_file(f, Path(f).read_bytes())
            print(f, json.dumps({'format': fmt, **p.import_records(recs, f'{fmt}:{Path(f).name}')}))
    elif a.cmd == 'score':
        key = os.environ.get('AI_GATEWAY_API_KEY')
        if not key:
            sys.exit('Set AI_GATEWAY_API_KEY to your Vercel AI Gateway API key.')
        n_todo = len(p.to_score())
        print(f'{n_todo} records to score; estimated cost ${estimate_cost(n_todo):.3f}.')
        print('The criteria and the titles and abstracts are sent to TypeSafe AI through Vercel (United States); the provider does not offer '
              'zero data retention. Send only published or cleared records.')
        if not a.yes and input('Continue? [y/N] ').strip().lower() != 'y':
            return
        client = JevClient(key, endpoint=GATEWAY_URL); cancel = threading.Event()
        try:
            out = scoring.run(p, client, cancel=cancel, progress=lambda d, t, s, th: print(f'\r{d}/{t}  ${s:.4f}  waits {th}', end='', flush=True))
        except KeyboardInterrupt:
            cancel.set(); print('\ncancelled'); return
        finally:
            client.close()
        print('\n' + json.dumps(out))
    elif a.cmd == 'rank':
        p.freeze_ranking(); print(json.dumps(p.counts()))
    elif a.cmd == 'status':
        print(json.dumps({'counts': p.counts(), 'scoring': p.scoring_summary(), 'stop': p.stop_status()}, indent=1))
    elif a.cmd == 'export':
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        (out / 'decisions.csv').write_text(records_io.to_csv(report.decisions_rows(p), report.DECISION_COLUMNS))
        (out / 'included.ris').write_text(report.included_ris(p))
        (out / 'methods.txt').write_text(report.methods_text(p))
        (out / 'archive.zip').write_bytes(report.archive(p))
        print('written to', out)


if __name__ == '__main__':
    main()
