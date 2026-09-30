from fastapi.testclient import TestClient

from conftest import split_block
from jevscreen import records_io
from jevscreen.server import create_app


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
