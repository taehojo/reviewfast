"""Live check of the Jev client on public records: scores the 70 records of SYNERGY review Theobald_2021 through a reviewfast
project, compares the probabilities with those stored by the paper, and records the responses (answers and usage only, no record
text) as the fixture for the offline tests.

The paper sent the records in dataset order; reviewfast sends them in seeded random order, so the batch neighbours differ and some
difference is expected even without a model change. The model version is not reported by the provider.

Usage: AI_GATEWAY_API_KEY=... python validation/live_theobald.py
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..'))
from reviewfast import scoring  # noqa: E402
from reviewfast.jev_client import GATEWAY_URL, JevClient  # noqa: E402
from reviewfast.project import Project  # noqa: E402

FIX = os.path.join(HERE, '..', 'tests', 'fixtures')


def split_block(block):
    head, crit = block.split('\nEligibility criteria reported by the review authors:\n', 1)
    t, q = head.split('\nResearch question: ', 1)
    return t.removeprefix('Systematic review title: '), q, crit


def auc(y, p):
    y = np.asarray(y); p = np.asarray(p, float); pos = p[y == 1]; neg = p[y == 0]
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()) / (len(pos) * len(neg)))


def avg_ranks(x):
    x = np.asarray(x, float); order = np.argsort(x, kind='stable'); r = np.empty(len(x)); i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2; i = j + 1
    return r


def spearman(a, b):
    return float(np.corrcoef(avg_ranks(a), avg_ranks(b))[0, 1])


def main():
    F = json.load(open(os.path.join(FIX, 'theobald_2021.json')))
    title, question, crit = split_block(F['criteria_block'])
    tmp = tempfile.mkdtemp(); path = os.path.join(tmp, 'theobald.reviewfast')
    p = Project.create(path, title, question, crit, seed=20260930)
    assert p.criteria_text() == F['criteria_block'], 'criteria block differs from the paper'
    recs = [{'source_id': r['openalex_id'], 'title': r['title'], 'abstract': r['abstract'], 'language': 'eng'} for r in F['records']]
    c = p.import_records(recs, 'fixture'); print('import', c)
    client = JevClient(os.environ['AI_GATEWAY_API_KEY'], endpoint=GATEWAY_URL)
    started = subprocess.run(['date', '+%Y-%m-%d %H:%M:%S %Z'], capture_output=True, text=True).stdout.strip()
    out = scoring.run(p, client); client.close()
    print('scoring', out)
    by_sid = {r['source_id']: r['rid'] for r in p.records()}
    new = {r[0]: r[1] for r in p.db.execute('SELECT rid, p FROM scores')}
    y = [r['label'] for r in F['records']]; old = [r['paper_p'] for r in F['records']]; nw = [new[by_sid[r['openalex_id']]] for r in F['records']]
    ok = [i for i, v in enumerate(nw) if v is not None]
    res = {'date': started, 'records': len(nw), 'scored': len(ok), 'cost_usd': out['cost_usd'], 'tokens': out['tokens'],
           'auc_paper': auc(y, old), 'auc_now': auc([y[i] for i in ok], [nw[i] for i in ok]),
           'spearman_paper_vs_now': spearman([old[i] for i in ok], [nw[i] for i in ok]),
           'mean_abs_diff': float(np.mean([abs(old[i] - nw[i]) for i in ok])), 'max_abs_diff': float(max(abs(old[i] - nw[i]) for i in ok))}
    print(json.dumps(res, indent=1))
    # recorded responses for offline tests: batch order, record positions (by fixture index), answers and usage
    idx = {by_sid[r['openalex_id']]: i for i, r in enumerate(F['records'])}
    batches = []
    for b in p.db.execute("SELECT rids, response, status FROM batches WHERE purpose='score' ORDER BY bid"):
        resp = json.loads(b['response']) if b['response'] else None
        batches.append({'fixture_index': [idx[r] for r in json.loads(b['rids'])], 'status': b['status'],
                        'response': {k: resp.get(k) for k in ('answers', 'usage', 'provider_metadata')} if resp else None})
    json.dump({'recorded': started, 'project_seed': 20260930, 'summary': res, 'batches': batches},
              open(os.path.join(FIX, 'theobald_2021_jev_responses.json'), 'w'), indent=1)
    json.dump(res, open(os.path.join(HERE, 'live_theobald_result.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
