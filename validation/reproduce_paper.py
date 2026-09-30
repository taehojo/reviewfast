"""Checks jevscreen.stopping against the stored analysis of the accompanying paper (read only).

1. p values: recomputes the criterion at the 3,148 evaluation points of the buscarpy cross-check (AN-0001-08) from the stored label
   sequences and compares them with buscarpy 0.0.2 and with the paper's own implementation.
2. Combined workflow: ranks each SYNERGY held-out review and each CLEF review by the stored Jev probabilities (seeds 0-9, ties
   broken as in the paper) and applies the criterion, which should give 87.6% read with 23/23 reliable reviews (held out) and
   86.1% with 28/28 (CLEF).

Usage: python validation/reproduce_paper.py /path/to/jev   (the root of the paper's analysis directory)
"""
import csv
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from jevscreen.stopping import h0_pvalue  # noqa: E402

ROOT = sys.argv[1] if len(sys.argv) > 1 else '/N/project/AiLab/jev'
AN = os.path.join(ROOT, 'review_pipeline/rounds/round_0001/revision/analysis/AN-0001-08')


def check_pvalues():
    curves = json.load(open(os.path.join(AN, 'stat_p_curves_hybrid.json')))
    rows = list(csv.DictReader(open(os.path.join(AN, 'buscarpy_check.csv'))))
    d_bus, d_paper, nan, mism = [], [], 0, 0
    for r in rows:
        c = curves[r['key']]; t = int(r['t']); N = int(r['N'])
        p, _ = h0_pvalue(c['labels_in_order'][:t], N)
        d_paper.append(abs(p - float(r['p_fast'])))
        pb = float(r['p_buscarpy'] or 'nan')
        if math.isnan(pb):
            nan += 1; continue
        d_bus.append(abs(p - pb)); mism += (p < 0.05) != (pb < 0.05)
    print(f'p values: {len(rows)} points; max |diff| vs paper implementation {max(d_paper):.2e}; '
          f'vs buscarpy {max(d_bus):.2e} over {len(d_bus)} non-nan points ({nan} nan in buscarpy); decision mismatches {mism}')
    # The paper's implementation has one decision mismatch with buscarpy (heldout|Donners_2021, t = 114), where p equals 0.05 up to
    # floating-point rounding (buscarpy_check.json, n_decision_mismatch = 1). The port reproduces that point, not a new mismatch.
    return max(d_paper) < 1e-10 and max(d_bus) < 1e-10 and mism <= 1


def schedule(N):
    step = 1 if N <= 2000 else max(1, N // 2000)
    s = set(range(step, N + 1, step)); s.add(N)
    return sorted(s)


def combined(records, jev):
    R = json.load(open(os.path.join(ROOT, records)))
    J = {d['id']: d['p'] for d in json.load(open(os.path.join(ROOT, jev))) if d.get('ok') and d.get('p') is not None}
    by = {}
    for r in R:
        by.setdefault(r['review'], []).append(r)
    works, reliable = [], 0
    for k, rs in sorted(by.items()):
        if not sum(r['label'] for r in rs):
            continue                      # topics without an included record cannot be evaluated (as in the paper)
        y = np.array([r['label'] for r in rs]); p = np.array([J[r['id']] for r in rs], float); N = len(y); n1 = y.sum()
        w, rec = [], []
        for s in range(10):
            rng = np.random.default_rng(s); seq = np.lexsort((rng.random(N), -p)); ys = y[seq]
            kk = N
            for t in schedule(N):
                if h0_pvalue(ys[:t], N)[0] < 0.05:
                    kk = t; break
            w.append(kk / N); rec.append(ys[:kk].sum() / n1)
        works.append(np.mean(w)); reliable += np.mean(rec) >= 0.95
    return len(works), float(np.mean(works)), int(reliable)


if __name__ == '__main__':
    ok = check_pvalues()
    for name, rec, jev in [('SYNERGY held out', 'synergy/test_records.json', 'synergy/jev_test.json'),
                           ('CLEF 2019', 'clef/clef_records.json', 'synergy/jev_clef.json')]:
        n, w, rel = combined(rec, jev)
        print(f'{name}: {n} reviews, mean proportion read {w:.4f}, reliable {rel}/{n}')
    print('p-value check', 'PASSED' if ok else 'FAILED')
