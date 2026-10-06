"""Writes reference outputs of the Python package for the JavaScript core tests (site/test/core.test.js):
request bodies for the public Theobald_2021 records, stopping p values on the paper's reference sequences, parsed RIS records."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, '..', '..')
sys.path.insert(0, ROOT)
from reviewfast import jev_client, records_io, stopping

th = json.load(open(os.path.join(ROOT, 'tests', 'fixtures', 'theobald_2021.json')))
ref = json.load(open(os.path.join(ROOT, 'tests', 'fixtures', 'stopping_reference.json')))
head, crit = th['criteria_block'].split('\nEligibility criteria reported by the review authors:\n', 1)
t, q = head.split('\nResearch question: ', 1); t = t.removeprefix('Systematic review title: ')
block = jev_client.criteria_block(t, q, crit)
recs = [{'title': r['title'], 'abstract': r['abstract']} for r in th['records']]
out = {'criteria': [t, q, crit], 'block': block,
       'request_batch': jev_client.build_request(block, recs[:10]), 'request_single': jev_client.build_request(block, recs[:1]),
       'h0': []}
for key, seq in ref['sequences'].items():
    lab = seq['labels_in_order']; N = seq['N']
    for tt in range(1, len(lab) + 1, max(1, len(lab) // 60)):
        p, _ = stopping.h0_pvalue(lab[:tt], N)
        out['h0'].append({'key': key, 't': tt, 'p': p})
ris = records_io.to_ris([{'title': r['title'], 'abstract': r['abstract'], 'year': '2021', 'doi': 'https://doi.org/10.1/X' + str(i), 'authors': 'A, B; C, D'}
                         for i, r in enumerate(th['records'][:12])])
out['ris_text'] = ris; out['ris_parsed'] = records_io.parse_ris(ris)
out['non_english'] = [records_io.looks_non_english(r) for r in recs]
json.dump(out, open(os.path.join(HERE, 'reference.json'), 'w'))
print('h0 points', len(out['h0']), 'non_english', sum(out['non_english']))
