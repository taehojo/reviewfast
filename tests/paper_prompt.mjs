// Request bodies built exactly as in the paper's scorer (synergy/run_screen.mjs, lines 16-22 and 33-37, copied verbatim), for
// the Theobald_2021 fixture in batches of 10 in fixture order. tests/test_jev_client.py compares them with jevscreen.
import fs from 'node:fs';
const F = JSON.parse(fs.readFileSync(new URL('./fixtures/theobald_2021.json', import.meta.url), 'utf8'));
const CRIT = { Theobald_2021: F.criteria_block };
const QUESTIONS_ALT = {
  q0: 'Based on the title and abstract, should this record be advanced to full-text screening for this review? Give the probability that it meets the eligibility criteria.',
};
const QUESTION = QUESTIONS_ALT.q0;
const recText = (d) => `Title: ${d.title || '(no title)'}\nAbstract: ${d.abstract || '(no abstract available; judge from the title)'}`;
function body(recs) {
  const crit = CRIT[recs[0].review];
  let state, questions;
  if (recs.length === 1) { state = crit + '\n\n' + recText(recs[0]); questions = { r1: { type: 'noul', instructions: QUESTION } }; }
  else {
    state = crit + '\n\nThe following records are independent candidates retrieved by the search; judge each one on its own.\n\n' + recs.map((d, i) => `[Record R${i + 1}]\n${recText(d)}`).join('\n\n');
    questions = {}; recs.forEach((_, i) => { questions['r' + (i + 1)] = { type: 'noul', instructions: `Consider only Record R${i + 1}. ${QUESTION}` }; });
  }
  return { model: 'typesafe-ai/jev', state, questions };
}
const recs = F.records.map((r) => ({ ...r, review: 'Theobald_2021' }));
const out = [];
for (let i = 0; i < recs.length; i += 10) out.push(body(recs.slice(i, i + 10)));
out.push(body([recs[0]]));   // the single-record form
process.stdout.write(JSON.stringify(out));
