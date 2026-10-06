"""Parses real PubMed output in both export formats (MEDLINE/.nbib and XML) for the same records. Needs network access to NCBI;
only a search term and PMIDs are sent. Run with: pytest -m network"""
import re

import httpx
import pytest

from reviewfast import records_io

BASE = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
TERM = 'statistical stopping criteria automated screening systematic reviews'


@pytest.mark.network
def test_medline_and_xml_agree():
    ids = httpx.get(BASE + 'esearch.fcgi', params={'db': 'pubmed', 'term': TERM, 'retmode': 'json', 'tool': 'reviewfast'}, timeout=60).json()['esearchresult']['idlist']
    assert ids
    get = lambda **kw: httpx.get(BASE + 'efetch.fcgi', params={'db': 'pubmed', 'id': ','.join(ids), 'tool': 'reviewfast', **kw}, timeout=60).content
    fx, xml = records_io.parse_file('x.xml', get(retmode='xml'))
    fm, med = records_io.parse_file('x.nbib', get(rettype='medline', retmode='text'))
    assert (fx, fm) == ('pubmed-xml', 'medline') and len(xml) == len(med) == len(ids)
    assert records_io.fetch_pubmed(ids)[0]['pmid'] in ids
    for a, b in zip(sorted(xml, key=lambda r: r['pmid']), sorted(med, key=lambda r: r['pmid'])):
        assert a['pmid'] == b['pmid'] and a['doi'] == b['doi'] and a['year'] == b['year']
        # PubMed's MEDLINE text renders some punctuation differently from its XML (a backtick becomes ';'), so compare letters and digits
        norm = lambda t: re.sub(r'[^a-z0-9]', '', t.lower())
        assert norm(a['title']) == norm(b['title']) and len(a['abstract']) > 200 and len(b['abstract']) > 200
        assert not records_io.looks_non_english(a)
