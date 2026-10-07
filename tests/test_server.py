from fastapi.testclient import TestClient

from conftest import split_block
from reviewfast import records_io
from reviewfast.server import create_app


def test_token_required_and_basic_flow(tmp_path, theobald):
    c = TestClient(create_app(tmp_path, 'secret'))
    assert c.get('/api/projects').status_code == 401
    assert c.get('/api/projects', headers={'X-Token': 'wrong'}).status_code == 401
    h = {'X-Token': 'secret'}
    t, q, crit = split_block(theobald['criteria_block'])
    assert c.post('/api/projects', headers=h, json={'name': 'theo bald', 'title': t, 'question': q, 'criteria': crit}).json() == {'name': 'theo-bald'}
    ris = records_io.to_ris([{'title': r['title'], 'abstract': r['abstract']} for r in theobald['records']])
    r = c.post('/api/p/theo-bald/import', headers=h, files={'file': ('search.ris', ris.encode())}).json()
    assert r['added'] == 70
    s = c.get('/api/p/theo-bald', headers=h).json()
    assert s['to_score'] == 55 and not s['ranked']
    # scoring without consent or key is refused before anything is sent
    assert c.post('/api/p/theo-bald/score', headers=h, json={'mode': 'key', 'key': 'k'}).status_code == 400
    assert c.get('/api/p/theo-bald/next', headers=h).json()['record'] is None
    assert c.post('/api/p/theo-bald/rank', headers=h).status_code == 409      # nothing scored yet
    assert c.get('/').status_code == 200 and c.get('/static/app.js').status_code == 200
    assert c.get('/api/p/..%2Fetc', headers=h).status_code in (400, 404)


def test_audit_endpoints(tmp_path, theobald):
    """Random-sample check after stopping through the local app's API: scoring is replayed with the paper's stored probabilities."""
    import httpx
    from conftest import replay_handler
    from reviewfast import scoring
    from reviewfast.jev_client import JevClient
    from reviewfast.project import Project
    t, q, crit = split_block(theobald['criteria_block'])
    p = Project.create(tmp_path / 'aud.reviewfast', t, q, crit, seed=3)
    p.import_records(records_io.parse_ris(records_io.to_ris([{'title': r['title'], 'abstract': r['abstract']} for r in theobald['records']])), 'ris')
    handler, _ = replay_handler(theobald); scoring.run(p, JevClient('replay', gap_ms=0, transport=httpx.MockTransport(handler))); p.freeze_ranking()
    label = {r['title']: r['label'] for r in theobald['records']}
    while not p.decide((r := p.next_record())['rid'], 'include' if label[r['title']] else 'exclude')['met']:
        pass
    p.close()
    c = TestClient(create_app(tmp_path, 'secret')); h = {'X-Token': 'secret'}
    assert c.post('/api/p/aud/audit', headers=h, json={'size': 5}).status_code == 409      # not stopped yet
    assert c.post('/api/p/aud/stop', headers=h).status_code == 200
    a = c.post('/api/p/aud/audit', headers=h, json={'size': 5}).json()
    assert a['drawn'] == min(5, a['pool']) and c.get('/api/p/aud', headers=h).json()['audit']['drawn'] == a['drawn']
    while (rec := c.get('/api/p/aud/next?queue=audit', headers=h).json()['record']) is not None:
        c.post('/api/p/aud/decide', headers=h, json={'rid': rec['rid'], 'decision': 'exclude'})
    s = c.get('/api/p/aud', headers=h).json()
    assert s['audit']['screened'] == a['drawn'] and s['stop']['screened'] == s['meta']['stopped_at']
    assert c.get('/api/p/aud/export/prisma.json', headers=h).json()['audit']['screened'] == a['drawn']
