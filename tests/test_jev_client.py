import json
import os
import shutil
import subprocess

import httpx
import pytest

from conftest import replay_handler
from reviewfast import jev_client as jc

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_request_identical_to_paper_scorer(theobald):
    """The request bodies are byte-identical to those of the paper's scorer (synergy/run_screen.mjs)."""
    ref = json.loads(subprocess.run(['node', os.path.join(HERE, 'paper_prompt.mjs')], capture_output=True, text=True, check=True).stdout)
    from conftest import split_block
    crit = jc.criteria_block(*split_block(theobald['criteria_block']))
    assert crit == theobald['criteria_block']
    recs = theobald['records']
    ours = [jc.build_request(crit, recs[i:i + 10]) for i in range(0, len(recs), 10)] + [jc.build_request(crit, recs[:1])]
    assert json.dumps(ours) == json.dumps(ref)


def test_retry_on_429_then_success(theobald):
    handler, state = replay_handler(theobald, fail_first=[429, 503])
    c = jc.JevClient('replay', gap_ms=0, transport=httpx.MockTransport(handler))
    crit = theobald['criteria_block']; recs = theobald['records'][:10]
    res = c.send(jc.build_request(crit, recs))
    assert res['status'] == 200 and res['attempts'] == 3 and c.pacer.throttled == 2
    assert jc.parse_answers(res['response'], 10) == [r['paper_p'] for r in recs]


def test_auth_error_is_raised(theobald):
    c = jc.JevClient('bad', gap_ms=0, transport=httpx.MockTransport(lambda r: httpx.Response(403, json={'error': {'message': 'forbidden'}})))
    with pytest.raises(jc.AuthError):
        c.send(jc.build_request(theobald['criteria_block'], theobald['records'][:2]))


def test_missing_answer_is_none():
    assert jc.parse_answers({'answers': {'r1': {'noul': None}}}, 2) == [None, None]
    assert jc.parse_answers({'answers': {'r1': {'noul': 1.7}}}, 1) == [None]
