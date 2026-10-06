"""MCP server: the tools are listed, and a full screen runs through them on the Theobald_2021 records (the paper's stored Jev
probabilities replayed through a mock transport, the review's own labels as the reviewer's decisions)."""
import asyncio

import httpx
import pytest

pytest.importorskip('mcp')

from conftest import replay_handler, split_block
from reviewfast import mcp_server, records_io, scoring
from reviewfast.jev_client import JevClient
from reviewfast.project import Project

EXPECTED = {'check_stopping', 'create_project', 'import_records', 'project_status', 'estimate_scoring_cost', 'score_records',
            'freeze_ranking', 'next_record', 'record_decision', 'undo_last_decision', 'stop_screening', 'export_results'}


def test_tools_listed():
    tools = asyncio.run(mcp_server.server.list_tools())
    assert {t.name for t in tools} == EXPECTED


def test_check_stopping_matches_library(stopping_ref):
    from reviewfast import stopping
    labels = [1, 1, 0, 1, 0, 0, 0, 0, 0, 0] * 5
    out = mcp_server.check_stopping(labels, 200)
    assert out['p_value'] == stopping.status(labels, 200).p_value and out['screened'] == 50


def test_score_requires_notice_and_key(tmp_path, theobald, monkeypatch):
    path = str(tmp_path / 'p.reviewfast')
    mcp_server.create_project(path, *split_block(theobald['criteria_block']))
    assert mcp_server.score_records(path, False)['scored'] is False
    monkeypatch.delenv('AI_GATEWAY_API_KEY', raising=False)
    assert 'AI_GATEWAY_API_KEY' in mcp_server.score_records(path, True)['reason']


def test_full_screen_through_tools(tmp_path, theobald):
    path = str(tmp_path / 't.reviewfast')
    mcp_server.create_project(path, *split_block(theobald['criteria_block']))
    ris = tmp_path / 'search.ris'
    ris.write_text(records_io.to_ris([{'title': r['title'], 'abstract': r['abstract']} for r in theobald['records']]))
    out = mcp_server.import_records(path, [str(ris)])
    assert out['counts']['unique'] == 70
    assert mcp_server.estimate_scoring_cost(path)['records_to_score'] == 55
    handler, _ = replay_handler(theobald)                     # scoring itself goes through the library with a mock transport
    p = Project(path); scoring.run(p, JevClient('replay', gap_ms=0, transport=httpx.MockTransport(handler))); p.close()
    assert mcp_server.freeze_ranking(path)['ranked'] == 55
    label = {r['title']: r['label'] for r in theobald['records']}
    while True:
        nxt = mcp_server.next_record(path)['record']
        assert 'probability' not in nxt                       # hidden by default
        res = mcp_server.record_decision(path, nxt['rid'], 'include' if label[nxt['title']] else 'exclude')
        if res['stopping_met']:
            break
    assert mcp_server.stop_screening(path)['met']
    while (m := mcp_server.next_record(path, queue='manual')['record']) is not None:
        mcp_server.record_decision(path, m['rid'], 'include' if label[m['title']] else 'exclude')
    files = mcp_server.export_results(path, str(tmp_path / 'out'))['written']
    assert all((tmp_path / 'out' / n).exists() for n in ('decisions.csv', 'included.ris', 'methods.txt', 'archive.zip'))
    assert len(files) == 4
