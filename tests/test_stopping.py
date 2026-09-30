import numpy as np

from jevscreen import stopping


def test_matches_buscarpy(stopping_ref):
    """p values equal buscarpy 0.0.2 calculate_h0 at the points of the paper's cross-check (nan in buscarpy = H0 impossible)."""
    n = 0
    for r in stopping_ref['buscarpy']:
        seq = stopping_ref['sequences'][r['key']]
        p, _ = stopping.h0_pvalue(seq['labels_in_order'][:r['t']], seq['N'])
        if r['p_buscarpy'] is not None:
            assert abs(p - r['p_buscarpy']) < 1e-10, r
            n += 1
    assert n > 100


def test_theobald_stop_points_match_paper(theobald, stopping_ref):
    """Jev-only order from the stored probabilities (ties broken as in the paper, seeds 0-9) stops where the paper reported."""
    y = np.array([r['label'] for r in theobald['records']]); p = np.array([r['paper_p'] for r in theobald['records']], float); N = len(y)
    for seed, exp in stopping_ref['jev_only_stat_k']['Theobald_2021'].items():
        rng = np.random.default_rng(int(seed)); seq = np.lexsort((rng.random(N), -p))
        k = stopping.first_stop(y[seq], N)
        assert k == exp['stat_k']
        assert y[seq][:k].sum() / y.sum() == exp['stat_rec']


def test_status_before_and_after():
    st = stopping.status([], 100)
    assert not st.met and st.p_value == 1.0
    st = stopping.status([0] * 100, 100)       # everything screened: recall is 1 by definition
    assert st.met
