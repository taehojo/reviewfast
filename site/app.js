/* ReviewFast web: user interface. Plain JavaScript, no build step, no third-party code. Everything runs in the browser; the only
   network requests leave for the Vercel AI Gateway (scoring) and this site (page files and the demo data). */
(function () {
  'use strict';
  const RF = window.RF;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtP = (p) => (p >= 0.1 ? p.toFixed(3) : p >= 0.001 ? p.toPrecision(3) : p === 0 ? '0' : p.toExponential(1));
  const pct = (a, b) => (b ? (100 * a / b).toFixed(1) : '0.0') + '%';
  const home = $('#home'), view = $('#view');
  $('#ver').textContent = RF.VERSION;

  let KEY = null;               // gateway key: memory only
  let demo = null;              // demo project (not saved)
  let scoring = null;           // {cancel: bool}
  let keyHandler = null;

  // ---------------- storage (IndexedDB, falls back to memory)
  const DB = {
    db: null, mem: new Map(), ok: true,
    async open() {
      try {
        this.db = await new Promise((res, rej) => {
          const r = indexedDB.open('reviewfast', 1);
          r.onupgradeneeded = () => r.result.createObjectStore('projects', { keyPath: 'id' });
          r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
        });
      } catch (e) { this.ok = false; this.db = null; }
    },
    tx(mode, fn) {
      return new Promise((res, rej) => {
        const t = this.db.transaction('projects', mode), s = t.objectStore('projects'); const r = fn(s);
        t.oncomplete = () => res(r && r.result); t.onerror = () => rej(t.error); t.onabort = () => rej(t.error);
      });
    },
    async put(p) { if (!this.db) { this.mem.set(p.id, p); return; } await this.tx('readwrite', (s) => s.put(p)); },
    async get(id) { if (!this.db) return this.mem.get(id); return this.tx('readonly', (s) => s.get(id)); },
    async all() { if (!this.db) return [...this.mem.values()]; return (await this.tx('readonly', (s) => s.getAll())) || []; },
    async del(id) { if (!this.db) { this.mem.delete(id); return; } await this.tx('readwrite', (s) => s.delete(id)); },
  };
  const cache = new Map();
  let saveTimer = null;
  function save(p, now) {
    if (p.demo) return;
    cache.set(p.id, p);
    clearTimeout(saveTimer);
    const go = () => DB.put(p).catch((e) => toast('Could not save the project in this browser: ' + e.message, 'bad'));
    if (now) return go();
    saveTimer = setTimeout(go, 400);
  }
  async function load(id) { if (cache.has(id)) return cache.get(id); const p = await DB.get(id); if (p) cache.set(id, p); return p; }

  function toast(msg, kind) {
    const n = document.createElement('div'); n.className = `notice ${kind || ''}`; n.setAttribute('role', 'status'); n.textContent = msg;
    view.prepend(n); setTimeout(() => n.remove(), 7000);
  }
  function download(name, text, type) {
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type: type || 'text/plain' }));
    a.download = name; document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  const slug = (s) => (s || 'project').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'project';

  // ---------------- router
  async function route() {
    if (keyHandler) { document.removeEventListener('keydown', keyHandler); keyHandler = null; }
    const h = location.hash.replace(/^#\/?/, ''); const [a, b, c] = h.split('/');
    $$('nav.main a[data-r]').forEach((x) => x.classList.toggle('on', x.dataset.r === (a || 'home') || (a === 'p' && x.dataset.r === 'projects')));
    const show = (isHome) => { home.classList.toggle('hidden', !isHome); view.classList.toggle('hidden', isHome); };
    if (!a) { show(true); return; }
    show(false); window.scrollTo(0, 0);
    try {
      if (a === 'demo') return renderDemo();
      if (a === 'use') return renderUse();
      if (a === 'projects') return renderProjects();
      if (a === 'p' && b) {
        const p = await load(b);
        if (!p) { view.innerHTML = '<div class="notice bad">Project not found in this browser.</div><a class="btn" href="#/projects">Back to projects</a>'; return; }
        return renderProject(p, c || defaultStep(p));
      }
      location.hash = '#/';
    } catch (e) { view.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  }
  window.addEventListener('hashchange', route);

  function defaultStep(p) {
    if (!p.records.length) return 'import';
    if (!p.ranking) return 'score';
    if (RF.nextRecord(p) && p.stopped_at == null) return 'screen';
    if (RF.nextRecord(p, 'manual')) return 'manual';
    return 'report';
  }

  // ---------------- projects
  async function renderProjects() {
    const list = (await DB.all()).sort((x, y) => (y.created || '').localeCompare(x.created || ''));
    view.innerHTML = `
      <h1>Screen your records</h1>
      <p class="lead">Create a project, import your search results, score them with your own Vercel AI Gateway key, and screen in ranked order.</p>
      ${DB.ok ? '' : '<div class="notice warn">This browser blocks local storage, so projects last only until you close the tab. Export your project to keep it.</div>'}
      <div class="grid g2">
        <div class="card">
          <h3>New project</h3>
          <form id="newp">
            <label for="np-t">Review title</label><input id="np-t" type="text" required>
            <label for="np-q">Research question</label><input id="np-q" type="text" required>
            <label for="np-c">Eligibility criteria (from your protocol)</label><textarea id="np-c" required placeholder="Population, intervention or exposure, comparator, outcomes, study designs, other limits"></textarea>
            <p class="small muted">The criteria are sent with every scoring request and cannot change after scoring starts.</p>
            <button class="btn primary" type="submit">Create project</button>
          </form>
        </div>
        <div class="card">
          <h3>Your projects in this browser</h3>
          <div id="plist">${list.length ? '' : '<p class="muted">No projects yet.</p>'}</div>
          <label for="openf">Open a project file (.json)</label>
          <input id="openf" type="file" accept=".json,application/json">
        </div>
      </div>`;
    const pl = $('#plist');
    for (const p of list) {
      const c = RF.counts(p); const st = p.ranking ? RF.projectStop(p) : null;
      const status = p.stopped_at != null ? '<span class="pill good">stopped</span>' : p.ranking ? `<span class="pill warn">screening ${st.screened}/${c.ranked}</span>` : '<span class="pill muted">setup</span>';
      const row = document.createElement('div'); row.className = 'list-row';
      row.innerHTML = `<div><b>${esc(p.title)}</b><div class="small muted">${c.unique} records · created ${esc((p.created || '').slice(0, 10))} ${status}</div></div>
        <div class="btns"><a class="btn sm primary" href="#/p/${esc(p.id)}">Open</a><button class="btn sm" data-x="exp">Export</button><button class="btn sm ghost" data-x="del">Delete</button></div>`;
      row.querySelector('[data-x=exp]').addEventListener('click', () => download(`reviewfast-${slug(p.title)}.json`, JSON.stringify(p), 'application/json'));
      row.querySelector('[data-x=del]').addEventListener('click', async () => {
        if (!confirm(`Delete "${p.title}" from this browser? Export it first if you want to keep it.`)) return;
        await DB.del(p.id); cache.delete(p.id); renderProjects();
      });
      pl.appendChild(row);
    }
    $('#newp').addEventListener('submit', async (e) => {
      e.preventDefault();
      const p = RF.newProject($('#np-t').value, $('#np-q').value, $('#np-c').value);
      await save(p, true); location.hash = `#/p/${p.id}/import`;
    });
    $('#openf').addEventListener('change', async (e) => {
      const f = e.target.files[0]; if (!f) return;
      try {
        const p = JSON.parse(await f.text());
        if (!p || !p.id || !Array.isArray(p.records) || p.app !== 'ReviewFast web') throw new Error('This is not a ReviewFast web project file.');
        if (await DB.get(p.id) && !confirm('A project with the same identifier exists in this browser. Replace it?')) return;
        await save(p, true); location.hash = `#/p/${p.id}`;
      } catch (err) { toast(err.message, 'bad'); }
    });
  }

  // ---------------- project views
  const STEPS = [['setup', 'Criteria'], ['import', 'Import'], ['score', 'Score'], ['screen', 'Screen'], ['manual', 'Manual queue'], ['report', 'Report']];
  function stepper(p, cur) {
    const done = { setup: true, import: p.records.length > 0, score: !!p.ranking, screen: p.stopped_at != null, manual: p.ranking && !RF.nextRecord(p, 'manual'), report: false };
    return `<nav class="stepper" aria-label="Steps">${STEPS.map(([k, l]) => `<a href="#/p/${esc(p.id)}/${k}" class="${k === cur ? 'on' : done[k] ? 'done' : ''}">${l}</a>`).join('')}</nav>`;
  }
  function head(p, cur) {
    return `<div class="small muted"><a href="#/projects">Projects</a> / ${esc(p.title)}</div>${stepper(p, cur)}`;
  }
  function renderProject(p, step) {
    ({ setup: viewSetup, import: viewImport, score: viewScore, screen: viewScreen, manual: viewManual, report: viewReport }[step] || viewImport)(p);
  }

  function viewSetup(p) {
    const locked = p.batches.some((b) => b.status === 200);
    view.innerHTML = `${head(p, 'setup')}<div class="card"><h3>Review criteria</h3>
      ${locked ? '<div class="notice">Records have been scored with these criteria, so they can no longer change. Start a new project to use different criteria.</div>' : ''}
      <label for="s-t">Review title</label><input id="s-t" type="text" value="${esc(p.title)}" ${locked ? 'disabled' : ''}>
      <label for="s-q">Research question</label><input id="s-q" type="text" value="${esc(p.question)}" ${locked ? 'disabled' : ''}>
      <label for="s-c">Eligibility criteria</label><textarea id="s-c" ${locked ? 'disabled' : ''}>${esc(p.criteria)}</textarea>
      <p class="small muted">What the classifier receives before each batch of records:</p><pre>${esc(RF.criteriaBlock(p.title, p.question, p.criteria))}</pre>
      ${locked ? '' : '<button class="btn primary" id="s-save">Save</button>'}</div>`;
    if (!locked) $('#s-save').addEventListener('click', () => {
      p.title = $('#s-t').value; p.question = $('#s-q').value; p.criteria = $('#s-c').value; RF.logEvent(p, 'criteria_updated'); save(p, true); viewSetup(p);
    });
  }

  function viewImport(p) {
    const c = RF.counts(p);
    view.innerHTML = `${head(p, 'import')}
      <div class="grid g2">
        <div class="card"><h3>Import search results</h3>
          <p class="muted">RIS, CSV, PubMed format (.nbib) or PubMed XML. Import every database export; duplicates are removed by DOI, PubMed identifier, or title and year.</p>
          ${p.ranking ? '<div class="notice">The ranking is frozen, so no more records can be added.</div>' : '<label for="files">Files</label><input id="files" type="file" multiple accept=".ris,.txt,.csv,.tsv,.nbib,.medline,.xml">'}
          <div id="imp-log"></div>
        </div>
        <div class="card"><h3>Records</h3>
          <div class="kv"><span>Imported</span><b>${c.imported}</b><span>Duplicates removed</span><b>${c.duplicates}</b><span>Unique records</span><b>${c.unique}</b>
          <span>Manual queue: no abstract</span><b>${c.manual_by_reason.no_abstract}</b><span>Manual queue: not in English</span><b>${c.manual_by_reason.non_english}</b>
          <span>To be ranked</span><b>${c.unique - c.manual_queue}</b></div>
          <p class="small muted">Records in the manual queue are not sent to the classifier; screen them in full after the ranked list.</p>
          <a class="btn primary ${c.unique - c.manual_queue ? '' : 'hidden'}" href="#/p/${esc(p.id)}/score">Next: score the records</a>
        </div>
      </div>`;
    const inp = $('#files');
    if (inp) inp.addEventListener('change', async (e) => {
      const log = $('#imp-log'); log.innerHTML = '';
      for (const f of e.target.files) {
        try {
          const [fmt, recs] = RF.parseFile(f.name, await f.text());
          const r = RF.importRecords(p, recs, `${fmt}:${f.name}`);
          log.insertAdjacentHTML('beforeend', `<div class="notice good">${esc(f.name)} (${fmt}): ${r.read} read, ${r.added} added, ${r.duplicates} duplicates${r.no_abstract ? `, ${r.no_abstract} without abstract` : ''}${r.non_english ? `, ${r.non_english} not in English` : ''}.</div>`);
        } catch (err) { log.insertAdjacentHTML('beforeend', `<div class="notice bad">${esc(f.name)}: ${esc(err.message)}</div>`); }
      }
      await save(p, true); const keep = log.innerHTML; viewImport(p); $('#imp-log').innerHTML = keep;
    });
  }

  // ---------------- scoring (browser to Vercel AI Gateway)
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function scoreAll(p, key, ui) {
    const todo = RF.toScore(p); const units = []; for (let i = 0; i < todo.length; i += RF.BATCH_SIZE) units.push(todo.slice(i, i + RF.BATCH_SIZE));
    const crit = RF.criteriaBlock(p.title, p.question, p.criteria);
    let next = 0, slot = 0, done = 0, waits = 0, authError = null;
    RF.logEvent(p, 'scoring_started', { records: todo.length, requests: units.length, endpoint: RF.GATEWAY_URL });
    const pace = async () => { const now = Date.now(); const at = Math.max(now, slot); slot = at + 700; if (at > now) await sleep(at - now); };
    async function send(body) {
      let attempt = 0, netErr = 0;
      for (;;) {
        attempt++;
        if (scoring.cancel) return { error: 'cancelled', attempts: attempt };
        await pace();
        let r;
        try {
          r = await fetch(RF.GATEWAY_URL, { method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        } catch (e) { netErr++; if (netErr <= 8) { await sleep(1500 * netErr); continue; } return { status: null, error: 'network error: ' + e.message, attempts: attempt }; }
        if (r.status === 401 || r.status === 403) { const t = await r.text(); throw Object.assign(new Error(`HTTP ${r.status}: ${t.slice(0, 200)}`), { auth: true }); }
        if (r.status === 429 || r.status >= 500) {
          if (attempt > 40) return { status: r.status, error: `HTTP ${r.status} after ${attempt} attempts`, attempts: attempt };
          const ra = parseFloat(r.headers.get('retry-after')) || 5; waits++; slot = Math.max(slot, Date.now() + (ra + Math.random() * 1.5) * 1000); continue;
        }
        if (r.status !== 200) return { status: r.status, error: `HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`, attempts: attempt };
        const j = await r.json();
        const cost = Number((((j.provider_metadata || {}).gateway || {}).marketCost) || 0);
        return { status: 200, response: j, tokens: (j.usage || {}).input_tokens || null, cost, attempts: attempt };
      }
    }
    async function worker() {
      while (!scoring.cancel && !authError) {
        const i = next++; if (i >= units.length) return;
        const unit = units[i]; const body = RF.buildRequest(crit, unit.map((r) => ({ title: r.title, abstract: r.abstract })));
        let res;
        try { res = await send(body); } catch (e) { if (e.auth) { authError = e; return; } throw e; }
        if (res.error === 'cancelled') return;
        const probs = res.response ? RF.parseAnswers(res.response, unit.length) : unit.map(() => null);
        const ts = new Date().toISOString();
        p.batches.push({ ts, endpoint: RF.GATEWAY_URL, purpose: 'score', rids: unit.map((r) => r.rid), request: body, response: res.response || null,
          status: res.status, error: res.error || null, tokens: res.tokens, cost: res.cost || 0, attempts: res.attempts });
        unit.forEach((r, k) => { p.scores[r.rid] = { p: probs[k], error: probs[k] == null ? (res.error || 'no answer from the classifier (possible refusal)') : null, ts }; });
        done += unit.length; save(p); ui(done, todo.length, RF.scoringSummary(p).cost_usd, waits);
      }
    }
    await Promise.all([worker(), worker()]);
    RF.logEvent(p, 'scoring_finished', { ...RF.scoringSummary(p), cancelled: !!scoring.cancel }); await save(p, true);
    if (authError) throw authError;
  }

  function viewScore(p) {
    const s = RF.scoringSummary(p), todo = s.eligible - s.scored - s.failed;
    view.innerHTML = `${head(p, 'score')}
      <div class="grid g2">
        <div class="card"><h3>Score with Jev</h3>
          ${p.ranking ? '<div class="notice">The ranking is frozen.</div>' : `
          <p class="muted">${todo} records to score, about US$${RF.estimateCost(todo).toFixed(3)} at the list price used in the study. Records go ten per request in a random order fixed by the project seed.</p>
          <label for="key">Vercel AI Gateway API key</label>
          <input id="key" type="password" autocomplete="off" placeholder="Paste your key" value="${KEY ? esc(KEY) : ''}">
          <p class="small muted">The key stays in this page's memory and is sent only to ai-gateway.vercel.sh. It needs paid credits; Jev is not on the free tier.</p>
          <label class="check"><input id="agree" type="checkbox"> <span>I understand that the criteria and the titles and abstracts go to TypeSafe AI through Vercel (United States), that the provider does not offer zero data retention, and that I am sending only published or cleared records.</span></label>
          <div class="btns"><button class="btn primary" id="go">${s.scored ? 'Resume scoring' : 'Start scoring'}</button><button class="btn hidden" id="stop">Cancel</button></div>
          <div class="bar" aria-hidden="true"><span id="pbar"></span></div><p class="small muted" id="pmsg"></p>`}
        </div>
        <div class="card"><h3>Progress</h3>
          <div class="kv"><span>Records to rank</span><b>${s.eligible}</b><span>Scored</span><b id="k-s">${s.scored}</b><span>No usable answer</span><b>${s.failed}</b>
          <span>Requests</span><b>${s.requests}</b><span>Cost reported by the gateway</span><b>US$${s.cost_usd.toFixed(4)}</b></div>
          <p class="small muted">Records without a usable answer, including refusals, move to the manual queue when you freeze the ranking.</p>
          ${!p.ranking && s.scored && !todo ? '<button class="btn primary" id="freeze">Freeze ranking and start screening</button>' : ''}
          ${p.ranking ? `<a class="btn primary" href="#/p/${esc(p.id)}/screen">Go to screening</a>` : ''}
          ${s.scored ? '<details><summary class="small">Check for model drift</summary><p class="small muted">Jev reports no model version. This re-sends up to five archived requests unchanged and compares the probabilities with the stored ones (costs a few cents at most).</p><button class="btn sm" id="drift">Run drift check</button><p class="small" id="drift-out"></p></details>' : ''}
        </div>
      </div>`;
    const bar = $('#pbar'); if (bar) bar.style.width = (s.eligible ? 100 * s.scored / s.eligible : 0) + '%';
    const go = $('#go');
    if (go) go.addEventListener('click', async () => {
      KEY = $('#key').value.trim();
      if (!KEY) return toast('Paste your Vercel AI Gateway API key.', 'warn');
      if (!$('#agree').checked) return toast('Please confirm the data notice first.', 'warn');
      scoring = { cancel: false }; go.disabled = true; $('#stop').classList.remove('hidden');
      $('#stop').onclick = () => { scoring.cancel = true; $('#pmsg').textContent = 'Cancelling after the current requests...'; };
      try {
        await scoreAll(p, KEY, (d, t, cost, w) => { bar.style.width = (100 * (s.scored + d) / s.eligible) + '%'; $('#pmsg').textContent = `${d} of ${t} scored · US$${cost.toFixed(4)} · ${w} rate-limit waits`; $('#k-s').textContent = s.scored + d; });
        viewScore(p);
      } catch (e) {
        viewScore(p); toast(e.auth ? `The gateway refused the key (${e.message}). Check the key and that the account has paid credits.` : e.message, 'bad');
      } finally { scoring = null; }
    });
    const fr = $('#freeze');
    if (fr) fr.addEventListener('click', () => { try { RF.freezeRanking(p); save(p, true); location.hash = `#/p/${p.id}/screen`; } catch (e) { toast(e.message, 'bad'); } });
    const dr = $('#drift');
    if (dr) dr.addEventListener('click', async () => {
      const k = KEY || ($('#key') && $('#key').value.trim()); if (!k) return toast('Paste your key in the scoring form first.', 'warn');
      const ok = p.batches.filter((b) => b.purpose === 'score' && b.status === 200 && b.request);
      const pick = RF.shuffle(ok, Date.now() % 2147483647).slice(0, 5); const pairs = []; dr.disabled = true;
      try {
        for (const b of pick) {
          const r = await fetch(RF.GATEWAY_URL, { method: 'POST', headers: { Authorization: `Bearer ${k}`, 'Content-Type': 'application/json' }, body: JSON.stringify(b.request) });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const j = await r.json(); const probs = RF.parseAnswers(j, b.rids.length);
          p.batches.push({ ts: new Date().toISOString(), endpoint: RF.GATEWAY_URL, purpose: 'drift_check', rids: b.rids, request: b.request, response: j, status: 200, error: null,
            tokens: (j.usage || {}).input_tokens || null, cost: Number((((j.provider_metadata || {}).gateway || {}).marketCost) || 0), attempts: 1 });
          b.rids.forEach((rid, i) => { if (probs[i] != null && p.scores[rid] && p.scores[rid].p != null) pairs.push([p.scores[rid].p, probs[i]]); });
        }
        const d = pairs.map(([a, b]) => Math.abs(a - b));
        const out = { batches: pick.length, records: pairs.length, identical: d.filter((x) => x === 0).length, max_abs_diff: d.length ? Math.max(...d) : 0 };
        RF.logEvent(p, 'drift_check', out); save(p, true);
        $('#drift-out').textContent = `${out.records} records re-scored: ${out.identical} identical, largest difference ${out.max_abs_diff.toFixed(2)}.`;
      } catch (e) { $('#drift-out').textContent = 'Drift check failed: ' + e.message; } finally { dr.disabled = false; }
    });
  }

  // ---------------- screening
  function spark(hist) {
    if (hist.length < 2) return '';
    const W = 300, H = 70, n = hist.length, y = (v) => H - 4 - (H - 8) * Math.min(1, Math.max(0, v));
    const pts = hist.map((v, i) => `${(W * i / (n - 1)).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="p value after each decision">
      <line class="ax" x1="0" y1="${H - 4}" x2="${W}" y2="${H - 4}"/><line class="th" x1="0" y1="${y(0.05)}" x2="${W}" y2="${y(0.05)}"/><polyline class="ln" points="${pts}"/></svg>`;
  }
  function pHistory(p) {
    if (!p._ph || p._ph.length > RF.rankedLabels(p).length) p._ph = [];
    const lab = RF.rankedLabels(p), N = p.ranking.length;
    for (let t = p._ph.length + 1; t <= lab.length; t++) p._ph.push(RF.h0Pvalue(lab.slice(0, t), N, p.recall_target));
    return p._ph;
  }
  function stopPanel(p) {
    const st = RF.projectStop(p), N = p.ranking.length, hist = pHistory(p);
    const met = st.met, stopped = st.stopped_at != null;
    return `<div class="card"><h3>Stopping criterion</h3>
      <div class="kv"><span>Screened</span><b>${st.screened} of ${N} (${pct(st.screened, N)})</b><span>Relevant so far</span><b>${st.relevant_seen}</b><span>p value</span><b>${st.screened ? fmtP(st.p_value) : '–'}</b></div>
      <div class="bar" aria-hidden="true"><span data-w="${N ? 100 * st.screened / N : 0}"></span></div>
      ${spark(hist)}
      <p class="small muted">H0: recall is below ${Math.round(p.recall_target * 100)}%. You can stop when p falls below ${(1 - p.confidence).toFixed(2)} (dashed line).</p>
      ${stopped ? `<div class="notice good">Stopped after ${st.stopped_at} of ${N} ranked records.</div>`
        : met ? `<div class="notice good"><b>The criterion is met.</b> At least ${Math.round(p.recall_target * 100)}% of the relevant records have been found, with ${Math.round(p.confidence * 100)}% confidence.</div><button class="btn good" id="stopbtn">Stop screening the ranked list</button>`
          : '<span class="pill warn">Keep screening</span>'}
    </div>`;
  }
  function recordCard(r, total, opts) {
    const meta = [r.year, r.journal, r.authors && r.authors.split(';').slice(0, 3).join(';') + (r.authors.split(';').length > 3 ? ' et al.' : '')].filter(Boolean).map(esc).join(' · ');
    return `<article class="card record">
      <div class="small muted">${opts.queue === 'manual' ? `Manual queue (${esc({ no_abstract: 'no abstract', non_english: 'not in English', no_score: 'no classifier score', user: 'set aside' }[r.flag] || r.flag)})` : `Rank ${r.rank} of ${total}`}${opts.showP && r.p != null ? ` · probability ${r.p.toFixed(2)}` : ''}</div>
      <h3>${esc(r.title || '(no title)')}</h3>
      ${meta ? `<div class="small muted">${meta}${r.doi ? ` · <a href="https://doi.org/${esc(r.doi)}" rel="noopener" target="_blank">doi</a>` : ''}</div>` : ''}
      <div class="abstract">${esc(r.abstract || '(no abstract)')}</div>
      <div class="decide">
        <button class="btn good" data-d="include">Include<span class="kbd">I</span></button>
        <button class="btn" data-d="maybe">Maybe<span class="kbd">M</span></button>
        <button class="btn bad" data-d="exclude">Exclude<span class="kbd">E</span></button>
        <button class="btn ghost" data-d="undo">Undo<span class="kbd">U</span></button>
      </div>
      ${opts.extra || ''}
    </article>`;
  }
  function bindDecisions(p, queue, rerender) {
    const act = (d) => {
      try {
        if (d === 'undo') { if (!RF.undo(p)) return; }
        else { const r = RF.nextRecord(p, queue); if (!r) return; RF.decide(p, r.rid, d); }
        save(p); rerender();
      } catch (e) { toast(e.message, 'bad'); }
    };
    $$('[data-d]', view).forEach((b) => b.addEventListener('click', () => act(b.dataset.d)));
    if (keyHandler) document.removeEventListener('keydown', keyHandler);
    keyHandler = (e) => {
      if (e.target.closest('input, textarea') || e.metaKey || e.ctrlKey || e.altKey) return;
      const d = { i: 'include', m: 'maybe', e: 'exclude', u: 'undo' }[e.key.toLowerCase()]; if (d) { e.preventDefault(); act(d); }
    };
    document.addEventListener('keydown', keyHandler);
  }
  function finishBars() { $$('.bar > span[data-w]', view).forEach((s) => { s.style.width = s.dataset.w + '%'; }); }
  let showP = false; try { showP = localStorage.getItem('rf-showp') === '1'; } catch (e) { showP = false; }

  function viewScreen(p) {
    if (!p.ranking) { view.innerHTML = `${p.demo ? '' : head(p, 'screen')}<div class="notice">Score the records and freeze the ranking first.</div>`; return; }
    const r = RF.nextRecord(p), st = RF.projectStop(p), stopped = st.stopped_at != null;
    const top = p.demo ? demoHead(p) : head(p, 'screen');
    let left;
    if (stopped || !r) {
      left = `<div class="card"><h3>${stopped ? 'Ranked list closed' : 'All ranked records screened'}</h3>
        <p class="muted">${stopped ? `You stopped after ${st.stopped_at} of ${p.ranking.length} ranked records; ${p.ranking.length - st.stopped_at} were not screened and are reported as excluded by the stopping rule.` : 'Every ranked record has a decision.'}
        ${st.manual_remaining ? ` ${st.manual_remaining} records in the manual queue still need screening.` : ''}</p>
        ${p.demo ? demoResult(p) : `<div class="btns">${st.manual_remaining ? `<a class="btn primary" href="#/p/${esc(p.id)}/manual">Screen the manual queue</a>` : ''}<a class="btn" href="#/p/${esc(p.id)}/report">Report</a></div>`}
        <div class="btns"><button class="btn ghost" data-d="undo">Undo last decision<span class="kbd">U</span></button></div></div>`;
    } else {
      left = recordCard(r, p.ranking.length, { showP, extra: (p.demo ? demoControls(p, r) : '') +
        `<label class="check small"><input type="checkbox" id="showp" ${showP ? 'checked' : ''}> <span>Show the classifier probability (it can anchor your judgement)</span></label>` });
    }
    view.innerHTML = `${top}<div class="screen"><div>${left}</div><div>${stopPanel(p)}</div></div>`;
    finishBars();
    const rer = () => viewScreen(p);
    bindDecisions(p, 'ranked', rer);
    const sp = $('#showp'); if (sp) sp.addEventListener('change', (e) => { showP = e.target.checked; try { localStorage.setItem('rf-showp', showP ? '1' : '0'); } catch (x) { /* ignore */ } rer(); });
    const sb = $('#stopbtn'); if (sb) sb.addEventListener('click', () => { try { RF.stop(p); save(p, true); rer(); } catch (e) { toast(e.message, 'bad'); } });
    if (p.demo) bindDemo(p, rer);
  }

  function viewManual(p) {
    const r = p.ranking ? RF.nextRecord(p, 'manual') : null, c = RF.counts(p);
    view.innerHTML = `${head(p, 'manual')}${!p.ranking ? '<div class="notice">Freeze the ranking first.</div>' : r ? recordCard(r, 0, { queue: 'manual' }) :
      `<div class="card"><h3>Manual queue done</h3><p class="muted">${c.manual_queue} records screened outside the ranking.</p><a class="btn primary" href="#/p/${esc(p.id)}/report">Report</a></div>`}`;
    if (r) bindDecisions(p, 'manual', () => viewManual(p));
  }

  function viewReport(p) {
    const P = RF.prisma(p), m = RF.methodsText(p);
    const rows = [['Records identified', P.records_identified], ['Duplicates removed', P.duplicates_removed], ['Records after deduplication', P.records_after_deduplication],
      ['Ranked by the classifier', P.records_ranked], ['Screened in full outside the ranking', P.records_for_manual_screening], ['Records screened', P.records_screened],
      ['Not screened after stopping (excluded by the stopping rule)', P.records_not_screened_after_stopping], ['Not yet screened', P.records_not_yet_screened],
      ['Excluded at title and abstract', P.records_excluded], ['Included or maybe (sought for retrieval)', P.records_sought_for_retrieval]];
    view.innerHTML = `${head(p, 'report')}
      <div class="grid g2">
        <div class="card"><h3>PRISMA 2020 counts</h3><table class="t">${rows.map(([k, v]) => `<tr><td>${k}</td><td class="n">${v}</td></tr>`).join('')}</table></div>
        <div class="card"><h3>Downloads</h3>
          <div class="btns"><button class="btn" data-dl="csv">Decisions (CSV)</button><button class="btn" data-dl="ris">Included and maybe (RIS)</button>
          <button class="btn" data-dl="txt">Methods paragraph</button><button class="btn" data-dl="json">Full project (JSON)</button></div>
          <p class="small muted">The project file holds every request, response, score and decision, so the screen can be audited and reopened here later.</p></div>
      </div>
      <div class="card"><h3>Draft methods paragraph</h3><pre id="mt"></pre><button class="btn sm" id="copy">Copy</button></div>`;
    $('#mt').textContent = m;
    $('#copy').addEventListener('click', () => navigator.clipboard.writeText(m).then(() => toast('Copied.', 'good'), () => toast('Copy failed; select the text instead.', 'warn')));
    const base = `reviewfast-${slug(p.title)}`;
    $$('[data-dl]', view).forEach((b) => b.addEventListener('click', () => ({
      csv: () => download(base + '-decisions.csv', RF.decisionsCsv(p), 'text/csv'), ris: () => download(base + '-included.ris', RF.includedRis(p)),
      txt: () => download(base + '-methods.txt', m), json: () => download(base + '.json', JSON.stringify(p), 'application/json'),
    }[b.dataset.dl])()));
  }

  // ---------------- demo (public SYNERGY review, scores stored by the evaluation)
  async function renderDemo() {
    if (!demo) {
      view.innerHTML = '<p class="muted">Loading the demo review...</p>';
      const r = await fetch('demo/attai_2022.json'); if (!r.ok) throw new Error('Could not load the demo data.');
      const d = await r.json();
      const [h, crit] = d.criteria_block.split('\nEligibility criteria reported by the review authors:\n');
      const [t, q] = h.split('\nResearch question: ');
      const p = RF.newProject(t.replace('Systematic review title: ', ''), q, crit, 7);
      p.demo = true; p.id = 'demo'; p.demo_source = d.source;
      RF.importRecords(p, d.records.map((x) => ({ title: x.title, abstract: x.abstract })), 'demo');
      const byTitle = new Map(); d.records.forEach((x) => byTitle.set(x.title + '\u0000' + x.abstract, x));
      p.demo_label = {};
      for (const rec of p.records) { const x = byTitle.get(rec.title + '\u0000' + rec.abstract); p.demo_label[rec.rid] = x ? x.label : 0; if (!rec.flag && x) p.scores[rec.rid] = { p: x.p }; }
      RF.freezeRanking(p); demo = p;
    }
    viewScreen(demo);
  }
  function demoHead(p) {
    const inc = p.ranking.filter((rid) => p.demo_label[rid]).length;
    return `<h1>Demo: screen a real review</h1>
      <p class="lead">${esc(p.title)}</p>
      <p class="muted small">${p.ranking.length} ranked records (${inc} included by the review authors), ranked with the Jev probabilities stored in the published evaluation, so no key is needed. ${RF.counts(p).manual_queue} records not in English sit in the manual queue. Source: ${esc(p.demo_source)}.</p>
      <div class="notice">Screen a few records yourself, or let the review's own decisions play out and watch the stopping criterion. Nothing is sent anywhere.</div>`;
  }
  function demoControls(p, r) {
    return `<div class="notice small"><b>Demo helpers.</b> <button class="btn sm" id="reveal">Show the review's decision</button> <span id="rev"></span>
      <div class="btns"><button class="btn sm" data-auto="1">Use the review's decision for this record</button><button class="btn sm" data-auto="50">Next 50</button><button class="btn sm primary" data-auto="all">Until the criterion is met</button><button class="btn sm ghost" id="reset">Restart</button></div></div>`;
  }
  function demoResult(p) {
    const st = RF.projectStop(p), k = st.stopped_at != null ? st.stopped_at : st.screened;
    const seen = p.ranking.slice(0, k), inc = p.ranking.filter((rid) => p.demo_label[rid]).length, found = seen.filter((rid) => p.demo_label[rid]).length;
    return `<div class="grid g3"><div class="card flat"><div class="stat">${pct(k, p.ranking.length)}</div><div class="stat-label">of ranked records read (${k} of ${p.ranking.length})</div></div>
      <div class="card flat"><div class="stat">${found} / ${inc}</div><div class="stat-label">included studies found (${pct(found, inc)} recall)</div></div>
      <div class="card flat"><div class="stat">${p.ranking.length - k}</div><div class="stat-label">records you did not need to read</div></div></div>
      <div class="btns"><button class="btn" id="reset">Restart the demo</button><a class="btn primary" href="#/projects">Screen your own records</a></div>`;
  }
  function bindDemo(p, rer) {
    const rv = $('#reveal'); if (rv) rv.addEventListener('click', () => { const r = RF.nextRecord(p); $('#rev').textContent = r ? (p.demo_label[r.rid] ? 'Included by the review authors.' : 'Excluded by the review authors.') : ''; });
    $$('[data-auto]', view).forEach((b) => b.addEventListener('click', () => {
      const lim = b.dataset.auto === 'all' ? Infinity : Number(b.dataset.auto);
      for (let i = 0; i < lim; i++) {
        const r = RF.nextRecord(p); if (!r) break;
        const st = RF.decide(p, r.rid, p.demo_label[r.rid] ? 'include' : 'exclude');
        if (st.met) { if (b.dataset.auto === 'all') RF.stop(p); break; }
      }
      rer();
    }));
    const rs = $('#reset'); if (rs) rs.addEventListener('click', () => { p.decisions = []; p.stopped_at = null; p._ph = []; rer(); });
  }

  // ---------------- Python and MCP
  function renderUse() {
    view.innerHTML = `<h1>Python and MCP</h1>
      <p class="lead">The same workflow is available as a Python package with a local web app and a command line, and as an MCP server that lets an AI assistant run the steps while you make the screening decisions.</p>
      <div class="grid g2">
        <div class="card"><h3>Python package and local app</h3>
          <pre>pip install "git+https://github.com/taehojo/reviewfast"
reviewfast serve        # opens http://127.0.0.1:8765</pre>
          <p class="muted small">Projects are SQLite files on your computer. Command line: <code>reviewfast new | import | score | rank | status | export</code>. Set <code>AI_GATEWAY_API_KEY</code> for scoring.</p></div>
        <div class="card"><h3>MCP server</h3>
          <pre>pip install "reviewfast[mcp] @ git+https://github.com/taehojo/reviewfast"</pre>
          <p class="muted small">Add it to your MCP client (for example Claude Desktop or Claude Code):</p>
          <pre>{
  "mcpServers": {
    "reviewfast": {
      "command": "reviewfast-mcp",
      "env": { "AI_GATEWAY_API_KEY": "your key" }
    }
  }
}</pre>
          <p class="muted small">Tools: create_project, import_records, estimate_scoring_cost, score_records, freeze_ranking, next_record, record_decision, undo_last_decision, stop_screening, export_results, project_status and check_stopping (the criterion alone, for any ranked screen). The server tells the assistant that inclusion decisions must come from you.</p></div>
      </div>
      <div class="card"><h3>Source and validation</h3><p class="muted">Source code, tests and the reproduction of the evaluation's stopping results are on <a href="https://github.com/taehojo/reviewfast" rel="noopener">GitHub</a> (Apache-2.0). The browser version's stopping criterion matches the Python package and buscarpy 0.0.2 to within 1e-13.</p></div>`;
  }

  DB.open().then(route);
})();
