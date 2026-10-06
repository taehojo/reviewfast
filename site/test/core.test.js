// Node tests for site/core.js: run with `node site/test/core.test.js` (no dependencies).
// Compares the JavaScript core with the Python package (reference.json, written by make_reference.py) and with buscarpy 0.0.2.
'use strict';
const assert = require('assert');
const path = require('path');
const fs = require('fs');
const RF = require('../core.js');

const ROOT = path.join(__dirname, '..', '..');
const ref = JSON.parse(fs.readFileSync(path.join(__dirname, 'reference.json'), 'utf8'));
const stopRef = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests', 'fixtures', 'stopping_reference.json'), 'utf8'));
const theobald = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests', 'fixtures', 'theobald_2021.json'), 'utf8'));
let n = 0;
function test(name, fn) { fn(); n++; console.log('ok', name); }

test('request bodies equal the Python package (and so the paper scorer)', () => {
  const block = RF.criteriaBlock(...ref.criteria);
  assert.strictEqual(block, ref.block);
  const recs = theobald.records.map((r) => ({ title: r.title, abstract: r.abstract }));
  assert.strictEqual(JSON.stringify(RF.buildRequest(block, recs.slice(0, 10))), JSON.stringify(ref.request_batch));
  assert.strictEqual(JSON.stringify(RF.buildRequest(block, recs.slice(0, 1))), JSON.stringify(ref.request_single));
});

test('stopping p values equal the Python port', () => {
  let maxd = 0;
  for (const r of ref.h0) {
    const seq = stopRef.sequences[r.key];
    const p = RF.h0Pvalue(seq.labels_in_order.slice(0, r.t), seq.N);
    maxd = Math.max(maxd, Math.abs(p - r.p));
  }
  assert.ok(maxd < 1e-9, 'max diff ' + maxd);
  console.log('   max |JS - Python| =', maxd.toExponential(2), 'over', ref.h0.length, 'points');
});

test('stopping p values equal buscarpy 0.0.2', () => {
  let k = 0, maxd = 0;
  for (const r of stopRef.buscarpy) {
    if (r.p_buscarpy === null) continue;
    const seq = stopRef.sequences[r.key];
    const p = RF.h0Pvalue(seq.labels_in_order.slice(0, r.t), seq.N);
    maxd = Math.max(maxd, Math.abs(p - r.p_buscarpy)); k++;
  }
  assert.ok(k > 100 && maxd < 1e-9, `points ${k}, max diff ${maxd}`);
  console.log('   max |JS - buscarpy| =', maxd.toExponential(2), 'over', k, 'points');
});

test('RIS round trip and language flag equal the Python package', () => {
  const got = RF.parseRis(ref.ris_text);
  assert.strictEqual(got.length, ref.ris_parsed.length);
  got.forEach((g, i) => { for (const k of Object.keys(ref.ris_parsed[i])) assert.strictEqual(g[k], ref.ris_parsed[i][k], k); });
  const flags = theobald.records.map((r) => RF.looksNonEnglish({ title: r.title, abstract: r.abstract }));
  assert.deepStrictEqual(flags, ref.non_english);
});

test('CSV and MEDLINE parsing', () => {
  const csv = 'Title,Abstract,Year,DOI\n"A, quoted ""title""","Line one\nline two",2019,https://doi.org/10.5/ABC\nSecond,,2020,\n';
  const r = RF.parseCsv(csv);
  assert.strictEqual(r.length, 2); assert.strictEqual(r[0].title, 'A, quoted "title"'); assert.strictEqual(r[0].doi, '10.5/abc');
  assert.strictEqual(r[0].abstract, 'Line one\nline two'); assert.strictEqual(r[1].year, '2020');
  const nbib = 'PMID- 123456\nTI  - A title that\n      continues\nAB  - Abstract text.\nDP  - 2018 Jan\nLID - 10.1/xyz [doi]\nLA  - eng\n\nPMID- 999999\nTI  - Second\n';
  const m = RF.parseNbib(nbib);
  assert.strictEqual(m.length, 2); assert.strictEqual(m[0].title, 'A title that continues'); assert.strictEqual(m[0].doi, '10.1/xyz');
  assert.strictEqual(m[0].year, '2018'); assert.strictEqual(m[1].pmid, '999999');
});

test('full demo screen on Theobald_2021 stops and reports', () => {
  const [t, q, c] = ref.criteria;
  const p = RF.newProject(t, q, c, 7);
  const recs = theobald.records.map((r) => ({ title: r.title, abstract: r.abstract }));
  const cnt = RF.importRecords(p, recs, 'demo');
  assert.strictEqual(cnt.added, 70); assert.strictEqual(cnt.non_english, 15);
  const prob = {}, label = {}; theobald.records.forEach((r) => { prob[r.title] = r.paper_p; label[r.title] = r.label; });
  for (const r of RF.toScore(p)) p.scores[r.rid] = { p: prob[r.title] };
  RF.freezeRanking(p); assert.strictEqual(p.ranking.length, 55);
  let st, last = 2;
  for (;;) {
    const r = RF.nextRecord(p); if (!r) break;
    assert.ok(r.p <= last); last = r.p;
    st = RF.decide(p, r.rid, label[r.title] ? 'include' : 'exclude');
    if (st.met) break;
  }
  assert.ok(st.met); RF.stop(p);
  assert.throws(() => RF.decide(p, RF.nextRecord(p).rid, 'exclude'));
  for (let r; (r = RF.nextRecord(p, 'manual')); ) RF.decide(p, r.rid, label[r.title] ? 'include' : 'exclude');
  const P = RF.prisma(p);
  assert.strictEqual(P.records_identified, 70); assert.strictEqual(P.records_not_yet_screened, 0);
  assert.strictEqual(P.records_screened + P.records_not_screened_after_stopping, 70);
  const m = RF.methodsText(p); assert.ok(m.includes('We stopped screening') && !m.includes('—'));
  assert.strictEqual(RF.decisionsCsv(p).trim().split('\n').length, 71);
  console.log(`   stopped after ${st.screened} of 55 ranked records`);
});

console.log(`${n} tests passed`);
