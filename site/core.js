/* ReviewFast web: core logic (no DOM). Parsers, the Jev request format of the accompanying paper, the statistical stopping
   criterion of Callaghan and Mueller-Hansen (2020), seeded randomness and exports. Loaded by the page and by the Node tests.
   Ported from the Python package (reviewfast/records_io.py, jev_client.py, stopping.py, report.py). */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RF = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const VERSION = '0.2.0';
  const GATEWAY_URL = 'https://ai-gateway.vercel.sh/typesafe/v1/systemone';
  const MODEL = 'typesafe-ai/jev';
  const BATCH_SIZE = 10;
  const QUESTION = 'Based on the title and abstract, should this record be advanced to full-text screening for this review? ' +
    'Give the probability that it meets the eligibility criteria.';
  const BATCH_INTRO = 'The following records are independent candidates retrieved by the search; judge each one on its own.';
  const USD_PER_M_INPUT_TOKENS = 0.042;
  const TOKENS_PER_RECORD = 455;
  const RECALL_TARGET = 0.95, CONFIDENCE = 0.95;
  const FIELDS = ['source_id', 'title', 'abstract', 'authors', 'year', 'journal', 'doi', 'pmid', 'language'];

  // ---------- records
  function rec(kw) {
    const r = {}; FIELDS.forEach((k) => { r[k] = ((kw[k] == null ? '' : String(kw[k]))).trim(); }); return r;
  }
  function year(s) { const m = /(1[5-9]|20)\d\d/.exec(s || ''); return m ? m[0] : ''; }
  function normDoi(s) {
    return (s || '').trim().toLowerCase().replace(/^(https?:\/\/(dx\.)?doi\.org\/|doi:\s*)/, '');
  }

  // RIS
  const RIS_LINE = /^([A-Z][A-Z0-9])  -( (.*))?$/;
  function parseRis(text) {
    const recs = []; let cur = null, last = null;
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.replace(/\r$/, ''); const m = RIS_LINE.exec(line);
      if (!m) { if (cur && last && line.trim()) { const a = cur[last]; a[a.length - 1] += ' ' + line.trim(); } continue; }
      const tag = m[1], val = (m[3] || '').trim();
      if (tag === 'TY') { cur = {}; last = null; continue; }
      if (!cur) continue;
      if (tag === 'ER') { recs.push(fromRis(cur)); cur = null; last = null; continue; }
      (cur[tag] = cur[tag] || []).push(val); last = tag;
    }
    if (cur) recs.push(fromRis(cur));
    return recs;
  }
  function fromRis(t) {
    const g = (...tags) => { for (const k of tags) if (t[k] && t[k].length) return t[k][0]; return ''; };
    let pmid = '';
    for (const v of [...(t.AN || []), ...(t.ID || [])]) if (/^\d{4,9}$/.test(v.trim())) { pmid = v.trim(); break; }
    return rec({ source_id: g('ID', 'AN'), title: g('TI', 'T1', 'CT'), abstract: (t.AB || t.N2 || []).join(' '),
      authors: (t.AU || t.A1 || []).join('; '), year: year(g('PY', 'Y1', 'DA')), journal: g('T2', 'JO', 'JF', 'JA', 'J2'),
      doi: normDoi(g('DO')), pmid, language: g('LA') });
  }

  // PubMed MEDLINE (.nbib)
  const NBIB_LINE = /^([A-Z]{2,4}) {0,3}- (.*)$/;
  function parseNbib(text) {
    const recs = []; let cur = {}, last = null;
    for (const line of [...text.split(/\r?\n/), '']) {
      if (!line.trim()) { if (Object.keys(cur).length) { recs.push(fromNbib(cur)); cur = {}; last = null; } continue; }
      const m = NBIB_LINE.exec(line);
      if (m) { last = m[1]; (cur[last] = cur[last] || []).push(m[2].trim()); }
      else if (last && line.startsWith('      ')) { const a = cur[last]; a[a.length - 1] += ' ' + line.trim(); }
    }
    return recs;
  }
  function fromNbib(t) {
    const g = (k) => (t[k] || [''])[0];
    let doi = '';
    for (const v of [...(t.LID || []), ...(t.AID || [])]) if (v.endsWith('[doi]')) { doi = v.slice(0, -5); break; }
    return rec({ source_id: g('PMID'), title: g('TI'), abstract: g('AB'), authors: (t.FAU || t.AU || []).join('; '),
      year: year(g('DP')), journal: g('JT') || g('TA'), doi: normDoi(doi), pmid: g('PMID'), language: g('LA') });
  }

  // PubMed XML (needs a DOMParser: the browser, or an injected one in tests)
  function parsePubmedXml(text, DOMParserImpl) {
    const P = DOMParserImpl || (typeof DOMParser !== 'undefined' ? DOMParser : null);
    if (!P) throw new Error('PubMed XML needs a browser.');
    const doc = new P().parseFromString(text, 'text/xml');
    const out = [];
    const all = (el, sel) => Array.from(el.getElementsByTagName(sel));
    const txt = (el) => (el ? el.textContent || '' : '');
    for (const art of all(doc, 'PubmedArticle')) {
      const pmid = txt(all(art, 'PMID')[0]);
      const parts = all(art, 'AbstractText').map((a) => { const lab = a.getAttribute('Label'); const t = txt(a).trim(); return lab ? `${lab}: ${t}` : t; });
      const pd = all(art, 'PubDate')[0];
      const yr = pd ? (txt(all(pd, 'Year')[0]) || year(txt(all(pd, 'MedlineDate')[0]))) : '';
      const doiEl = all(art, 'ArticleId').find((a) => a.getAttribute('IdType') === 'doi');
      const authors = all(art, 'Author').filter((a) => all(a, 'LastName')[0] || all(a, 'CollectiveName')[0])
        .map((a) => `${txt(all(a, 'LastName')[0])}, ${txt(all(a, 'ForeName')[0])}`.replace(/^, |, $/g, '')).join('; ');
      const jt = all(art, 'Journal')[0];
      out.push(rec({ source_id: pmid, title: txt(all(art, 'ArticleTitle')[0]), abstract: parts.join(' '), authors, year: yr,
        journal: jt ? txt(all(jt, 'Title')[0]) : '', doi: normDoi(txt(doiEl)), pmid,
        language: all(art, 'Language').map(txt).join(' ') }));
    }
    return out;
  }

  // CSV
  const CSV_ALIASES = {
    title: ['title', 'primary title', 'article title', 'ti'], abstract: ['abstract', 'abstract note', 'ab', 'notes abstract'],
    authors: ['authors', 'author', 'au'], year: ['year', 'publication year', 'py', 'date'],
    journal: ['journal', 'publication title', 'source', 'secondary title', 'journal/book'], doi: ['doi'],
    pmid: ['pmid', 'pubmed id', 'pubmed_id'], source_id: ['id', 'record id', 'record_id', 'key', 'accession number', 'covidence #'],
    language: ['language', 'la'],
  };
  function csvRows(text, delim) {
    const rows = []; let row = [], f = '', q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) {
        if (c === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += c;
      } else if (c === '"') q = true;
      else if (c === delim) { row.push(f); f = ''; }
      else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(f); rows.push(row); row = []; f = ''; }
      else f += c;
    }
    if (f !== '' || row.length) { row.push(f); rows.push(row); }
    return rows.filter((r) => r.some((x) => x !== ''));
  }
  function parseCsv(text) {
    text = text.replace(/^﻿/, '');
    const head = text.split(/\r?\n/, 1)[0] || '';
    const delim = [',', ';', '\t'].map((d) => [d, head.split(d).length]).sort((a, b) => b[1] - a[1])[0][0];
    const rows = csvRows(text, delim);
    if (rows.length < 2) return [];
    const cols = {}; rows[0].forEach((c, i) => { if (c) cols[c.trim().toLowerCase()] = i; });
    const pick = {}; for (const [k, al] of Object.entries(CSV_ALIASES)) { const a = al.find((x) => x in cols); pick[k] = a === undefined ? null : cols[a]; }
    if (pick.title === null) throw new Error('The CSV file has no title column (expected a header such as "title").');
    return rows.slice(1).map((r) => {
      const v = {}; for (const k of Object.keys(pick)) v[k] = pick[k] === null ? '' : (r[pick[k]] || '');
      v.year = year(v.year); v.doi = normDoi(v.doi); return rec(v);
    });
  }

  function parseFile(name, text, DOMParserImpl) {
    text = text.replace(/^﻿/, '');
    const low = name.toLowerCase(), head = text.trimStart().slice(0, 2000);
    if (low.endsWith('.xml') || head.startsWith('<?xml') || head.includes('<PubmedArticleSet')) return ['pubmed-xml', parsePubmedXml(text, DOMParserImpl)];
    if (low.endsWith('.nbib') || low.endsWith('.medline') || /^PMID- /m.test(head)) return ['medline', parseNbib(text)];
    if (low.endsWith('.ris') || low.endsWith('.txt') || /^TY  - /m.test(head)) return ['ris', parseRis(text)];
    if (low.endsWith('.csv') || low.endsWith('.tsv')) return ['csv', parseCsv(text)];
    throw new Error('Unrecognised file format. Use RIS, CSV, PubMed format (.nbib) or PubMed XML.');
  }

  const EN = new Set('the of and in to a with for was were is are that by on from as this we at be or an which these than between not'.split(' '));
  function looksNonEnglish(r) {
    const lang = (r.language || '').toLowerCase();
    if (lang && !/\b(en|eng|english)\b/.test(lang)) return true;
    const text = `${r.title || ''} ${r.abstract || ''}`;
    const letters = Array.from(text).filter((c) => /\p{L}/u.test(c));
    if (letters.length && letters.filter((c) => c.codePointAt(0) > 0x24F).length / letters.length > 0.2) return true;
    const words = text.toLowerCase().match(/[a-zÀ-ɏ]+/g) || [];
    return words.length >= 40 && words.filter((w) => EN.has(w)).length / words.length < 0.08;
  }
  const titleKey = (t) => (t || '').toLowerCase().replace(/[^a-z0-9]/g, '');

  /* Adds records to a project with duplicate removal and manual-queue flags. Returns counts. */
  function importRecords(project, recs, source) {
    if (project.ranking) throw new Error('The ranking is frozen; records cannot be added after screening has started.');
    const seenDoi = {}, seenPmid = {}, seenTitle = {};
    for (const r of project.records) if (r.dup_of == null) {
      if (r.doi) seenDoi[r.doi] = r.rid; if (r.pmid) seenPmid[r.pmid] = r.rid;
      if (titleKey(r.title)) seenTitle[titleKey(r.title) + '|' + (r.year || '')] = r.rid;
    }
    const c = { read: recs.length, added: 0, duplicates: 0, no_abstract: 0, non_english: 0, no_title: 0 };
    for (const x of recs) {
      if (!(x.title || '').trim() && !(x.abstract || '').trim()) { c.no_title++; continue; }
      const doi = normDoi(x.doi), tk = titleKey(x.title) + '|' + (x.year || '');
      let dup = (doi && seenDoi[doi]) || (x.pmid && seenPmid[x.pmid]) || (titleKey(x.title) && seenTitle[tk]) || null;
      let flag = null;
      if (dup == null) { if (!(x.abstract || '').trim()) flag = 'no_abstract'; else if (looksNonEnglish(x)) flag = 'non_english'; }
      const rid = project.records.length + 1;
      project.records.push({ ...rec(x), doi, rid, source, dup_of: dup, flag });
      if (dup != null) { c.duplicates++; continue; }
      c.added++; if (flag) c[flag]++;
      if (doi) seenDoi[doi] = rid; if (x.pmid) seenPmid[x.pmid] = rid; if (titleKey(x.title)) seenTitle[tk] = rid;
    }
    logEvent(project, 'import', { source, ...c });
    return c;
  }

  // ---------- Jev request format (identical to the paper's scorer)
  function criteriaBlock(title, question, criteria) {
    return `Systematic review title: ${title.trim()}\nResearch question: ${question.trim()}\nEligibility criteria reported by the review authors:\n${criteria.trim()}`;
  }
  function recordText(r) {
    return `Title: ${r.title || '(no title)'}\nAbstract: ${r.abstract || '(no abstract available; judge from the title)'}`;
  }
  function buildRequest(crit, recs) {
    let state, questions = {};
    if (recs.length === 1) {
      state = crit + '\n\n' + recordText(recs[0]); questions.r1 = { type: 'noul', instructions: QUESTION };
    } else {
      state = crit + '\n\n' + BATCH_INTRO + '\n\n' + recs.map((d, i) => `[Record R${i + 1}]\n${recordText(d)}`).join('\n\n');
      recs.forEach((_, i) => { questions[`r${i + 1}`] = { type: 'noul', instructions: `Consider only Record R${i + 1}. ${QUESTION}` }; });
    }
    return { model: MODEL, state, questions };
  }
  function parseAnswers(resp, n) {
    const out = [];
    for (let i = 0; i < n; i++) {
      let v = ((resp && resp.answers) || {})[`r${i + 1}`];
      v = v && typeof v === 'object' ? v.noul : null;
      const f = typeof v === 'number' ? v : (typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN);
      out.push(Number.isFinite(f) && f >= 0 && f <= 1 ? f : null);
    }
    return out;
  }
  const estimateCost = (n) => n * TOKENS_PER_RECORD * USD_PER_M_INPUT_TOKENS / 1e6;

  // ---------- seeded randomness (mulberry32)
  function rng(seed) {
    let a = seed >>> 0;
    return function () { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }
  function shuffle(arr, seed) { const r = rng(seed); const a = arr.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; }

  // ---------- statistical stopping criterion (ranked quasi-sampling variant, equivalent to buscarpy.calculate_h0)
  let LF = new Float64Array([0, 0]);
  function lnFactTable(n) {
    if (LF.length < n + 2) { const t = new Float64Array(n + 2); t[0] = 0; for (let m = 1; m < n + 2; m++) t[m] = t[m - 1] + Math.log(m); LF = t; }
    return LF;
  }
  function lnC(a, b) { return (b < 0 || b > a || a < 0) ? -Infinity : LF[a] - LF[b] - LF[a - b]; }
  /* labels: 0/1 relevance of the screened records in ranked order; N: size of the ranked set. Returns the smallest p over all
     look-back windows; windows where H0 is impossible count as p = 0. */
  function h0Pvalue(labels, N, target = RECALL_TARGET) {
    const t = labels.length; if (t === 0) return 1;
    let rSeen = 0; for (const x of labels) rSeen += x ? 1 : 0;
    const D = N - t;
    const c = Math.floor(rSeen / target + 1) - rSeen;
    if (D <= 0) return 0;
    lnFactTable(N);
    let pmin = 1, cum = 0;
    for (let i = 1; i <= t; i++) {
      cum += labels[t - i] ? 1 : 0;
      const M = D + i, K = cum + c;
      if (K > M) { pmin = 0; continue; }
      const lnCMD = lnC(M, D); let S = 0;
      for (let x = 0; x < Math.min(c, D + 1); x++) S += Math.exp(lnC(K, x) + lnC(M - K, D - x) - lnCMD);
      const p = Math.min(1, Math.max(0, 1 - S));
      if (p < pmin) pmin = p;
    }
    return pmin;
  }
  function stopStatus(labels, N, target = RECALL_TARGET, confidence = CONFIDENCE) {
    const p = h0Pvalue(labels, N, target);
    return { screened: labels.length, ranked_total: N, relevant_seen: labels.reduce((s, x) => s + (x ? 1 : 0), 0), p_value: p,
      recall_target: target, confidence, met: labels.length > 0 && p < 1 - confidence };
  }

  // ---------- project model
  function newProject(title, question, criteria, seed) {
    const s = seed != null ? seed : Math.floor(Math.random() * 2147483646) + 1;
    const p = { id: 'p' + Date.now().toString(36) + Math.floor(Math.random() * 1e6).toString(36), app: 'ReviewFast web', app_version: VERSION,
      created: new Date().toISOString(), title, question, criteria, recall_target: RECALL_TARGET, confidence: CONFIDENCE, seed: s,
      records: [], batches: [], scores: {}, ranking: null, decisions: [], stopped_at: null, events: [] };
    logEvent(p, 'created', { seed: s, app_version: VERSION });
    return p;
  }
  function logEvent(p, kind, detail) { p.events.push({ ts: new Date().toISOString(), kind, detail: detail || {} }); }
  const unique = (p) => p.records.filter((r) => r.dup_of == null);
  function toScore(p) {
    const rows = unique(p).filter((r) => !r.flag && !(p.scores[r.rid] && p.scores[r.rid].p != null));
    return shuffle(rows, p.seed);
  }
  function scoringSummary(p) {
    const el = unique(p).filter((r) => !r.flag);
    let scored = 0, failed = 0; for (const r of el) { const s = p.scores[r.rid]; if (s) { if (s.p != null) scored++; else failed++; } }
    let cost = 0, tokens = 0; for (const b of p.batches) { cost += b.cost || 0; tokens += b.tokens || 0; }
    return { eligible: el.length, scored, failed, requests: p.batches.filter((b) => b.purpose === 'score').length, cost_usd: cost, tokens };
  }
  function freezeRanking(p) {
    if (p.ranking) throw new Error('The ranking is already frozen.');
    const s = scoringSummary(p); const unscored = s.eligible - s.scored - s.failed;
    if (unscored) throw new Error(`${unscored} records have not been scored yet.`);
    if (!s.scored) throw new Error('No scored records.');
    let moved = 0;
    for (const r of unique(p)) if (!r.flag && p.scores[r.rid] && p.scores[r.rid].p == null) { r.flag = 'no_score'; moved++; }
    const r2 = rng(p.seed + 1);
    const rows = unique(p).filter((r) => !r.flag && p.scores[r.rid] && p.scores[r.rid].p != null).sort((a, b) => a.rid - b.rid)
      .map((r) => [-p.scores[r.rid].p, r2(), r.rid]);
    rows.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
    p.ranking = rows.map((x) => x[2]); p.ranked_at = new Date().toISOString();
    logEvent(p, 'ranking_frozen', { ranked: p.ranking.length, moved_to_manual_no_score: moved });
  }
  const byRid = (p) => { const m = {}; for (const r of p.records) m[r.rid] = r; return m; };
  function decided(p) { const s = new Set(); for (const d of p.decisions) s.add(d.rid); return s; }
  function nextRecord(p, queue = 'ranked') {
    const done = decided(p), m = byRid(p);
    if (queue === 'ranked') { if (!p.ranking) return null; for (let i = 0; i < p.ranking.length; i++) if (!done.has(p.ranking[i])) return { ...m[p.ranking[i]], rank: i + 1, p: p.scores[p.ranking[i]].p }; return null; }
    for (const r of unique(p)) if (r.flag && !done.has(r.rid)) return { ...r, rank: null, p: null };
    return null;
  }
  function rankedLabels(p) {
    const pos = {}; (p.ranking || []).forEach((rid, i) => { pos[rid] = i; });
    return p.decisions.filter((d) => d.queue === 'ranked').sort((a, b) => pos[a.rid] - pos[b.rid]).map((d) => (d.decision === 'exclude' ? 0 : 1));
  }
  function projectStop(p) {
    const st = stopStatus(rankedLabels(p), (p.ranking || []).length, p.recall_target, p.confidence);
    const done = decided(p);
    st.stopped_at = p.stopped_at; st.manual_remaining = unique(p).filter((r) => r.flag && !done.has(r.rid)).length;
    return st;
  }
  function decide(p, rid, decision) {
    if (!['include', 'exclude', 'maybe'].includes(decision)) throw new Error('Unknown decision.');
    if (!p.ranking) throw new Error('Freeze the ranking before screening.');
    const inRank = p.ranking.includes(rid); let queue;
    if (inRank) {
      const nxt = nextRecord(p, 'ranked');
      if (!nxt || nxt.rid !== rid) throw new Error('Ranked records must be screened in ranked order.');
      if (p.stopped_at != null) throw new Error('Screening of the ranked set has been stopped.');
      queue = 'ranked';
    } else {
      const r = byRid(p)[rid];
      if (!r || r.dup_of != null || !r.flag) throw new Error('Unknown record.');
      if (decided(p).has(rid)) throw new Error('This record has already been screened.');
      queue = 'manual';
    }
    p.decisions.push({ rid, decision, queue, seq: p.decisions.length + 1, ts: new Date().toISOString() });
    return projectStop(p);
  }
  function undo(p) {
    const last = p.decisions[p.decisions.length - 1]; if (!last) return null;
    if (last.queue === 'ranked' && p.stopped_at != null) throw new Error('Screening of the ranked set has been stopped.');
    p.decisions.pop(); logEvent(p, 'undo', { rid: last.rid, decision: last.decision }); return last;
  }
  function stop(p) {
    const st = projectStop(p); if (!st.met) throw new Error('The stopping criterion has not been met.');
    p.stopped_at = st.screened; logEvent(p, 'stopped', st); return st;
  }
  function counts(p) {
    const u = unique(p), flags = { no_abstract: 0, non_english: 0, no_score: 0, user: 0 };
    for (const r of u) if (r.flag) flags[r.flag] = (flags[r.flag] || 0) + 1;
    const dec = {}; for (const q of ['ranked', 'manual']) for (const d of ['include', 'exclude', 'maybe']) dec[`${q}_${d}`] = p.decisions.filter((x) => x.queue === q && x.decision === d).length;
    const nr = (p.ranking || []).length, sr = dec.ranked_include + dec.ranked_exclude + dec.ranked_maybe;
    return { imported: p.records.length, duplicates: p.records.length - u.length, unique: u.length, manual_queue: Object.values(flags).reduce((a, b) => a + b, 0),
      manual_by_reason: flags, ranked: nr, ...dec, ranked_unscreened: nr - sr };
  }
  function prisma(p) {
    const c = counts(p), st = projectStop(p);
    const sr = c.ranked_include + c.ranked_exclude + c.ranked_maybe, sm = c.manual_include + c.manual_exclude + c.manual_maybe;
    return { records_identified: c.imported, duplicates_removed: c.duplicates, records_after_deduplication: c.unique,
      records_ranked: c.ranked, records_for_manual_screening: c.manual_queue, manual_screening_reasons: c.manual_by_reason,
      records_screened: sr + sm, records_screened_ranked: sr, records_screened_manual: sm,
      records_not_screened_after_stopping: st.stopped_at != null ? c.ranked_unscreened : 0,
      records_not_yet_screened: (st.stopped_at != null ? 0 : c.ranked_unscreened) + st.manual_remaining,
      records_excluded: c.ranked_exclude + c.manual_exclude,
      records_sought_for_retrieval: c.ranked_include + c.ranked_maybe + c.manual_include + c.manual_maybe, stopping: st };
  }

  // ---------- exports
  const n_ = (k, noun) => `${k} ${noun}${k === 1 ? '' : 's'}`;
  function methodsText(p) {
    const P = prisma(p), st = P.stopping, s = scoringSummary(p);
    const ts = p.batches.filter((b) => b.purpose === 'score' && b.status === 200).map((b) => b.ts.slice(0, 10)).sort();
    const first = ts[0] || '', last = ts[ts.length - 1] || '';
    const when = first === last ? first : `between ${first} and ${last}`;
    const parts = [
      `We screened titles and abstracts with ReviewFast (web version ${VERSION}). Of ${n_(P.records_identified, 'record')} retrieved, ` +
      (P.duplicates_removed ? `we removed ${n_(P.duplicates_removed, 'duplicate')} (matched on DOI, PubMed identifier, or normalised title and year), leaving ${n_(P.records_after_deduplication, 'unique record')}.`
        : 'we found no duplicates (matched on DOI, PubMed identifier, or normalised title and year).'),
      `We scored ${n_(P.records_ranked, 'record')} with a zero-shot LLM classifier, Jev (model alias ${MODEL}, accessed through the Vercel AI Gateway ${when}; the provider does not report a model version). ` +
      `Each request contained the review title, research question and eligibility criteria from our protocol and up to ${BATCH_SIZE} records in random order, and asked for each record: "${QUESTION}"`,
      'We screened the ranked records in descending order of the classifier probability, with the probabilities hidden from the screener. We counted records marked as possibly relevant as relevant for the stopping criterion.',
    ];
    if (st.stopped_at != null) {
      parts.push(`We stopped screening the ranked records when the statistical stopping criterion of Callaghan and Müller-Hansen (ranked quasi-sampling variant) rejected the hypothesis that recall was below ${p.recall_target.toFixed(2)} at the ${(1 - p.confidence).toFixed(2)} level ` +
        `(p = ${Number(st.p_value.toPrecision(3))}), after screening ${st.stopped_at} of ${st.ranked_total} ranked records (${(100 * st.stopped_at / st.ranked_total).toFixed(1)}%). We did not screen the remaining ${n_(P.records_not_screened_after_stopping, 'record')}.`);
    } else if (P.records_not_yet_screened === 0) parts.push(`We screened all ${n_(P.records_ranked, 'ranked record')}.`);
    else parts.push(`At the time of this report, ${n_(P.records_not_yet_screened, 'record')} had not yet been screened and the stopping criterion had not been applied.`);
    const R = P.manual_screening_reasons;
    if (P.records_for_manual_screening) {
      const why = [R.no_abstract ? `${R.no_abstract} without an abstract` : '', R.non_english ? `${R.non_english} flagged as not in English` : '',
        R.no_score ? `${R.no_score} without a usable classifier score` : '', R.user ? `${R.user} set aside by the reviewers` : ''].filter(Boolean);
      parts.push(`We screened ${n_(P.records_for_manual_screening, 'record')} in full outside the ranking (${why.join('; ')}).`);
    }
    parts.push(`The classifier requests used ${s.tokens} input tokens and cost US$${s.cost_usd.toFixed(2)}. We have archived every request, response and score so that the ranking can be audited.`);
    return parts.join(' ') + '\n\nReference: Callaghan MW, Müller-Hansen F. Statistical stopping criteria for automated screening in systematic reviews. Syst Rev. 2020;9:273.\n';
  }
  function csvEsc(v) { const s = v == null ? '' : String(v); return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
  const DECISION_COLUMNS = ['rid', 'source_id', 'pmid', 'doi', 'title', 'year', 'journal', 'flag', 'dup_of', 'rank', 'p', 'decision', 'queue', 'seq', 'ts'];
  function decisionsCsv(p) {
    const pos = {}; (p.ranking || []).forEach((rid, i) => { pos[rid] = i + 1; });
    const dec = {}; for (const d of p.decisions) dec[d.rid] = d;
    const lines = [DECISION_COLUMNS.join(',')];
    for (const r of p.records) {
      const d = dec[r.rid] || {}, s = p.scores[r.rid] || {};
      const row = { ...r, rank: pos[r.rid] || '', p: s.p == null ? '' : s.p, decision: d.decision || '', queue: d.queue || '', seq: d.seq || '', ts: d.ts || '' };
      lines.push(DECISION_COLUMNS.map((k) => csvEsc(row[k])).join(','));
    }
    return lines.join('\n') + '\n';
  }
  function toRis(recs) {
    const out = [];
    for (const r of recs) {
      out.push('TY  - JOUR');
      for (const [tag, key] of [['TI', 'title'], ['AB', 'abstract'], ['PY', 'year'], ['JO', 'journal'], ['DO', 'doi'], ['AN', 'pmid'], ['LA', 'language']]) if (r[key]) out.push(`${tag}  - ${r[key]}`);
      for (const a of (r.authors || '').split(';').map((x) => x.trim()).filter(Boolean)) out.push(`AU  - ${a}`);
      if (r.note) out.push(`N1  - ${r.note}`);
      out.push('ER  - '); out.push('');
    }
    return out.join('\n');
  }
  function includedRis(p) {
    const m = byRid(p);
    return toRis(p.decisions.filter((d) => d.decision !== 'exclude').map((d) => ({ ...m[d.rid], note: `ReviewFast decision: ${d.decision}` })));
  }

  return { VERSION, GATEWAY_URL, MODEL, BATCH_SIZE, QUESTION, RECALL_TARGET, CONFIDENCE, parseRis, parseNbib, parsePubmedXml, parseCsv,
    parseFile, looksNonEnglish, importRecords, criteriaBlock, buildRequest, parseAnswers, estimateCost, rng, shuffle, h0Pvalue, stopStatus,
    newProject, logEvent, toScore, scoringSummary, freezeRanking, nextRecord, rankedLabels, projectStop, decide, undo, stop, counts,
    prisma, methodsText, decisionsCsv, includedRis, toRis, normDoi };
});
