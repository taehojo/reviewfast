import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))


@pytest.fixture(scope='session')
def theobald():
    """70 public records of SYNERGY review Theobald_2021 with labels and the Jev probabilities stored by the paper."""
    return json.load(open(os.path.join(HERE, 'fixtures', 'theobald_2021.json')))


@pytest.fixture(scope='session')
def stopping_ref():
    return json.load(open(os.path.join(HERE, 'fixtures', 'stopping_reference.json')))


def split_block(block):
    head, crit = block.split('\nEligibility criteria reported by the review authors:\n', 1)
    t, q = head.split('\nResearch question: ', 1)
    return t.removeprefix('Systematic review title: '), q, crit


def replay_handler(theobald, fail_first=None):
    """httpx handler that answers each request with the Jev probabilities the paper stored for these records (real outputs,
    replayed; the paper batched the records in dataset order, so neighbours may differ from the request). `fail_first` lists HTTP
    statuses to return before answering, to exercise the retry path."""
    import re

    import httpx
    by_title = {r['title']: r['paper_p'] for r in theobald['records']}
    state = {'n': 0}

    def handler(request):
        state['n'] += 1
        if fail_first and state['n'] <= len(fail_first):
            return httpx.Response(fail_first[state['n'] - 1], headers={'retry-after': '0'}, json={'error': {'message': 'status under test'}})
        body = json.loads(request.content); qs = list(body['questions'])
        answers = {}
        for i, q in enumerate(qs):
            pat = r'\[Record R%d\]\nTitle: (.*)\n' % (i + 1) if len(qs) > 1 else r'\n\nTitle: (.*)\n'
            answers[q] = {'noul': by_title[re.search(pat, body['state']).group(1)]}
        return httpx.Response(200, json={'answers': answers, 'usage': {'input_tokens': 0}})
    return handler, state
