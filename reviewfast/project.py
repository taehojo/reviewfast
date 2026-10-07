"""A screening project is one SQLite file. It keeps the records, every request sent to Jev and its response, the scores, the
frozen ranking, the screening decisions in order and an event log, so that the whole screen can be reported and reproduced."""
import datetime as dt
import json
import random
import re
import sqlite3
import threading

from . import __version__
from .jev_client import criteria_block
from .records_io import looks_non_english, norm_doi
from . import stopping

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS records (
  rid INTEGER PRIMARY KEY, source TEXT, source_id TEXT, title TEXT, abstract TEXT, authors TEXT, year TEXT, journal TEXT,
  doi TEXT, pmid TEXT, language TEXT, dup_of INTEGER, flag TEXT, imported TEXT);
CREATE TABLE IF NOT EXISTS batches (
  bid INTEGER PRIMARY KEY, ts TEXT, endpoint TEXT, purpose TEXT, rids TEXT, request TEXT, response TEXT, status INTEGER,
  error TEXT, tokens INTEGER, cost REAL, attempts INTEGER);
CREATE TABLE IF NOT EXISTS scores (rid INTEGER PRIMARY KEY, p REAL, error TEXT, bid INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS ranking (rid INTEGER PRIMARY KEY, rank INTEGER UNIQUE);
CREATE TABLE IF NOT EXISTS decisions (rid INTEGER PRIMARY KEY, decision TEXT, queue TEXT, seq INTEGER UNIQUE, ts TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, detail TEXT);
"""
DECISIONS = ('include', 'exclude', 'maybe')
MANUAL_FLAGS = ('no_abstract', 'non_english', 'no_score', 'user')


def now():
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec='seconds')


def _title_key(t):
    return re.sub(r'[^a-z0-9]', '', (t or '').lower())


class ProjectError(Exception):
    pass


class Project:
    def __init__(self, path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    # ---------- creation and metadata
    @classmethod
    def create(cls, path, title, question, criteria, recall_target=stopping.RECALL_TARGET, confidence=stopping.CONFIDENCE, seed=None):
        p = cls(path)
        if p.meta('created'):
            raise ProjectError('A project already exists at this path.')
        seed = seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
        for k, v in dict(created=now(), app_version=__version__, title=title, question=question, criteria=criteria,
                         recall_target=recall_target, confidence=confidence, seed=seed).items():
            p.set_meta(k, v)
        p.log('created', {'app_version': __version__, 'seed': seed})
        return p

    def meta(self, key, default=None):
        r = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_meta(self, key, value):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def log(self, kind, detail=None):
        with self.lock:
            self.db.execute('INSERT INTO events (ts, kind, detail) VALUES (?,?,?)', (now(), kind, json.dumps(detail or {})))

    def criteria_text(self):
        return criteria_block(self.meta('title'), self.meta('question'), self.meta('criteria'))

    def update_criteria(self, title, question, criteria):
        if self.db.execute('SELECT COUNT(*) FROM batches WHERE status=200').fetchone()[0]:
            raise ProjectError('Records have already been scored with the current criteria. Start a new project to change them.')
        for k, v in dict(title=title, question=question, criteria=criteria).items():
            self.set_meta(k, v)
        self.log('criteria_updated')

    def is_ranked(self):
        return self.db.execute('SELECT COUNT(*) FROM ranking').fetchone()[0] > 0

    # ---------- import
    def import_records(self, recs, source):
        if self.is_ranked():
            raise ProjectError('The ranking is frozen; records cannot be added after screening has started.')
        with self.lock:
            seen_doi, seen_pmid, seen_title = {}, {}, {}
            for r in self.db.execute('SELECT rid, doi, pmid, title, year FROM records WHERE dup_of IS NULL'):
                if r['doi']: seen_doi[r['doi']] = r['rid']
                if r['pmid']: seen_pmid[r['pmid']] = r['rid']
                if _title_key(r['title']): seen_title[(_title_key(r['title']), r['year'] or '')] = r['rid']
            c = dict(read=len(recs), added=0, duplicates=0, no_abstract=0, non_english=0, no_title=0)
            ts = now()
            for r in recs:
                if not (r.get('title') or '').strip() and not (r.get('abstract') or '').strip():
                    c['no_title'] += 1; continue
                doi = norm_doi(r.get('doi')); tk = (_title_key(r.get('title')), r.get('year') or '')
                dup = seen_doi.get(doi) if doi else None
                dup = dup or (seen_pmid.get(r['pmid']) if r.get('pmid') else None)
                dup = dup or (seen_title.get(tk) if tk[0] else None)
                flag = None
                if dup is None:
                    if not (r.get('abstract') or '').strip():
                        flag = 'no_abstract'
                    elif looks_non_english(r):
                        flag = 'non_english'
                cur = self.db.execute('INSERT INTO records (source, source_id, title, abstract, authors, year, journal, doi, pmid, language, dup_of, flag, imported) '
                                      'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                      (source, r.get('source_id'), r.get('title'), r.get('abstract'), r.get('authors'), r.get('year'), r.get('journal'),
                                       doi, r.get('pmid'), r.get('language'), dup, flag, ts))
                if dup is not None:
                    c['duplicates'] += 1; continue
                rid = cur.lastrowid; c['added'] += 1
                if flag: c[flag] += 1
                if doi: seen_doi[doi] = rid
                if r.get('pmid'): seen_pmid[r['pmid']] = rid
                if tk[0]: seen_title[tk] = rid
            self.log('import', {'source': source, **c})
            return c

    def set_flag(self, rid, flag):
        """Moves a record to the manual queue (flag = 'user' or another manual flag) or back to the ranked set (flag = None)."""
        if self.is_ranked():
            raise ProjectError('The ranking is frozen.')
        if flag is not None and flag not in MANUAL_FLAGS:
            raise ProjectError(f'Unknown flag {flag}.')
        with self.lock:
            self.db.execute('UPDATE records SET flag=? WHERE rid=? AND dup_of IS NULL', (flag, rid))
            self.log('flag', {'rid': rid, 'flag': flag})

    def records(self, where='1=1', args=()):
        return [dict(r) for r in self.db.execute(f'SELECT * FROM records WHERE {where} ORDER BY rid', args)]

    # ---------- scoring
    def to_score(self):
        """Unique, unflagged records without a successful score, in a seeded random order (the input must not be sorted by
        relevance, and records from the same database export should not travel together)."""
        rows = self.db.execute('SELECT r.rid, r.title, r.abstract FROM records r LEFT JOIN scores s ON s.rid=r.rid AND s.p IS NOT NULL '
                               'WHERE r.dup_of IS NULL AND r.flag IS NULL AND s.rid IS NULL ORDER BY r.rid').fetchall()
        rows = [dict(r) for r in rows]
        random.Random(self.meta('seed')).shuffle(rows)
        return rows

    def save_batch(self, rids, request, result, endpoint, purpose='score', probs=None):
        with self.lock:
            cur = self.db.execute('INSERT INTO batches (ts, endpoint, purpose, rids, request, response, status, error, tokens, cost, attempts) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                  (now(), endpoint, purpose, json.dumps(rids), json.dumps(request), json.dumps(result.get('response')), result.get('status'),
                                   result.get('error'), result.get('tokens'), result.get('cost'), result.get('attempts')))
            bid = cur.lastrowid
            if purpose == 'score':
                for rid, p in zip(rids, probs or [None] * len(rids)):
                    err = None if p is not None else (result.get('error') or 'no answer from Jev (possible refusal)')
                    self.db.execute('INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?)', (rid, p, err, bid, now()))
            return bid

    def scoring_summary(self):
        q = lambda s: self.db.execute(s).fetchone()[0]
        return {
            'eligible': q('SELECT COUNT(*) FROM records WHERE dup_of IS NULL AND flag IS NULL'),
            'scored': q('SELECT COUNT(*) FROM scores s JOIN records r ON r.rid=s.rid WHERE s.p IS NOT NULL AND r.flag IS NULL AND r.dup_of IS NULL'),
            'failed': q('SELECT COUNT(*) FROM scores s JOIN records r ON r.rid=s.rid WHERE s.p IS NULL AND r.flag IS NULL AND r.dup_of IS NULL'),
            'requests': q("SELECT COUNT(*) FROM batches WHERE purpose='score'"),
            'cost_usd': q("SELECT COALESCE(SUM(cost),0) FROM batches"),
            'tokens': q("SELECT COALESCE(SUM(tokens),0) FROM batches"),
        }

    # ---------- ranking
    def freeze_ranking(self):
        """Fixes the screening order: Jev probability, highest first; ties broken by the project seed. Records whose scoring failed
        (including refusals) move to the manual queue. After this, records and criteria cannot change."""
        if self.is_ranked():
            raise ProjectError('The ranking is already frozen.')
        with self.lock:
            s = self.scoring_summary()
            unscored = s['eligible'] - s['scored'] - s['failed']
            if unscored:
                raise ProjectError(f'{unscored} records have not been scored yet.')
            if s['scored'] == 0:
                raise ProjectError('No scored records.')
            failed = [r[0] for r in self.db.execute('SELECT s.rid FROM scores s JOIN records r ON r.rid=s.rid WHERE s.p IS NULL AND r.flag IS NULL AND r.dup_of IS NULL')]
            for rid in failed:
                self.db.execute("UPDATE records SET flag='no_score' WHERE rid=?", (rid,))
            rows = self.db.execute('SELECT s.rid, s.p FROM scores s JOIN records r ON r.rid=s.rid WHERE s.p IS NOT NULL AND r.flag IS NULL AND r.dup_of IS NULL ORDER BY s.rid').fetchall()
            rng = random.Random(self.meta('seed') + 1)
            order = sorted(((-r['p'], rng.random(), r['rid']) for r in rows))
            self.db.executemany('INSERT INTO ranking VALUES (?,?)', [(rid, i + 1) for i, (_, _, rid) in enumerate(order)])
            self.set_meta('ranked_at', now())
            self.log('ranking_frozen', {'ranked': len(order), 'moved_to_manual_no_score': len(failed)})

    def n_ranked(self):
        return self.db.execute('SELECT COUNT(*) FROM ranking').fetchone()[0]

    # ---------- screening
    def next_record(self, queue='ranked'):
        if queue == 'audit':
            for rid in self.meta('audit_rids') or []:
                if not self.db.execute('SELECT 1 FROM decisions WHERE rid=?', (rid,)).fetchone():
                    r = self.db.execute('SELECT r.*, k.rank, s.p FROM records r JOIN ranking k ON k.rid=r.rid JOIN scores s ON s.rid=r.rid WHERE r.rid=?', (rid,)).fetchone()
                    return dict(r)
            return None
        if queue == 'ranked':
            r = self.db.execute('SELECT r.*, k.rank, s.p FROM ranking k JOIN records r ON r.rid=k.rid JOIN scores s ON s.rid=k.rid '
                                'LEFT JOIN decisions d ON d.rid=k.rid WHERE d.rid IS NULL ORDER BY k.rank LIMIT 1').fetchone()
        else:
            r = self.db.execute('SELECT r.*, NULL AS rank, NULL AS p FROM records r LEFT JOIN decisions d ON d.rid=r.rid '
                                'WHERE r.dup_of IS NULL AND r.flag IS NOT NULL AND d.rid IS NULL ORDER BY r.rid LIMIT 1').fetchone()
        return dict(r) if r else None

    def decide(self, rid, decision):
        if decision not in DECISIONS:
            raise ProjectError(f'Unknown decision {decision}.')
        if not self.is_ranked():
            raise ProjectError('Freeze the ranking before screening.')
        with self.lock:
            in_rank = self.db.execute('SELECT rank FROM ranking WHERE rid=?', (rid,)).fetchone()
            in_audit = rid in (self.meta('audit_rids') or []) and not self.db.execute('SELECT 1 FROM decisions WHERE rid=?', (rid,)).fetchone()
            if in_audit:
                queue = 'audit'
            elif in_rank:
                nxt = self.next_record('ranked')
                if not nxt or nxt['rid'] != rid:
                    raise ProjectError('Ranked records must be screened in ranked order (the stopping criterion depends on it).')
                if self.meta('stopped_at') is not None:
                    raise ProjectError('Screening of the ranked set has been stopped.')
                queue = 'ranked'
            else:
                r = self.db.execute('SELECT flag, dup_of FROM records WHERE rid=?', (rid,)).fetchone()
                if not r or r['dup_of'] is not None or r['flag'] is None:
                    raise ProjectError('Unknown record.')
                if self.db.execute('SELECT 1 FROM decisions WHERE rid=?', (rid,)).fetchone():
                    raise ProjectError('This record has already been screened.')
                queue = 'manual'
            seq = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM decisions').fetchone()[0]
            self.db.execute('INSERT INTO decisions VALUES (?,?,?,?,?)', (rid, decision, queue, seq, now()))
            return self.stop_status()

    def undo(self):
        with self.lock:
            r = self.db.execute('SELECT * FROM decisions ORDER BY seq DESC LIMIT 1').fetchone()
            if not r:
                return None
            if r['queue'] == 'ranked' and self.meta('stopped_at') is not None:
                raise ProjectError('Screening of the ranked set has been stopped.')
            self.db.execute('DELETE FROM decisions WHERE rid=?', (r['rid'],))
            self.log('undo', {'rid': r['rid'], 'decision': r['decision']})
            return dict(r)

    def ranked_labels(self):
        """Relevance (include or maybe = 1) of the screened ranked records, in rank order. 'Maybe' counts as relevant, which is the
        conservative choice for the stopping criterion."""
        rows = self.db.execute("SELECT d.decision FROM ranking k JOIN decisions d ON d.rid=k.rid WHERE d.queue='ranked' ORDER BY k.rank").fetchall()
        return [0 if r[0] == 'exclude' else 1 for r in rows]

    def stop_status(self):
        st = stopping.status(self.ranked_labels(), self.n_ranked(), self.meta('recall_target'), self.meta('confidence'))
        d = st.to_dict(); d['stopped_at'] = self.meta('stopped_at')
        d['manual_remaining'] = self.db.execute('SELECT COUNT(*) FROM records r LEFT JOIN decisions d ON d.rid=r.rid '
                                                'WHERE r.dup_of IS NULL AND r.flag IS NOT NULL AND d.rid IS NULL').fetchone()[0]
        return d

    # ---------- random-sample check after stopping
    def draw_audit(self, n, seed=None):
        """Draws a random sample of the ranked records left unscreened after stopping, for full screening (the paper's suggested
        safeguard). Decisions on the sample do not change the stopping statistics; relevant records found there are reported."""
        if self.meta('stopped_at') is None:
            raise ProjectError('Stop screening the ranked list first.')
        if self.meta('audit_rids'):
            raise ProjectError('A random sample has already been drawn.')
        with self.lock:
            pool = [r[0] for r in self.db.execute('SELECT k.rid FROM ranking k LEFT JOIN decisions d ON d.rid=k.rid WHERE d.rid IS NULL ORDER BY k.rank')]
            seed = seed if seed is not None else self.meta('seed') + 2
            pick = random.Random(seed).sample(pool, max(0, min(int(n), len(pool))))
            self.set_meta('audit_rids', pick); self.set_meta('audit_pool', len(pool)); self.set_meta('audit_seed', seed)
            self.log('audit_drawn', {'size': len(pick), 'pool': len(pool), 'seed': seed})
        return self.audit_summary()

    def audit_summary(self):
        rids = self.meta('audit_rids')
        if not rids:
            return None
        dec = {r[0]: r[1] for r in self.db.execute("SELECT rid, decision FROM decisions WHERE queue='audit'")}
        screened = sum(1 for r in rids if r in dec); relevant = sum(1 for r in rids if dec.get(r) in ('include', 'maybe'))
        return {'drawn': len(rids), 'pool': self.meta('audit_pool'), 'screened': screened, 'relevant': relevant, 'remaining': len(rids) - screened}

    def stop(self):
        with self.lock:
            st = self.stop_status()
            if not st['met']:
                raise ProjectError('The stopping criterion has not been met.')
            self.set_meta('stopped_at', st['screened'])
            self.log('stopped', st)
            return st

    # ---------- summaries
    def counts(self):
        q = lambda s, a=(): self.db.execute(s, a).fetchone()[0]
        flags = {f: q('SELECT COUNT(*) FROM records WHERE dup_of IS NULL AND flag=?', (f,)) for f in MANUAL_FLAGS}
        dec = {f'{queue}_{d}': q('SELECT COUNT(*) FROM decisions WHERE queue=? AND decision=?', (queue, d)) for queue in ('ranked', 'manual', 'audit') for d in DECISIONS}
        return {
            'imported': q('SELECT COUNT(*) FROM records'),
            'duplicates': q('SELECT COUNT(*) FROM records WHERE dup_of IS NOT NULL'),
            'unique': q('SELECT COUNT(*) FROM records WHERE dup_of IS NULL'),
            'manual_queue': sum(flags.values()), 'manual_by_reason': flags,
            'ranked': self.n_ranked(), **dec,
            'ranked_unscreened': self.n_ranked() - sum(v for k, v in dec.items() if k.startswith('ranked_')),
        }

    def events(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY id')]

    def close(self):
        self.db.close()
