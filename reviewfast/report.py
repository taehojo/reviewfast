"""PRISMA counts, a draft methods paragraph and a reproducibility archive for a screening project."""
import hashlib
import io
import json
import sqlite3
import zipfile

from . import __version__
from .jev_client import BATCH_SIZE, MODEL, QUESTION
from .records_io import to_csv, to_ris

STOP_REF = ('Callaghan MW, Müller-Hansen F. Statistical stopping criteria for automated screening in systematic reviews. '
            'Syst Rev. 2020;9:273.')


def _n(k, noun):
    return f'{k} {noun}' + ('' if k == 1 else 's')


def prisma(project):
    c = project.counts()
    st = project.stop_status()
    screened_ranked = sum(c[f'ranked_{d}'] for d in ('include', 'exclude', 'maybe'))
    screened_manual = sum(c[f'manual_{d}'] for d in ('include', 'exclude', 'maybe'))
    return {
        'records_identified': c['imported'],
        'duplicates_removed': c['duplicates'],
        'records_after_deduplication': c['unique'],
        'records_ranked_by_jev': c['ranked'],
        'records_for_manual_screening': c['manual_queue'],
        'manual_screening_reasons': c['manual_by_reason'],
        'records_screened': screened_ranked + screened_manual,
        'records_screened_ranked': screened_ranked,
        'records_screened_manual': screened_manual,
        'records_not_screened_after_stopping': c['ranked_unscreened'] if st['stopped_at'] is not None else 0,
        'records_not_yet_screened': (0 if st['stopped_at'] is not None else c['ranked_unscreened']) + st['manual_remaining'],
        'records_excluded': c['ranked_exclude'] + c['manual_exclude'],
        'records_sought_for_retrieval': c['ranked_include'] + c['ranked_maybe'] + c['manual_include'] + c['manual_maybe'],
        'stopping': st,
    }


def _span(project):
    r = project.db.execute("SELECT MIN(ts), MAX(ts) FROM batches WHERE purpose='score' AND status=200").fetchone()
    return (r[0] or '')[:10], (r[1] or '')[:10]


def methods_text(project):
    P = prisma(project); st = P['stopping']; s = project.scoring_summary(); first, last = _span(project)
    rt = project.meta('recall_target'); conf = project.meta('confidence')
    when = first if first == last else f'between {first} and {last}'
    parts = [
        f"We screened titles and abstracts with ReviewFast {__version__}. Of {_n(P['records_identified'], 'record')} retrieved, "
        + (f"we removed {_n(P['duplicates_removed'], 'duplicate')} (matched on DOI, PubMed identifier, or normalised title and year), leaving "
           f"{_n(P['records_after_deduplication'], 'unique record')}." if P['duplicates_removed'] else
           "we found no duplicates (matched on DOI, PubMed identifier, or normalised title and year)."),
        f"We scored {_n(P['records_ranked_by_jev'], 'record')} with Jev (model alias {MODEL}, accessed through the Vercel AI Gateway {when}; "
        f"the provider does not report a model version). Each request contained the review title, research question and eligibility "
        f"criteria from our protocol and up to {BATCH_SIZE} records in random order, and asked for each record: \"{QUESTION}\"",
        f"We screened the ranked records in descending order of the Jev probability, with the probabilities hidden from the screener. "
        f"We counted records marked as possibly relevant as relevant for the stopping criterion.",
    ]
    if st['stopped_at'] is not None:
        parts.append(
            f"We stopped screening the ranked records when the statistical stopping criterion of Callaghan and Müller-Hansen "
            f"(ranked quasi-sampling variant) rejected the hypothesis that recall was below {rt:.2f} at the {1 - conf:.2f} level "
            f"(p = {st['p_value']:.3g}), after screening {st['stopped_at']} of {st['ranked_total']} ranked records "
            f"({100 * st['stopped_at'] / st['ranked_total']:.1f}%). We did not screen the remaining "
            f"{_n(P['records_not_screened_after_stopping'], 'record')}.")
    elif P['records_not_yet_screened'] == 0:
        parts.append(f"We screened all {_n(P['records_ranked_by_jev'], 'ranked record')}.")
    else:
        parts.append(f"At the time of this report, {_n(P['records_not_yet_screened'], 'record')} had not yet been screened and the "
                     f"stopping criterion had not been applied.")
    reasons = P['manual_screening_reasons']
    if P['records_for_manual_screening']:
        why = [f"{reasons['no_abstract']} without an abstract" if reasons.get('no_abstract') else '',
               f"{reasons['non_english']} flagged as not in English" if reasons.get('non_english') else '',
               f"{reasons['no_score']} without a usable Jev score" if reasons.get('no_score') else '',
               f"{reasons['user']} set aside by the reviewers" if reasons.get('user') else '']
        parts.append(f"We screened {_n(P['records_for_manual_screening'], 'record')} in full outside the ranking "
                     f"({'; '.join(w for w in why if w)}), following the recommendation to screen such records without the model ranking.")
    parts.append(f"The Jev requests used {s['tokens']} input tokens and cost US${s['cost_usd']:.2f}. We have archived every request, "
                 f"response and score so that the ranking can be audited.")
    return ' '.join(parts) + f"\n\nReference: {STOP_REF}\n"


def decisions_rows(project):
    rows = project.db.execute(
        'SELECT r.rid, r.source_id, r.pmid, r.doi, r.title, r.year, r.journal, r.flag, r.dup_of, k.rank, s.p, s.error AS score_error, '
        'd.decision, d.queue, d.seq, d.ts FROM records r LEFT JOIN ranking k ON k.rid=r.rid LEFT JOIN scores s ON s.rid=r.rid '
        'LEFT JOIN decisions d ON d.rid=r.rid ORDER BY r.rid').fetchall()
    return [dict(r) for r in rows]


DECISION_COLUMNS = ['rid', 'source_id', 'pmid', 'doi', 'title', 'year', 'journal', 'flag', 'dup_of', 'rank', 'p', 'score_error',
                    'decision', 'queue', 'seq', 'ts']


def included_ris(project):
    rows = project.db.execute("SELECT r.*, d.decision FROM records r JOIN decisions d ON d.rid=r.rid WHERE d.decision IN ('include','maybe') "
                              "ORDER BY d.seq").fetchall()
    return to_ris([{**dict(r), 'note': f"ReviewFast decision: {r['decision']}"} for r in rows])


def _db_bytes(db):
    """Consistent copy of the project database as bytes (Connection.serialize needs Python 3.11; older versions use a file)."""
    mem = sqlite3.connect(':memory:'); db.backup(mem)
    if hasattr(mem, 'serialize'):
        data = mem.serialize(); mem.close(); return data
    mem.close()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, 'project.sqlite'); dst = sqlite3.connect(f); db.backup(dst); dst.close()
        with open(f, 'rb') as fh:
            return fh.read()


def archive(project):
    """Zip with a consistent copy of the project database, readable exports and SHA-256 sums."""
    files = {}
    buf = io.BytesIO()
    files['project.sqlite'] = _db_bytes(project.db)
    meta = {r[0]: json.loads(r[1]) for r in project.db.execute('SELECT key, value FROM meta')}
    files['meta.json'] = json.dumps(meta, indent=1).encode()
    files['criteria_block.txt'] = project.criteria_text().encode()
    files['decisions.csv'] = to_csv(decisions_rows(project), DECISION_COLUMNS).encode()
    files['requests.jsonl'] = '\n'.join(json.dumps({k: (json.loads(v) if k in ('rids', 'request', 'response') and v else v) for k, v in dict(r).items()})
                                        for r in project.db.execute('SELECT * FROM batches ORDER BY bid')).encode()
    files['events.json'] = json.dumps(project.events(), indent=1).encode()
    files['prisma.json'] = json.dumps(prisma(project), indent=1).encode()
    files['methods.txt'] = methods_text(project).encode()
    files['SHA256SUMS'] = ''.join(f'{hashlib.sha256(v).hexdigest()}  {k}\n' for k, v in files.items()).encode()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()
