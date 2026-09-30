"""Statistical stopping criterion of Callaghan and Mueller-Hansen (2020, Syst Rev 9:273).

Ranked quasi-sampling variant, equivalent to buscarpy.calculate_h0. The null hypothesis is that recall is below the target.
For every look-back window (the last i screened records treated as a sample from the i + unseen records), Ktar is the smallest
number of relevant records in that population compatible with H0, and p = P(X <= k) with X ~ Hypergeometric(population, Ktar, i).
Screening can stop when the smallest p over all windows falls below 1 - confidence.

Ported from the analysis code of the accompanying paper (AN-0001-08, an08_stopping.py), which matched buscarpy 0.0.2 to within
3e-11 on 3,148 evaluation points. The only change is that the factorial table uses math.lgamma instead of scipy.special.gammaln.
"""
import math
from dataclasses import dataclass, asdict

import numpy as np

RECALL_TARGET = 0.95
CONFIDENCE = 0.95

_LF = np.zeros(0)


def _lnfact_table(n):
    global _LF
    if len(_LF) < n + 2:
        _LF = np.array([math.lgamma(m + 1.0) for m in range(n + 2)])   # _LF[m] = ln m!
    return _LF


def _lnC(a, b):
    """ln C(a, b) elementwise for integer arrays; -inf where the coefficient is zero (b < 0 or b > a)."""
    ok = (b >= 0) & (b <= a) & (a >= 0)
    aa = np.clip(a, 0, None); bb = np.clip(b, 0, None); ab = np.clip(aa - bb, 0, None)
    v = _LF[aa] - _LF[bb] - _LF[ab]
    return np.where(ok, v, -np.inf)


def h0_pvalue(labels, N, recall_target=RECALL_TARGET):
    """labels: 0/1 relevance of the records screened so far, in screening order. N: size of the ranked set.

    Returns (min p over windows, number of windows where H0 is impossible). Windows where Ktar exceeds the population get p = 0
    because H0 cannot hold there (buscarpy returns nan in that case).
    """
    lab = np.asarray(labels, np.int64); t = len(lab); r_seen = int(lab.sum()); D = N - t
    if t == 0:
        return 1.0, 0
    c = int(math.floor(r_seen / recall_target + 1)) - r_seen           # same floor expression as buscarpy.calculate_h0
    cum = np.cumsum(lab[::-1]); sizes = np.arange(1, t + 1); M = D + sizes; K = cum + c
    impossible = K > M
    if D <= 0:
        return 0.0, int(impossible.sum())                               # nothing unseen: recall = 1
    _lnfact_table(N)
    lnCMD = _lnC(M, np.full(t, D)); S = np.zeros(t)
    for x in range(0, min(c, D + 1)):                                   # x = 0 .. c-1 (terms with x > D vanish)
        S += np.exp(_lnC(K, np.full(t, x)) + _lnC(M - K, np.full(t, D - x)) - lnCMD)
    p = np.clip(1.0 - S, 0.0, 1.0); p[impossible] = 0.0
    return float(p.min()), int(impossible.sum())


@dataclass
class StopStatus:
    screened: int
    ranked_total: int
    relevant_seen: int
    p_value: float
    recall_target: float
    confidence: float
    met: bool

    @property
    def proportion_screened(self):
        return self.screened / self.ranked_total if self.ranked_total else 0.0

    def to_dict(self):
        d = asdict(self); d['proportion_screened'] = self.proportion_screened
        return d


def status(labels, N, recall_target=RECALL_TARGET, confidence=CONFIDENCE):
    """Stopping status after screening `labels` (in ranked order) out of N ranked records."""
    p, _ = h0_pvalue(labels, N, recall_target)
    return StopStatus(screened=len(labels), ranked_total=N, relevant_seen=int(np.sum(labels)), p_value=p,
                      recall_target=recall_target, confidence=confidence, met=bool(len(labels) > 0 and p < 1 - confidence))


def first_stop(labels_in_order, N, recall_target=RECALL_TARGET, confidence=CONFIDENCE, points=None):
    """Number of records read when the criterion is first met, evaluated at `points` (default: after every record); N if never."""
    lab = np.asarray(labels_in_order)
    for t in (points if points is not None else range(1, N + 1)):
        p, _ = h0_pvalue(lab[:t], N, recall_target)
        if p < 1 - confidence:
            return int(t)
    return int(N)
