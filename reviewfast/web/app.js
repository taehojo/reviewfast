'use strict';
// ReviewFast local web client. Plain JavaScript, no build step.

let TOKEN = null;
(function initToken() {
  const m = location.hash.match(/^#t=([\w-]+)/);
  try {
    if (m) { sessionStorage.setItem('reviewfast-token', m[1]); }
    TOKEN = m ? m[1] : sessionStorage.getItem('reviewfast-token');
  } catch (e) { TOKEN = m ? m[1] : null; }
  if (m) history.replaceState(null, '', '#/');
})();

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n, d = 0) => Number(n ?? 0).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (x, d = 1) => `${fmt(100 * x, d)}%`;

function toast(msg, ms = 3500) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('show'), ms); }

async function api(path, opts = {}) {
  const headers = { 'X-Token': TOKEN || '' };
  if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(opts.json); }
  const r = await fetch('/api' + path, { method: opts.method || (opts.body ? 'POST' : 'GET'), headers, body: opts.body });
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}
async function download(path) {
  const r = await fetch('/api' + path, { headers: { 'X-Token': TOKEN || '' } });
  if (!r.ok) { toast('Export failed'); return; }
  const cd = r.headers.get('content-disposition') || ''; const name = (cd.match(/filename="([^"]+)"/) || [])[1] || path.split('/').pop();
  const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = name; a.click(); URL.revokeObjectURL(a.href);
}

// ---------- routing
let POLL = null;
window.addEventListener('hashchange', route);
window.addEventListener('load', async () => {
  if (!TOKEN) { $('#main').innerHTML = '<div class="notice bad">No session token. Open the address printed by <code>reviewfast serve</code>.</div>'; return; }
  try { const i = await api('/info'); $('#version').textContent = 'v' + i.version; window.INFO = i; } catch (e) { $('#main').innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; return; }
  route();
});
function route() {
  clearInterval(POLL); POLL = null; document.onkeydown = null;
  const h = location.hash.replace(/^#/, '') || '/';
  const m = h.match(/^\/p\/([\w-]+)(?:\/(\w+))?/);
  if (m) return projectView(m[1], m[2] || 'overview');
  return homeView();
}

// ---------- home
async function homeView() {
  $('#crumb').textContent = '';
  const ps = await api('/projects');
  $('#main').innerHTML = `
    <h1>Screening projects</h1>
    <p class="muted small">Stored in ${esc(INFO.projects_dir)}</p>
    ${ps.length ? `<table><tr><th>Project</th><th>Review</th><th>Records</th><th>Screened</th></tr>${ps.map((p) => {
      const c = p.counts; const s = ['include', 'exclude', 'maybe'].reduce((a, d) => a + c['ranked_' + d] + c['manual_' + d], 0);
      return `<tr><td><a href="#/p/${esc(p.name)}">${esc(p.name)}</a></td><td>${esc(p.title)}</td><td>${fmt(c.unique)}</td><td>${fmt(s)}</td></tr>`; }).join('')}</table>`
      : '<p class="muted">No projects yet.</p>'}
    <h2>New project</h2>
    <div class="card">
      <p class="small muted">Take the title, question and eligibility criteria from your registered protocol, written before screening. Jev was evaluated with the criteria as the review authors reported them; criteria written after seeing the records can bias the ranking.</p>
      <label for="np-name">Project name (letters, digits, - and _)</label><input id="np-name" type="text" placeholder="statins-dementia-2026">
      <label for="np-title">Systematic review title</label><input id="np-title" type="text">
      <label for="np-q">Research question</label><textarea id="np-q" style="min-height:70px"></textarea>
      <label for="np-c">Eligibility criteria</label><textarea id="np-c" placeholder="Population, intervention or exposure, comparator, outcomes, study designs, and exclusion criteria, as written in the protocol."></textarea>
      <div class="row" style="margin-top:12px"><button class="primary" id="np-go">Create project</button></div>
    </div>`;
  $('#np-go').onclick = async () => {
    try {
      const r = await api('/projects', { json: { name: $('#np-name').value, title: $('#np-title').value, question: $('#np-q').value, criteria: $('#np-c').value } });
      location.hash = `#/p/${r.name}/import`;
    } catch (e) { toast(e.message); }
  };
}

// ---------- project
const TABS = [['overview', 'Overview'], ['import', 'Import'], ['score', 'Score'], ['screen', 'Screen'], ['manual', 'Manual queue'], ['report', 'Report']];
async function projectView(name, tab) {
  let S;
  try { S = await api(`/p/${name}`); } catch (e) { $('#main').innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; return; }
  $('#crumb').innerHTML = `/ ${esc(name)}`;
  $('#main').innerHTML = `
    <h1>${esc(S.meta.title)}</h1>
    <nav class="tabs">${TABS.map(([k, l]) => `<a href="#/p/${name}/${k}" class="${k === tab ? 'on' : ''}">${l}</a>`).join('')}</nav>
    <section id="tab"></section>`;
  const el = $('#tab');
  ({ overview: overviewTab, import: importTab, score: scoreTab, screen: screenTab, manual: manualTab, report: reportTab }[tab] || overviewTab)(name, S, el);
}

function stats(items) { return `<div class="stats">${items.map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join('')}</div>`; }

function overviewTab(name, S, el) {
  const c = S.counts, sc = S.scoring;
  const step = !c.unique ? 'import' : !S.ranked ? 'score' : 'screen';
  el.innerHTML = `
    ${stats([[fmt(c.imported), 'records imported'], [fmt(c.duplicates), 'duplicates'], [fmt(c.manual_queue), 'manual queue'], [fmt(sc.scored), 'scored by Jev'], [fmt(c.ranked), 'ranked'], ['$' + fmt(sc.cost_usd, 3), 'spent on Jev']])}
    <div class="card"><b>Next step:</b> <a href="#/p/${name}/${step}">${{ import: 'Import search results', score: 'Score and freeze the ranking', screen: 'Screen in ranked order' }[step]}</a></div>
    <h2>Protocol</h2>
    <div class="card"><h3>Research question</h3><p>${esc(S.meta.question)}</p><h3>Eligibility criteria</h3><pre>${esc(S.meta.criteria)}</pre>
    <p class="small muted">Stopping target: recall ${S.meta.recall_target} with confidence ${S.meta.confidence}. Created ${esc(S.meta.created)} with ReviewFast ${esc(S.meta.app_version)}.</p></div>`;
}

let LAST_IMPORT = null, LAST_IMPORT_FOR = null;
function importTab(name, S, el) {
  const locked = S.ranked;
  el.innerHTML = `
    ${locked ? '<div class="notice">The ranking is frozen, so no more records can be added.</div>' : ''}
    <div class="card">
      <h3>Upload search results</h3>
      <p class="small muted">RIS, CSV (with a title column), PubMed format (.nbib) or PubMed XML. Export all records from each database; duplicates are removed on DOI, PMID, or title and year. Do not sort the export by relevance.</p>
      <input type="file" id="f" multiple ${locked ? 'disabled' : ''}> <button class="primary" id="up" ${locked ? 'disabled' : ''}>Import</button>
    </div>
    <div class="card">
      <h3>Or paste PubMed IDs</h3>
      <p class="small muted">Titles and abstracts are fetched from NCBI E-utilities; only the PMIDs are sent.</p>
      <textarea id="pm" style="min-height:70px" ${locked ? 'disabled' : ''}></textarea>
      <button id="pmgo" ${locked ? 'disabled' : ''}>Fetch and import</button>
    </div>
    <div id="res">${LAST_IMPORT_FOR === name ? LAST_IMPORT : ''}</div>
    ${stats([[fmt(S.counts.unique), 'unique records'], [fmt(S.counts.duplicates), 'duplicates removed'], [fmt(S.counts.manual_by_reason.no_abstract), 'no abstract (manual)'], [fmt(S.counts.manual_by_reason.non_english), 'possibly not English (manual)']])}`;
  const show = (r) => { LAST_IMPORT_FOR = name; LAST_IMPORT = `<div class="notice good">Read ${fmt(r.read)} records (${esc(r.format)}): ${fmt(r.added)} added, ${fmt(r.duplicates)} duplicates, ${fmt(r.no_abstract)} without abstract and ${fmt(r.non_english)} possibly not in English (both go to the manual queue).</div>`; };
  $('#up').onclick = async () => {
    for (const f of $('#f').files) {
      const fd = new FormData(); fd.append('file', f);
      try { show(await api(`/p/${name}/import`, { body: fd })); } catch (e) { toast(e.message); }
    }
    projectView(name, 'import');
  };
  $('#pmgo').onclick = async () => { try { toast('Fetching from PubMed…'); show(await api(`/p/${name}/import_pmids`, { json: { pmids: $('#pm').value } })); projectView(name, 'import'); } catch (e) { toast(e.message); } };
}

function keyForm(prefix) {
  return `
    <label><input type="radio" name="${prefix}-mode" value="key" checked> My Vercel AI Gateway API key</label>
    <input type="password" id="${prefix}-key" placeholder="${INFO.env_key ? 'Using AI_GATEWAY_API_KEY from the environment' : 'API key'}" autocomplete="off">
    <p class="small muted">Held in memory for this run only and never written to the project. Create one at vercel.com under AI Gateway, API keys.</p>
    ${INFO.trial_available ? `<label><input type="radio" name="${prefix}-mode" value="trial"> Free trial (sign in with GitHub)</label><div id="${prefix}-trial"></div>` : ''}
    <div class="notice small"><label style="font-weight:400;margin:0"><input type="checkbox" id="${prefix}-consent">
      I understand that the review criteria and the titles and abstracts are sent to TypeSafe AI through Vercel (servers in the United States),
      that the provider does not offer zero data retention, and I confirm that these records are published or otherwise cleared for this use.
      I will not send unpublished, confidential or personal data.</label></div>`;
}
function keyBody(prefix) {
  const mode = (document.querySelector(`input[name=${prefix}-mode]:checked`) || {}).value || 'key';
  return { mode, key: ($(`#${prefix}-key`) || {}).value || '', consent: $(`#${prefix}-consent`).checked };
}
function wireTrial(prefix) {
  const box = $(`#${prefix}-trial`); if (!box) return;
  box.innerHTML = '<button id="tl">Sign in with GitHub</button> <span id="tq" class="small muted"></span>';
  api('/trial/quota').then((q) => { if (q.signed_in) $('#tq').textContent = q.quota ? `${fmt(q.quota.remaining)} free records left` : 'Signed in'; }).catch(() => {});
  $('#tl').onclick = async () => {
    try {
      const d = await api('/trial/login', { method: 'POST' });
      $('#tq').innerHTML = `Go to <a href="${esc(d.verification_uri)}" target="_blank" rel="noopener">${esc(d.verification_uri)}</a> and enter <b>${esc(d.user_code)}</b>`;
      const t = setInterval(async () => { const r = await api('/trial/poll', { method: 'POST' }); if (r.signed_in) { clearInterval(t); $('#tq').textContent = r.quota ? `${fmt(r.quota.remaining)} free records left` : 'Signed in'; } }, (d.interval + 1) * 1000);
    } catch (e) { toast(e.message); }
  };
}

function scoreTab(name, S, el) {
  const sc = S.scoring, job = S.job;
  const running = job && job.running;
  el.innerHTML = `
    ${stats([[fmt(sc.eligible), 'records to rank'], [fmt(sc.scored), 'scored'], [fmt(sc.failed), 'failed or refused'], [fmt(S.to_score), 'still to score'], ['$' + fmt(sc.cost_usd, 3), 'spent'], ['$' + fmt(S.estimate_usd, 3), 'estimate for the rest']])}
    ${S.ranked ? `<div class="notice good">The ranking is frozen (${fmt(S.counts.ranked)} records). <a href="#/p/${name}/screen">Start screening</a>.</div>` : ''}
    ${!S.ranked && (S.to_score || running) ? `<div class="card"><h3>Score with Jev</h3>
      <p class="small muted">Records are sent in random order, ten per request, with your review criteria. Records without an abstract or possibly not in English are not sent; they are in the manual queue.</p>
      ${keyForm('sc')}
      <div class="row"><button class="primary" id="go" ${running || !S.to_score ? 'disabled' : ''}>Score ${fmt(S.to_score)} records</button><button id="stop" ${running ? '' : 'disabled'}>Cancel</button></div>
      <div id="prog" style="margin-top:12px"></div></div>` : ''}
    ${!S.ranked && sc.scored && !S.to_score ? `<div class="card"><h3>Freeze the ranking</h3>
      <p class="small muted">Fixes the screening order (highest Jev probability first). Records that failed or were refused move to the manual queue. After this, no records can be added and the criteria cannot change.</p>
      <button class="primary" id="rank" ${running ? 'disabled' : ''}>Freeze ranking</button></div>` : ''}
    ${sc.scored ? `<div class="card"><h3>Model drift check</h3>
      <p class="small muted">Jev does not report a model version. This re-sends up to five archived requests unchanged and compares the probabilities with the stored ones.</p>
      ${S.ranked || !S.to_score ? keyForm('dr') : '<p class="small muted">Uses the key and data notice above.</p>'}
      <button id="drift" ${running ? 'disabled' : ''}>Run drift check (about $0.001)</button><div id="dres"></div></div>` : ''}`;
  const pre = S.ranked || !S.to_score ? 'dr' : 'sc';
  wireTrial(pre);
  const showJob = (j) => {
    if (!j) return;
    const box = $('#prog') || $('#dres'); if (!box) return;
    if (j.kind === 'drift' && !j.running && j.summary) { $('#dres').innerHTML = `<div class="notice ${j.summary.max_abs_diff > 0.02 ? 'bad' : 'good'}">Re-scored ${fmt(j.summary.records)} records: ${fmt(j.summary.identical)} identical, mean absolute difference ${fmt(j.summary.mean_abs_diff, 4)}, largest ${fmt(j.summary.max_abs_diff, 4)}.</div>`; return; }
    box.innerHTML = `<div class="bar"><i style="width:${j.total ? 100 * j.done / j.total : 0}%"></i></div>
      <p class="small">${fmt(j.done)} / ${fmt(j.total)} records · $${fmt(j.spent, 4)} · rate-limit waits ${fmt(j.throttled)} ${j.running ? '· running' : ''}</p>
      ${j.error ? `<div class="notice bad">${esc(j.error)}</div>` : ''}`;
  };
  showJob(job);
  const poll = () => { clearInterval(POLL); POLL = setInterval(async () => { const j = await api(`/p/${name}/job`); showJob(j); if (!j || !j.running) { clearInterval(POLL); if (!j || !j.error) projectView(name, 'score'); } }, 1500); };
  if (running) poll();
  const go = $('#go'); if (go) go.onclick = async () => { try { await api(`/p/${name}/score`, { json: keyBody('sc') }); poll(); go.disabled = true; $('#stop').disabled = false; } catch (e) { toast(e.message); } };
  const st = $('#stop'); if (st) st.onclick = async () => { await api(`/p/${name}/job`, { method: 'DELETE' }); toast('Cancelling after the current requests…'); };
  const rk = $('#rank'); if (rk) rk.onclick = async () => { try { await api(`/p/${name}/rank`, { method: 'POST' }); location.hash = `#/p/${name}/screen`; } catch (e) { toast(e.message); } };
  const dr = $('#drift'); if (dr) dr.onclick = async () => { try { await api(`/p/${name}/drift`, { json: keyBody(pre) }); $('#dres').innerHTML = '<p class="small">Running…</p>'; poll(); } catch (e) { toast(e.message); } };
}

function stopPanel(st) {
  const met = st.met; const stopped = st.stopped_at !== null && st.stopped_at !== undefined;
  return `<div class="card">
    <div class="row"><b>Stopping criterion</b><span class="chip">recall ≥ ${st.recall_target} with ${pct(st.confidence, 0)} confidence</span><span class="spacer"></span>
    ${stopped ? '<span class="chip">stopped</span>' : `<button id="stopbtn" class="${met ? 'primary' : ''}" ${met ? '' : 'disabled'}>Stop screening the ranked list</button>`}</div>
    <div class="bar" style="margin:10px 0"><i style="width:${100 * st.proportion_screened}%"></i></div>
    <p class="small">${fmt(st.screened)} of ${fmt(st.ranked_total)} ranked records screened (${pct(st.proportion_screened)}), ${fmt(st.relevant_seen)} relevant so far.
    p = ${st.screened ? fmt(st.p_value, 4) : '–'} (${met ? `<b style="color:var(--good)">criterion met</b>: you may stop` : `stop when p < ${fmt(1 - st.confidence, 2)}`}).
    ${st.manual_remaining ? ` ${fmt(st.manual_remaining)} records in the manual queue still need screening.` : ''}</p></div>`;
}

async function screenTab(name, S, el) {
  if (!S.ranked) { el.innerHTML = `<div class="notice">Score the records and freeze the ranking first (<a href="#/p/${name}/score">Score</a>).</div>`; return; }
  let showScore = false; try { showScore = localStorage.getItem('reviewfast-show-score') === '1'; } catch (e) { /* storage unavailable */ }
  let cur = null;
  const render = async () => {
    const d = await api(`/p/${name}/next?queue=ranked&show_score=${showScore}`); cur = d.record;
    const st = d.stop; const stopped = st.stopped_at !== null && st.stopped_at !== undefined;
    el.innerHTML = `${stopPanel(st)}
      ${stopped ? `<div class="notice good">Screening of the ranked list stopped after ${fmt(st.stopped_at)} records. ${st.manual_remaining ? `<a href="#/p/${name}/manual">Screen the manual queue</a>, then` : 'Next:'} <a href="#/p/${name}/report">report</a>.</div>`
      : !cur ? `<div class="notice good">All ranked records have been screened.</div>`
      : `<div class="card record">
        <div class="row small muted"><span>Rank ${fmt(cur.rank)} of ${fmt(st.ranked_total)}</span>${cur.year ? `<span>${esc(cur.year)}</span>` : ''}${cur.journal ? `<span>${esc(cur.journal)}</span>` : ''}${showScore ? `<span class="chip">Jev p = ${fmt(cur.p, 3)}</span>` : ''}</div>
        <h2>${esc(cur.title)}</h2>
        ${cur.authors ? `<p class="small muted">${esc(cur.authors.split(';').slice(0, 6).join(';'))}${cur.authors.split(';').length > 6 ? '; et al.' : ''}</p>` : ''}
        <div class="abstract">${esc(cur.abstract)}</div>
        ${cur.doi || cur.pmid ? `<p class="small">${cur.doi ? `<a href="https://doi.org/${esc(cur.doi)}" target="_blank" rel="noopener">doi:${esc(cur.doi)}</a> ` : ''}${cur.pmid ? `<a href="https://pubmed.ncbi.nlm.nih.gov/${esc(cur.pmid)}/" target="_blank" rel="noopener">PMID ${esc(cur.pmid)}</a>` : ''}</p>` : ''}
        <div class="decide"><button class="inc" data-d="include">Include <kbd>I</kbd></button><button data-d="maybe">Maybe <kbd>M</kbd></button><button class="exc" data-d="exclude">Exclude <kbd>E</kbd></button><button id="undo">Undo <kbd>U</kbd></button></div>
      </div>`}
      <p class="small muted"><label style="display:inline;font-weight:400"><input type="checkbox" id="ss" ${showScore ? 'checked' : ''}> Show Jev probability</label> (hidden by default to avoid anchoring on the model's judgement). "Maybe" counts as relevant for the stopping criterion.</p>`;
    el.querySelectorAll('.decide button[data-d]').forEach((b) => { b.onclick = () => decide(b.dataset.d); });
    const u = $('#undo'); if (u) u.onclick = undo;
    const sb = $('#stopbtn'); if (sb) sb.onclick = async () => { if (confirm('Stop screening the ranked list? The remaining ranked records will be reported as not screened.')) { try { await api(`/p/${name}/stop`, { method: 'POST' }); render(); } catch (e) { toast(e.message); } } };
    $('#ss').onchange = (e) => { showScore = e.target.checked; try { localStorage.setItem('reviewfast-show-score', showScore ? '1' : '0'); } catch (x) { /* ignore */ } render(); };
  };
  let busy = false;
  const decide = async (dcs) => { if (!cur || busy) return; busy = true; try { await api(`/p/${name}/decide`, { json: { rid: cur.rid, decision: dcs } }); await render(); } catch (e) { toast(e.message); } busy = false; };
  const undo = async () => { if (busy) return; busy = true; try { const r = await api(`/p/${name}/undo`, { method: 'POST' }); if (r.undone && r.undone.queue !== 'ranked') toast('Undid a manual-queue decision'); await render(); } catch (e) { toast(e.message); } busy = false; };
  document.onkeydown = (e) => { if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.metaKey || e.ctrlKey) return; const k = e.key.toLowerCase(); if (k === 'i') decide('include'); else if (k === 'e') decide('exclude'); else if (k === 'm') decide('maybe'); else if (k === 'u') undo(); };
  render();
}

async function manualTab(name, S, el) {
  const reasons = { no_abstract: 'no abstract', non_english: 'possibly not English', no_score: 'no Jev score', user: 'set aside' };
  if (!S.ranked) {
    const rows = await api(`/p/${name}/records?queue=manual`);
    el.innerHTML = `<div class="card"><p class="small muted">These records are not sent to Jev and will be screened in full by hand. Before the ranking is frozen you can move a record back to the ranked set, for example when an abstract is in English but was flagged.</p>
      ${rows.length ? `<table><tr><th>Title</th><th>Reason</th><th></th></tr>${rows.map((r) => `<tr><td>${esc(r.title)}</td><td><span class="chip">${reasons[r.flag] || esc(r.flag)}</span></td><td>${r.flag === 'no_abstract' ? '' : `<button data-rid="${r.rid}">Send to Jev</button>`}</td></tr>`).join('')}</table>` : '<p class="muted">The manual queue is empty.</p>'}</div>`;
    el.querySelectorAll('button[data-rid]').forEach((b) => { b.onclick = async () => { await api(`/p/${name}/records/${b.dataset.rid}/flag`, { json: { flag: null } }); manualTab(name, S, el); }; });
    return;
  }
  let cur = null;
  const render = async () => {
    const d = await api(`/p/${name}/next?queue=manual`); cur = d.record;
    el.innerHTML = !cur ? `<div class="notice good">The manual queue is done.</div>` : `
      <p class="small muted">${fmt(d.stop.manual_remaining)} records left in the manual queue. These are screened in full, in any order, and do not count toward the stopping criterion.</p>
      <div class="card record"><div class="row small muted"><span class="chip">${reasons[cur.flag] || esc(cur.flag)}</span>${cur.year ? `<span>${esc(cur.year)}</span>` : ''}${cur.journal ? `<span>${esc(cur.journal)}</span>` : ''}</div>
      <h2>${esc(cur.title)}</h2><div class="abstract">${esc(cur.abstract || '(no abstract)')}</div>
      ${cur.doi || cur.pmid ? `<p class="small">${cur.doi ? `<a href="https://doi.org/${esc(cur.doi)}" target="_blank" rel="noopener">doi:${esc(cur.doi)}</a> ` : ''}${cur.pmid ? `<a href="https://pubmed.ncbi.nlm.nih.gov/${esc(cur.pmid)}/" target="_blank" rel="noopener">PMID ${esc(cur.pmid)}</a>` : ''}</p>` : ''}
      <div class="decide"><button class="inc" data-d="include">Include <kbd>I</kbd></button><button data-d="maybe">Maybe <kbd>M</kbd></button><button class="exc" data-d="exclude">Exclude <kbd>E</kbd></button></div></div>`;
    el.querySelectorAll('.decide button').forEach((b) => { b.onclick = () => decide(b.dataset.d); });
  };
  const decide = async (dcs) => { if (!cur) return; try { await api(`/p/${name}/decide`, { json: { rid: cur.rid, decision: dcs } }); render(); } catch (e) { toast(e.message); } };
  document.onkeydown = (e) => { if (e.target.tagName === 'INPUT' || e.metaKey || e.ctrlKey) return; const k = e.key.toLowerCase(); if (k === 'i') decide('include'); else if (k === 'e') decide('exclude'); else if (k === 'm') decide('maybe'); };
  render();
}

async function reportTab(name, S, el) {
  const P = await api(`/p/${name}/export/prisma.json`); const M = await api(`/p/${name}/export/methods.txt`);
  const row = (l, v) => `<tr><td>${l}</td><td style="text-align:right">${fmt(v)}</td></tr>`;
  el.innerHTML = `
    ${stopPanel(P.stopping)}
    <h2>PRISMA 2020 counts (title and abstract stage)</h2>
    <div class="card"><table>
      ${row('Records identified', P.records_identified)}${row('Duplicates removed', P.duplicates_removed)}${row('Records after deduplication', P.records_after_deduplication)}
      ${row('Ranked by Jev', P.records_ranked_by_jev)}${row('Screened outside the ranking (manual queue)', P.records_for_manual_screening)}
      ${row('Records screened', P.records_screened)}${row('Not screened after the stopping criterion was met', P.records_not_screened_after_stopping)}
      ${row('Not yet screened', P.records_not_yet_screened)}${row('Records excluded', P.records_excluded)}${row('Records sought for full-text retrieval (include or maybe)', P.records_sought_for_retrieval)}
    </table><p class="small muted">Report records not screened after stopping as excluded by the stopping rule, and name the rule, in the PRISMA flow diagram.</p></div>
    <h2>Methods paragraph (draft)</h2>
    <div class="card"><pre id="mt">${esc(M)}</pre><button id="cp">Copy</button></div>
    <h2>Export</h2>
    <div class="card row">
      <button data-x="decisions.csv">Decisions (CSV)</button><button data-x="included.ris">Included and maybe (RIS)</button><button class="primary" data-x="archive.zip">Reproducibility archive (ZIP)</button>
    </div>
    <p class="small muted">The archive holds a copy of the project database, every request and response, the scores, decisions, PRISMA counts and SHA-256 sums. Keep it with your review records.</p>`;
  el.querySelectorAll('button[data-x]').forEach((b) => { b.onclick = () => download(`/p/${name}/export/${b.dataset.x}`); });
  $('#cp').onclick = async () => { try { await navigator.clipboard.writeText(M); toast('Copied'); } catch (e) { toast('Copy failed; select the text instead'); } };
  const sb = $('#stopbtn'); if (sb) sb.onclick = async () => { if (confirm('Stop screening the ranked list?')) { try { await api(`/p/${name}/stop`, { method: 'POST' }); projectView(name, 'report'); } catch (e) { toast(e.message); } } };
}
