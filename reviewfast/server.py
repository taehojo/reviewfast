"""Local web server. Binds to 127.0.0.1 and requires the per-launch token printed at start, so other users of a shared machine
cannot reach the projects. API keys are held in memory for the running job only and are never written to disk."""
import os
import re
import secrets
import threading
import time
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, records_io, report, scoring
from .jev_client import GATEWAY_URL, AuthError, JevClient, estimate_cost
from .project import Project, ProjectError

WEB = Path(__file__).parent / 'web'
TRIAL_URL = os.environ.get('REVIEWFAST_TRIAL_URL', '')               # the operator's proxy, if a free trial is offered
GITHUB_CLIENT_ID = os.environ.get('REVIEWFAST_GITHUB_CLIENT_ID', '')  # OAuth app used by the trial proxy to identify users


def create_app(projects_dir, token):
    projects_dir = Path(projects_dir).expanduser(); projects_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title='ReviewFast', version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    open_projects, jobs, trial = {}, {}, {'token': None, 'device': None}
    lock = threading.Lock()

    @app.middleware('http')
    async def require_token(request: Request, call_next):
        if request.url.path.startswith('/api/') and not secrets.compare_digest(request.headers.get('x-token', ''), token):
            return JSONResponse({'detail': 'Missing or wrong session token. Open the address printed by reviewfast serve.'}, 401)
        return await call_next(request)

    @app.exception_handler(ProjectError)
    async def project_error(request, exc):
        return JSONResponse({'detail': str(exc)}, 409)

    def slug(name):
        s = re.sub(r'[^A-Za-z0-9_-]+', '-', name).strip('-')[:60]
        if not s:
            raise HTTPException(400, 'Invalid project name.')
        return s

    def proj(name) -> Project:
        name = slug(name)
        with lock:
            if name not in open_projects:
                path = projects_dir / f'{name}.reviewfast'
                if not path.exists():
                    raise HTTPException(404, 'No such project.')
                open_projects[name] = Project(path)
            return open_projects[name]

    # ---------- projects
    @app.get('/api/info')
    def info():
        return {'version': __version__, 'projects_dir': str(projects_dir), 'trial_available': bool(TRIAL_URL and GITHUB_CLIENT_ID),
                'env_key': bool(os.environ.get('AI_GATEWAY_API_KEY'))}

    @app.get('/api/projects')
    def list_projects():
        out = []
        for f in sorted(projects_dir.glob('*.reviewfast')):
            p = proj(f.stem); out.append({'name': f.stem, 'title': p.meta('title'), 'created': p.meta('created'), 'counts': p.counts()})
        return out

    @app.post('/api/projects')
    def new_project(d: dict = Body(...)):
        name = slug(d.get('name', ''))
        if (projects_dir / f'{name}.reviewfast').exists():
            raise HTTPException(409, 'A project with this name exists.')
        for k in ('title', 'question', 'criteria'):
            if not (d.get(k) or '').strip():
                raise HTTPException(400, f'{k} is required.')
        Project.create(projects_dir / f'{name}.reviewfast', d['title'], d['question'], d['criteria']).close()
        return {'name': name}

    @app.get('/api/p/{name}')
    def summary(name: str):
        p = proj(name)
        return {'name': slug(name), 'meta': {k: p.meta(k) for k in ('title', 'question', 'criteria', 'created', 'app_version', 'recall_target', 'confidence', 'ranked_at', 'stopped_at')},
                'counts': p.counts(), 'scoring': p.scoring_summary(), 'ranked': p.is_ranked(), 'stop': p.stop_status(),
                'job': job_state(slug(name)), 'estimate_usd': estimate_cost(len(p.to_score())), 'to_score': len(p.to_score()),
                'audit': p.audit_summary()}

    @app.put('/api/p/{name}/criteria')
    def criteria(name: str, d: dict = Body(...)):
        proj(name).update_criteria(d['title'], d['question'], d['criteria']); return {'ok': True}

    # ---------- import and records
    @app.post('/api/p/{name}/import')
    async def import_file(name: str, file: UploadFile = File(...)):
        data = await file.read()
        try:
            fmt, recs = records_io.parse_file(file.filename or '', data)
        except (ValueError, Exception) as e:  # noqa: BLE001 - parse errors are shown to the user
            raise HTTPException(400, f'Could not read {file.filename}: {e}')
        return {'format': fmt, **proj(name).import_records(recs, f'{fmt}:{file.filename}')}

    @app.post('/api/p/{name}/import_pmids')
    def import_pmids(name: str, d: dict = Body(...)):
        pmids = re.findall(r'\d{4,9}', d.get('pmids', ''))
        if not pmids:
            raise HTTPException(400, 'No PMIDs found.')
        recs = records_io.fetch_pubmed(pmids)
        return {'format': 'pubmed', 'requested': len(pmids), **proj(name).import_records(recs, 'pubmed:efetch')}

    @app.get('/api/p/{name}/records')
    def records(name: str, queue: str = 'manual', limit: int = 500):
        p = proj(name)
        where = {'manual': 'dup_of IS NULL AND flag IS NOT NULL', 'duplicates': 'dup_of IS NOT NULL', 'all': 'dup_of IS NULL'}.get(queue)
        if not where:
            raise HTTPException(400, 'Unknown queue.')
        rows = p.records(where)
        return [{k: r[k] for k in ('rid', 'title', 'year', 'journal', 'flag', 'dup_of', 'language')} for r in rows[:limit]]

    @app.post('/api/p/{name}/records/{rid}/flag')
    def flag(name: str, rid: int, d: dict = Body(...)):
        proj(name).set_flag(rid, d.get('flag')); return {'ok': True}

    # ---------- scoring
    def job_state(name):
        j = jobs.get(name)
        if not j:
            return None
        return {k: j[k] for k in ('running', 'done', 'total', 'spent', 'throttled', 'error', 'summary', 'kind')}

    def client_for(d):
        if d.get('mode') == 'trial':
            if not (TRIAL_URL and trial['token']):
                raise HTTPException(400, 'Sign in for the free trial first.')
            return JevClient(trial['token'], endpoint=TRIAL_URL.rstrip('/') + '/api/score')
        key = (d.get('key') or '').strip() or os.environ.get('AI_GATEWAY_API_KEY', '')
        if not key:
            raise HTTPException(400, 'Enter a Vercel AI Gateway API key, or set AI_GATEWAY_API_KEY.')
        return JevClient(key, endpoint=GATEWAY_URL)

    def start_job(name, kind, d, target):
        if not d.get('consent'):
            raise HTTPException(400, 'Confirm the data notice before sending records.')
        if jobs.get(name, {}).get('running'):
            raise HTTPException(409, 'A job is already running for this project.')
        client = client_for(d)
        j = jobs[name] = {'running': True, 'done': 0, 'total': 0, 'spent': 0.0, 'throttled': 0, 'error': None, 'summary': None,
                          'kind': kind, 'cancel': threading.Event()}

        def prog(done, total, spent, thr):
            j.update(done=done, total=total, spent=spent, throttled=thr)

        def work():
            try:
                j['summary'] = target(client, j, prog)
            except AuthError as e:
                j['error'] = f'The key was rejected ({e}).'
            except Exception as e:  # noqa: BLE001 - reported in the UI
                j['error'] = f'{type(e).__name__}: {e}'
            finally:
                client.close(); j['running'] = False
        threading.Thread(target=work, daemon=True).start()
        return job_state(name)

    @app.post('/api/p/{name}/score')
    def score(name: str, d: dict = Body(...)):
        p = proj(name); name = slug(name)
        if p.is_ranked():
            raise HTTPException(409, 'The ranking is frozen.')
        return start_job(name, 'score', d, lambda c, j, prog: scoring.run(p, c, workers=2, cancel=j['cancel'], progress=prog))

    @app.post('/api/p/{name}/drift')
    def drift(name: str, d: dict = Body(...)):
        p = proj(name)
        return start_job(slug(name), 'drift', d, lambda c, j, prog: scoring.drift_check(p, c, n_batches=int(d.get('batches', 5)), cancel=j['cancel']))

    @app.get('/api/p/{name}/job')
    def job(name: str):
        return job_state(slug(name))

    @app.delete('/api/p/{name}/job')
    def cancel(name: str):
        j = jobs.get(slug(name))
        if j:
            j['cancel'].set()
        return {'ok': True}

    # ---------- ranking, screening and stopping
    @app.post('/api/p/{name}/rank')
    def rank(name: str):
        p = proj(name); p.freeze_ranking(); return {'ranked': p.n_ranked(), 'counts': p.counts()}

    @app.get('/api/p/{name}/next')
    def next_record(name: str, queue: str = 'ranked', show_score: bool = False):
        p = proj(name); r = p.next_record(queue)
        if r is None:
            return {'record': None, 'stop': p.stop_status()}
        keep = ('rid', 'title', 'abstract', 'authors', 'year', 'journal', 'doi', 'pmid', 'flag', 'rank')
        rec = {k: r.get(k) for k in keep}
        if show_score:
            rec['p'] = r.get('p')
        return {'record': rec, 'stop': p.stop_status()}

    @app.post('/api/p/{name}/decide')
    def decide(name: str, d: dict = Body(...)):
        return proj(name).decide(int(d['rid']), d['decision'])

    @app.post('/api/p/{name}/undo')
    def undo(name: str):
        p = proj(name); return {'undone': p.undo(), 'stop': p.stop_status()}

    @app.post('/api/p/{name}/stop')
    def stop(name: str):
        return proj(name).stop()

    @app.post('/api/p/{name}/audit')
    def audit(name: str, d: dict = Body(...)):
        return proj(name).draw_audit(int(d['size']))

    # ---------- exports
    @app.get('/api/p/{name}/export/{what}')
    def export(name: str, what: str):
        p = proj(name); n = slug(name)
        if what == 'decisions.csv':
            return Response(records_io.to_csv(report.decisions_rows(p), report.DECISION_COLUMNS), media_type='text/csv',
                            headers={'Content-Disposition': f'attachment; filename="{n}_decisions.csv"'})
        if what == 'included.ris':
            return Response(report.included_ris(p), media_type='application/x-research-info-systems',
                            headers={'Content-Disposition': f'attachment; filename="{n}_included.ris"'})
        if what == 'prisma.json':
            return report.prisma(p)
        if what == 'methods.txt':
            return PlainTextResponse(report.methods_text(p))
        if what == 'archive.zip':
            stamp = time.strftime('%Y%m%d-%H%M%S')
            return Response(report.archive(p), media_type='application/zip',
                            headers={'Content-Disposition': f'attachment; filename="{n}_archive_{stamp}.zip"'})
        raise HTTPException(404, 'Unknown export.')

    # ---------- free trial sign-in (GitHub device flow; the proxy checks the token and the quota)
    @app.post('/api/trial/login')
    def trial_login():
        if not (TRIAL_URL and GITHUB_CLIENT_ID):
            raise HTTPException(400, 'No free trial is configured for this installation.')
        r = httpx.post('https://github.com/login/device/code', data={'client_id': GITHUB_CLIENT_ID, 'scope': ''},
                       headers={'Accept': 'application/json'}, timeout=30).json()
        trial['device'] = r
        return {'user_code': r.get('user_code'), 'verification_uri': r.get('verification_uri'), 'interval': r.get('interval', 5)}

    @app.post('/api/trial/poll')
    def trial_poll():
        dv = trial.get('device')
        if not dv:
            raise HTTPException(400, 'Start the sign-in first.')
        r = httpx.post('https://github.com/login/oauth/access_token', headers={'Accept': 'application/json'}, timeout=30,
                       data={'client_id': GITHUB_CLIENT_ID, 'device_code': dv['device_code'], 'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'}).json()
        if r.get('access_token'):
            trial['token'] = r['access_token']; trial['device'] = None
            q = httpx.get(TRIAL_URL.rstrip('/') + '/api/quota', headers={'Authorization': f"Bearer {trial['token']}"}, timeout=30)
            return {'signed_in': True, 'quota': q.json() if q.status_code == 200 else None}
        return {'signed_in': False, 'status': r.get('error')}

    @app.get('/api/trial/quota')
    def trial_quota():
        if not trial['token']:
            return {'signed_in': False}
        q = httpx.get(TRIAL_URL.rstrip('/') + '/api/quota', headers={'Authorization': f"Bearer {trial['token']}"}, timeout=30)
        return {'signed_in': True, 'quota': q.json() if q.status_code == 200 else None}

    @app.get('/')
    def index():
        return FileResponse(WEB / 'index.html')

    app.mount('/static', StaticFiles(directory=WEB), name='static')
    return app


def serve(projects_dir, host='127.0.0.1', port=8765, open_browser=True):
    import uvicorn
    token = secrets.token_urlsafe(24)
    app = create_app(projects_dir, token)
    url = f'http://{host}:{port}/#t={token}'
    print(f'ReviewFast {__version__}\nProjects: {Path(projects_dir).expanduser()}\nOpen: {url}\n(Ctrl+C to stop)', flush=True)
    if open_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level='warning')
