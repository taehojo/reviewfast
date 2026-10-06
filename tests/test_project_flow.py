"""End to end on the Theobald_2021 records: import, scoring (the paper's stored Jev probabilities replayed), ranking, screening
with the review's own labels in ranked order until the criterion is met, stopping, report and exports."""
import csv
import hashlib
import io
import zipfile

import httpx
import pytest

from conftest import replay_handler, split_block
from reviewfast import records_io, report, scoring
from reviewfast.jev_client import JevClient
from reviewfast.project import Project, ProjectError


@pytest.fixture
def screened(tmp_path, theobald):
    p = Project.create(tmp_path / 't.reviewfast', *split_block(theobald['criteria_block']), seed=7)
    ris = records_io.to_ris([{'title': r['title'], 'abstract': r['abstract'], 'note': r['openalex_id']} for r in theobald['records']])
    fmt, recs = records_io.parse_file('search.ris', ris.encode())
    assert fmt == 'ris' and len(recs) == 70
    c = p.import_records(recs, 'ris:search.ris')
    assert c['added'] == 70 and c['non_english'] == 15       # 14 German and 1 Spanish record in this review
    assert p.import_records(recs[:5], 'again')['duplicates'] == 5
    handler, state = replay_handler(theobald)
    client = JevClient('replay', gap_ms=0, transport=httpx.MockTransport(handler))
    out = scoring.run(p, client)
    assert out['scored'] == 55 and out['failed'] == 0 and state['n'] == 6
    with pytest.raises(ProjectError):
        p.decide(1, 'include')                              # not ranked yet
    p.freeze_ranking()
    with pytest.raises(ProjectError):
        p.update_criteria('x', 'y', 'z')
    label = {r['title']: r['label'] for r in theobald['records']}
    prob = {r['title']: r['paper_p'] for r in theobald['records']}
    first = p.next_record()
    with pytest.raises(ProjectError):
        p.decide(first['rid'] + 1000, 'include')            # out of order / unknown
    ranks = []
    while True:
        r = p.next_record()
        if r is None:
            break
        ranks.append(prob[r['title']])
        st = p.decide(r['rid'], 'include' if label[r['title']] else 'exclude')
        if st['met']:
            break
    assert ranks == sorted(ranks, reverse=True)            # screened in descending Jev probability
    return p, st, label


def test_flow(screened):
    p, st, label = screened
    assert st['met'] and st['screened'] <= 55
    p.stop()
    with pytest.raises(ProjectError):
        p.decide(p.next_record()['rid'], 'exclude')        # the ranked list is closed after stopping
    while (r := p.next_record('manual')) is not None:
        p.decide(r['rid'], 'include' if label[r['title']] else 'exclude')
    P = report.prisma(p)
    assert P['records_identified'] == 75 and P['duplicates_removed'] == 5 and P['records_after_deduplication'] == 70
    assert P['records_screened'] + P['records_not_screened_after_stopping'] == 70 and P['records_not_yet_screened'] == 0
    included = sum(label.values())
    found = P['records_sought_for_retrieval']
    assert found <= included
    m = report.methods_text(p)
    assert '—' not in m and 'We stopped screening' in m and 'does not report a model version' in m


def test_undo_and_exports(screened):
    p, st, label = screened
    n = st['screened']; last = p.undo()
    assert last['queue'] == 'ranked' and p.stop_status()['screened'] == n - 1
    rows = list(csv.DictReader(io.StringIO(records_io.to_csv(report.decisions_rows(p), report.DECISION_COLUMNS))))
    assert len(rows) == 75 and sum(1 for r in rows if r['decision']) == n - 1
    back = records_io.parse_ris(report.included_ris(p))
    assert len(back) == sum(1 for r in rows if r['decision'] in ('include', 'maybe'))
    z = zipfile.ZipFile(io.BytesIO(report.archive(p)))
    sums = dict(line.split('  ')[::-1] for line in z.read('SHA256SUMS').decode().splitlines())
    for name, h in sums.items():
        assert hashlib.sha256(z.read(name)).hexdigest() == h
    assert 'project.sqlite' in sums and 'requests.jsonl' in sums
