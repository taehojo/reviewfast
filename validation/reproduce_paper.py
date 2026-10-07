"""Reproduces the stopping results of the accompanying paper from the public per-record data in validation/data/.

The data are the stored outputs of the evaluation: for each record of the 23 held-out SYNERGY+ reviews and the 31 CLEF 2019
reviews, the review, the record identifier, the label (final inclusion; CLEF content-level relevance) and the stored Jev
probability, in the data-file order the analysis used; the ASReview default model's screening orders (seeds 0-9); and the label
sequences and buscarpy 0.0.2 values of the stopping cross-check. No titles or abstracts are included.

Checks (expected values from the paper, Table 3):
1. Stopping criterion: p values at the 3,148 points of the buscarpy cross-check equal buscarpy and the paper's implementation.
2. Label-free ranking with statistical stopping (Jev ranking, ties broken with seeds 0-9): 87.6% read, 23/23 reliable (held out);
   86.1% read, 28/28 (CLEF).
3. ASReview ranking with statistical stopping (seeds 0-9): 90.1%, 23/23 (held out); 86.9%, 28/28 (CLEF).
4. Label-free threshold tau = 0.07: 58.9% read, 23/23 reliable (held out); 39.1% read, 25/28 (CLEF).
"Reliable" means mean recall over the runs of at least 95%; proportions read are means over reviews.

Usage: python validation/reproduce_paper.py [data_dir]   (default: validation/data next to this script)
"""
import csv
import gzip
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from reviewfast.stopping import h0_pvalue  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'data')
TAU = 0.07
EXPECTED = {('heldout', 'jev'): (0.876, 23), ('clef', 'jev'): (0.861, 28), ('heldout', 'asr'): (0.901, 23), ('clef', 'asr'): (0.869, 28),
            ('heldout', 'tau'): (0.589, 23), ('clef', 'tau'): (0.391, 25)}


def gz_json(name):
    with gzip.open(os.path.join(DATA, name), 'rt') as f:
        return json.load(f)


def check_pvalues():
    seqs = gz_json('stopping_crosscheck_sequences.json.gz')
    with gzip.open(os.path.join(DATA, 'stopping_crosscheck_buscarpy.csv.gz'), 'rt') as f:
        rows = list(csv.DictReader(f))
    d_bus, d_paper, nan, mism = [], [], 0, 0
    for r in rows:
        c = seqs[r['key']]; t = int(r['t']); N = int(r['N'])
        p, _ = h0_pvalue(c['labels_in_order'][:t], N)
        d_paper.append(abs(p - float(r['p_fast'])))
        pb = float(r['p_buscarpy'] or 'nan')
        if math.isnan(pb):
            nan += 1; continue
        d_bus.append(abs(p - pb)); mism += (p < 0.05) != (pb < 0.05)
    print(f'1. p values: {len(rows)} points; max |diff| vs paper implementation {max(d_paper):.2e}; '
          f'vs buscarpy {max(d_bus):.2e} over {len(d_bus)} points ({nan} undefined in buscarpy); decision mismatches {mism}')
    # One decision differs from buscarpy (heldout|Donners_2021, t = 114), where p equals 0.05 up to floating-point rounding; the
    # paper's implementation differs at the same point.
    return max(d_paper) < 1e-10 and max(d_bus) < 1e-10 and mism <= 1


def schedule(N):
    """Evaluation points of the paper: after every record when N <= 2000, else every N // 2000 records (and at N)."""
    step = 1 if N <= 2000 else max(1, N // 2000)
    s = set(range(step, N + 1, step)); s.add(N)
    return sorted(s)


def stop_point(ys, N):
    for t in schedule(N):
        if h0_pvalue(ys[:t], N)[0] < 0.05:
            return t
    return N


def load(coll):
    with gzip.open(os.path.join(DATA, f'{coll}_records.csv.gz'), 'rt') as f:
        rows = list(csv.DictReader(f))
    by = {}
    for r in rows:
        by.setdefault(r['review'], []).append(r)
    return by, gz_json(f'{coll}_asreview_orders.json.gz')


def evaluate(coll):
    by, orders = load(coll)
    out = {m: {'work': [], 'reliable': 0} for m in ('jev', 'asr', 'tau')}
    for k, rs in sorted(by.items()):
        y = np.array([int(r['label']) for r in rs]); p = np.array([float(r['jev_p']) for r in rs]); N = len(y); n1 = y.sum()
        if not n1:
            continue                       # reviews without a relevant record cannot be evaluated (3 CLEF reviews)
        runs = {'jev': [], 'asr': []}
        for s in range(10):
            seq = np.lexsort((np.random.default_rng(s).random(N), -p)); ys = y[seq]; kk = stop_point(ys, N)
            runs['jev'].append((kk / N, ys[:kk].sum() / n1))
            seq = np.array(orders[f'{k}|asr_prior|{s}']); ys = y[seq]; kk = stop_point(ys, len(seq))
            runs['asr'].append((kk / N, ys[:kk].sum() / n1))
        for m in ('jev', 'asr'):
            out[m]['work'].append(np.mean([w for w, _ in runs[m]])); out[m]['reliable'] += np.mean([r for _, r in runs[m]]) >= 0.95
        keep = p >= TAU
        out['tau']['work'].append(keep.mean()); out['tau']['reliable'] += y[keep].sum() / n1 >= 0.95
    return {m: (float(np.mean(v['work'])), int(v['reliable']), len(v['work'])) for m, v in out.items()}


if __name__ == '__main__':
    ok = check_pvalues()
    names = {'jev': 'label-free Jev ranking + statistical stopping', 'asr': 'ASReview ranking + statistical stopping',
             'tau': f'label-free threshold tau = {TAU}'}
    res = {coll: evaluate(coll) for coll in ('heldout', 'clef')}
    for i, m in enumerate(('jev', 'asr', 'tau'), start=2):
        for coll in ('heldout', 'clef'):
            w, rel, n = res[coll][m]; ew, erel = EXPECTED[(coll, m)]
            match = round(w, 3) == ew and rel == erel
            ok &= match
            print(f'{i}. {names[m]}, {"SYNERGY+ held out" if coll == "heldout" else "CLEF 2019"}: {n} reviews, '
                  f'{100 * w:.1f}% read, reliable {rel}/{n} (paper: {100 * ew:.1f}%, {erel}/{n}) {"OK" if match else "DIFFERS"}')
    print('All checks', 'PASSED' if ok else 'FAILED')
    sys.exit(0 if ok else 1)
