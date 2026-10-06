"""Scores all unscored records of a project with Jev, in batches of 10, with a small number of concurrent workers sharing one pacer."""
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor

from .jev_client import BATCH_SIZE, JevClient, build_request, parse_answers


def run(project, client: JevClient, workers=2, cancel=None, progress=None):
    """Scores project.to_score(). Returns a summary dict. `progress(done, total, spent, throttled)` is called after each batch."""
    todo = project.to_score()
    units = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    crit = project.criteria_text(); total = len(todo); done = [0]; lock = threading.Lock()
    project.log('scoring_started', {'records': total, 'requests': len(units), 'endpoint': client.endpoint})

    def one(unit):
        if cancel is not None and cancel.is_set():
            return
        body = build_request(crit, unit)
        res = client.send(body, cancel)
        if res['error'] == 'cancelled':
            return
        probs = parse_answers(res['response'], len(unit)) if res['response'] else [None] * len(unit)
        project.save_batch([u['rid'] for u in unit], body, res, client.endpoint, 'score', probs)
        with lock:
            done[0] += len(unit)
            if progress:
                progress(done[0], total, client.spent, client.pacer.throttled)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, units))
    summary = {**project.scoring_summary(), 'cancelled': bool(cancel is not None and cancel.is_set()), 'throttled': client.pacer.throttled}
    project.log('scoring_finished', summary)
    return summary


def drift_check(project, client: JevClient, n_batches=5, cancel=None):
    """Re-sends up to n_batches archived scoring requests unchanged and compares the new probabilities with the stored ones.
    A change points to a change of the model or its serving; the paper recommends this check because Jev reports no version."""
    rows = project.db.execute("SELECT bid, rids, request FROM batches WHERE purpose='score' AND status=200 ORDER BY bid").fetchall()
    if not rows:
        return None
    pick = random.Random().sample(list(rows), min(n_batches, len(rows)))
    old = {r[0]: r[1] for r in project.db.execute('SELECT rid, p FROM scores WHERE p IS NOT NULL')}
    pairs = []
    for r in pick:
        if cancel is not None and cancel.is_set():
            break
        rids = json.loads(r['rids']); body = json.loads(r['request'])
        res = client.send(body, cancel)
        probs = parse_answers(res['response'], len(rids)) if res['response'] else [None] * len(rids)
        project.save_batch(rids, body, res, client.endpoint, 'drift_check')
        pairs += [(old[rid], p) for rid, p in zip(rids, probs) if p is not None and rid in old]
    if not pairs:
        return {'records': 0}
    d = [abs(a - b) for a, b in pairs]
    out = {'batches': len(pick), 'records': len(pairs), 'identical': sum(x == 0 for x in d), 'mean_abs_diff': sum(d) / len(d), 'max_abs_diff': max(d)}
    project.log('drift_check', out)
    return out
