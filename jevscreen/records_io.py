"""Import of search results (RIS, CSV, PubMed MEDLINE/.nbib, PubMed XML, PMID lists) and export of decisions."""
import csv
import io
import re
import time
import xml.etree.ElementTree as ET

import httpx

FIELDS = ('source_id', 'title', 'abstract', 'authors', 'year', 'journal', 'doi', 'pmid', 'language')


def _rec(**kw):
    r = {k: '' for k in FIELDS}
    r.update({k: (v or '').strip() for k, v in kw.items() if k in FIELDS})
    return r


def _year(s):
    m = re.search(r'(1[5-9]|20)\d\d', s or '')
    return m.group(0) if m else ''


def norm_doi(s):
    s = (s or '').strip().lower()
    s = re.sub(r'^(https?://(dx\.)?doi\.org/|doi:\s*)', '', s)
    return s


# ---------- RIS
RIS_LINE = re.compile(r'^([A-Z][A-Z0-9])  -( (.*))?$')


def parse_ris(text):
    recs, cur, last = [], None, None
    for line in text.splitlines():
        m = RIS_LINE.match(line.rstrip('\r'))
        if not m:
            if cur is not None and last and line.strip():
                cur[last][-1] += ' ' + line.strip()
            continue
        tag, val = m.group(1), (m.group(3) or '').strip()
        if tag == 'TY':
            cur, last = {}, None; continue
        if cur is None:
            continue
        if tag == 'ER':
            recs.append(_from_ris(cur)); cur, last = None, None; continue
        cur.setdefault(tag, []).append(val); last = tag
    if cur:
        recs.append(_from_ris(cur))
    return recs


def _from_ris(t):
    g = lambda *tags: next((v[0] for k in tags for v in [t.get(k)] if v), '')
    authors = '; '.join(t.get('AU', []) or t.get('A1', []))
    pmid = ''
    for v in t.get('AN', []) + t.get('ID', []):
        if re.fullmatch(r'\d{4,9}', v.strip()):
            pmid = v.strip(); break
    return _rec(source_id=g('ID', 'AN', 'ID'), title=g('TI', 'T1', 'CT'), abstract=' '.join(t.get('AB', []) or t.get('N2', [])),
                authors=authors, year=_year(g('PY', 'Y1', 'DA')), journal=g('T2', 'JO', 'JF', 'JA', 'J2'), doi=norm_doi(g('DO')),
                pmid=pmid, language=g('LA'))


# ---------- PubMed MEDLINE (.nbib, "PubMed format" export)
NBIB_LINE = re.compile(r'^([A-Z]{2,4}) {0,3}- (.*)$')


def parse_nbib(text):
    recs, cur, last = [], {}, None
    for line in text.splitlines() + ['']:
        if not line.strip():
            if cur:
                recs.append(_from_nbib(cur)); cur, last = {}, None
            continue
        m = NBIB_LINE.match(line)
        if m:
            last = m.group(1); cur.setdefault(last, []).append(m.group(2).strip())
        elif last and line.startswith('      '):
            cur[last][-1] += ' ' + line.strip()
    return recs


def _from_nbib(t):
    g = lambda k: (t.get(k) or [''])[0]
    doi = ''
    for v in t.get('LID', []) + t.get('AID', []):
        if v.endswith('[doi]'):
            doi = v[:-5]; break
    return _rec(source_id=g('PMID'), title=g('TI'), abstract=g('AB'), authors='; '.join(t.get('FAU', []) or t.get('AU', [])),
                year=_year(g('DP')), journal=g('JT') or g('TA'), doi=norm_doi(doi), pmid=g('PMID'), language=g('LA'))


# ---------- PubMed XML (efetch retmode=xml, or "Save > XML" from the PubMed website)
def parse_pubmed_xml(text):
    root = ET.fromstring(text.encode() if isinstance(text, str) else text)
    recs = []
    for art in root.iter('PubmedArticle'):
        pmid = art.findtext('.//PMID') or ''
        t = art.find('.//ArticleTitle'); title = ''.join(t.itertext()) if t is not None else ''
        parts = []
        for a in art.findall('.//Abstract/AbstractText'):
            lab = a.get('Label'); txt = ''.join(a.itertext()).strip()
            parts.append(f'{lab}: {txt}' if lab else txt)
        year = art.findtext('.//PubDate/Year') or _year(art.findtext('.//PubDate/MedlineDate'))
        doi = next(((a.text or '') for a in art.findall('.//ArticleId') if a.get('IdType') == 'doi'), '')
        authors = '; '.join(f"{a.findtext('LastName') or ''}, {a.findtext('ForeName') or ''}".strip(', ')
                            for a in art.findall('.//AuthorList/Author') if a.findtext('LastName') or a.findtext('CollectiveName'))
        lang = ' '.join(l.text or '' for l in art.findall('.//Language'))
        recs.append(_rec(source_id=pmid, title=title, abstract=' '.join(parts), authors=authors, year=year,
                         journal=art.findtext('.//Journal/Title') or '', doi=norm_doi(doi), pmid=pmid, language=lang))
    return recs


# ---------- CSV (header names matched case-insensitively; common exports from Covidence, Rayyan, Zotero, ASReview)
CSV_ALIASES = {
    'title': ['title', 'primary title', 'article title', 'ti'],
    'abstract': ['abstract', 'abstract note', 'ab', 'notes abstract'],
    'authors': ['authors', 'author', 'au'],
    'year': ['year', 'publication year', 'py', 'date'],
    'journal': ['journal', 'publication title', 'source', 'secondary title', 'journal/book'],
    'doi': ['doi'],
    'pmid': ['pmid', 'pubmed id', 'pubmed_id'],
    'source_id': ['id', 'record id', 'record_id', 'key', 'accession number', 'covidence #'],
    'language': ['language', 'la'],
}


def parse_csv(text):
    text = text.lstrip('﻿')
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        return []
    cols = {c.strip().lower(): c for c in rows[0].keys() if c}
    pick = {f: next((cols[a] for a in al if a in cols), None) for f, al in CSV_ALIASES.items()}
    if not pick['title']:
        raise ValueError('The CSV file has no title column (expected a header such as "title").')
    out = []
    for r in rows:
        v = {f: (r.get(c) or '') if c else '' for f, c in pick.items()}
        v['year'] = _year(v['year']); v['doi'] = norm_doi(v['doi'])
        out.append(_rec(**v))
    return out


def parse_file(name, data):
    """Detects the format from the file name and content. data: bytes."""
    text = data.decode('utf-8-sig', errors='replace')
    low = name.lower(); head = text.lstrip()[:2000]
    if low.endswith('.xml') or head.startswith('<?xml') or '<PubmedArticleSet' in head:
        return 'pubmed-xml', parse_pubmed_xml(text)
    if low.endswith(('.nbib', '.medline')) or re.search(r'^PMID- ', head, re.M):
        return 'medline', parse_nbib(text)
    if low.endswith(('.ris', '.txt')) or re.search(r'^TY  - ', head, re.M):
        return 'ris', parse_ris(text)
    if low.endswith(('.csv', '.tsv')):
        return 'csv', parse_csv(text)
    raise ValueError('Unrecognised file format. Use RIS, CSV, PubMed format (.nbib) or PubMed XML.')


def fetch_pubmed(pmids, email=None, pause=0.4):
    """Fetches titles and abstracts for PMIDs from NCBI E-utilities (only the PMIDs are sent)."""
    base = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi'
    out = []
    for i in range(0, len(pmids), 200):
        q = {'db': 'pubmed', 'id': ','.join(pmids[i:i + 200]), 'retmode': 'xml', 'tool': 'jev-screen'}
        if email:
            q['email'] = email
        err = None
        for a in range(5):
            try:
                r = httpx.post(base, data=q, timeout=120); r.raise_for_status()
                out += parse_pubmed_xml(r.content); break
            except Exception as e:     # noqa: BLE001 - retried, then reported
                err = e; time.sleep(2 * (a + 1))
        else:
            raise RuntimeError(f'PubMed request failed: {err}')
        time.sleep(pause)
    return out


# ---------- language flag
_EN = set('the of and in to a with for was were is are that by on from as this we at be or an which these than between not'.split())


def looks_non_english(rec):
    """True when the declared language is not English, or the title and abstract do not read as English.
    A screening aid only: flagged records go to the manual queue, and the user can move them back."""
    lang = (rec.get('language') or '').lower()
    if lang and not re.search(r'\b(en|eng|english)\b', lang):
        return True
    text = f"{rec.get('title', '')} {rec.get('abstract', '')}"
    letters = [c for c in text if c.isalpha()]
    if letters and sum(ord(c) > 0x24F for c in letters) / len(letters) > 0.2:     # mostly non-Latin script
        return True
    words = re.findall(r"[a-zA-ZÀ-ɏ]+", text.lower())
    return len(words) >= 40 and sum(w in _EN for w in words) / len(words) < 0.08


# ---------- export
def to_ris(recs):
    out = []
    for r in recs:
        out.append('TY  - JOUR')
        for tag, key in [('TI', 'title'), ('AB', 'abstract'), ('PY', 'year'), ('JO', 'journal'), ('DO', 'doi'), ('AN', 'pmid'), ('LA', 'language')]:
            if r.get(key):
                out.append(f'{tag}  - {r[key]}')
        for a in [a.strip() for a in (r.get('authors') or '').split(';') if a.strip()]:
            out.append(f'AU  - {a}')
        if r.get('note'):
            out.append(f"N1  - {r['note']}")
        out.append('ER  - '); out.append('')
    return '\n'.join(out)


def to_csv(rows, columns):
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=columns, extrasaction='ignore'); w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()
