'use strict';

// ── Config ──────────────────────────────────────────────────────────────
const SESSION_KEY = 'default';
const BASE = window.location.pathname.replace(/\/+$/, '');
const api = (path) => `${BASE}${path}`;
const withBase = (path) => String(path || '').startsWith('/') ? `${BASE}${path}` : path;

// ── Utilities ────────────────────────────────────────────────────────────
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), type === 'error' ? 6000 : 3000);
}

function skeleton(rows = 3) {
  return Array.from({length: rows}, (_, i) =>
    `<div class="sk-row" style="width:${65 + (i % 3) * 12}%"></div>`
  ).join('');
}

function rel(iso) {
  if (!iso) return '—';
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

async function apiFetch(path, opts = {}) {
  const res = await fetch(api(path), opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({detail: res.statusText}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// ── Section switching ─────────────────────────────────────────────────────
const _loaded = new Set();

function showSection(id) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
  const sec = document.getElementById(id);
  if (sec) sec.classList.add('active');
  const nav = document.querySelector(`[data-section="${id}"]`);
  if (nav) nav.classList.add('active');
  if (id === 'sec-documents') { refreshDocuments(); }
  else if (!_loaded.has(id)) { _loaded.add(id); loadSection(id); }
}

function toggleAdminGroup(el) {
  const extras = document.getElementById('admin-extras');
  if (!extras) return;
  const open = extras.style.display !== 'none';
  extras.style.display = open ? 'none' : 'block';
  if (el) el.textContent = open ? '▸ More…' : '▾ Less';
}

function loadSection(id) {
  ({
    'sec-command':   refreshOps,
    'sec-admin':     refreshAdmin,
    'sec-autoapply': refreshAutoApply,
    'sec-queue':     refreshQueue,
    'sec-takeover':  refreshWorkflow,
    'sec-health':    refreshHealth,
    'sec-alerts':    refreshAlerts,
    'sec-policy':    refreshPolicy,
    'sec-scheduler': refreshScheduler,
    'sec-profiles':  refreshProfiles,
    'sec-remediation': refreshRemediation,
    'sec-timeline':  refreshTimeline,
    'sec-live': () => { refreshLiveMeta(); startScreenshotStream(); },
    'sec-funnel':    refreshPipelineFunnel,
    'sec-jobs':      () => { loadJobCounts(); refreshPipelineJobs(); },
    'sec-headless':  refreshHeadlessSection,
    'sec-documents': refreshDocuments,
    'sec-logs':      refreshLogs,
    'sec-settings':  refreshSettings,
  })[id]?.();
}

// ── Command Center ─────────────────────────────────────────────────────────
async function refreshOps() {
  document.getElementById('ops-body').innerHTML = skeleton();
  try {
    const d = await apiFetch('/api/ops/command-center');
    const states = (d.active_platform_states || []).map(s =>
      `<span class="badge b-info">${s.platform}:${s.state}</span>`
    ).join(' ') || '<span class="badge b-ok">nominal</span>';
    const actions = (d.latest_operator_actions || []).slice(0, 3).map(a =>
      `<div class="tl-detail">${a.event_type} by ${a.actor} — ${rel(a.created_at)}</div>`
    ).join('') || '<div class="tl-detail">No recent actions</div>';
    const qTiles = Object.entries(d.queue_counts || {}).map(([k, v]) =>
      `<div class="stat-tile"><div class="stat-val v-slate">${v}</div><div class="stat-label">${k.replace(/_/g,' ')}</div></div>`
    ).join('');
    document.getElementById('ops-body').innerHTML = `
      <div class="stat-grid" style="margin-bottom:.75rem">
        <div class="stat-tile"><div class="stat-val v-red">${d.critical_alerts_count ?? 0}</div><div class="stat-label">Critical alerts</div></div>
        ${qTiles}
      </div>
      <div style="margin-bottom:.5rem">${states}</div>
      ${actions}`;
  } catch (e) { toast(e.message, 'error'); }
}

async function approveCheckpoint() {
  try {
    await apiFetch('/api/policy/checkpoint', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({approved:true, actor:'dashboard'})});
    toast('Checkpoint approved', 'success');
    if (_loaded.has('sec-policy')) refreshPolicy();
    if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) { toast(e.message, 'error'); }
}

// ── Auto-Apply (scheduled) ─────────────────────────────────────────────────
async function refreshAutoApply() {
  const el = document.getElementById('autoapply-panel');
  el.innerHTML = skeleton();
  try {
    const [cfg, st] = await Promise.all([apiFetch('/api/schedule'), apiFetch('/api/schedule/status')]);
    const lr = st.last_result;
    const lastLine = lr
      ? `${lr.ran ? (lr.applied_planned > 0 ? `planned ${lr.applied_planned} application(s)` : (lr.reason || 'ran')) : (lr.reason || 'skipped')} — ${rel(lr.at)}`
      : 'never';
    el.innerHTML = `
      <div class="settings-row" style="justify-content:space-between;align-items:center">
        <div>
          <div style="font-weight:600">Scheduled auto-apply</div>
          <div class="muted-label">Applies up to a set number of jobs per run, on a schedule, within your daily cap.</div>
        </div>
        <label class="toggle-switch">
          <input type="checkbox" id="aa-enabled" ${cfg.enabled ? 'checked' : ''} onchange="saveAutoApply()">
          <span class="toggle-slider"></span>
        </label>
      </div>
      <div class="settings-row"><label class="settings-field-label">Jobs per run</label>
        <input id="aa-jobs" type="number" min="0" max="100" class="settings-input" style="width:90px" value="${cfg.jobs_per_run}" onchange="saveAutoApply()"></div>
      <div class="settings-row"><label class="settings-field-label">Run every (min)</label>
        <input id="aa-interval" type="number" min="5" max="1440" class="settings-input" style="width:90px" value="${cfg.interval_minutes}" onchange="saveAutoApply()"></div>
      <div class="settings-row"><label class="settings-field-label">Active window (${cfg.timezone})</label>
        <input id="aa-start" type="time" class="settings-input" style="width:130px" value="${cfg.window_start}" onchange="saveAutoApply()">
        <span style="margin:0 .4rem">to</span>
        <input id="aa-end" type="time" class="settings-input" style="width:130px" value="${cfg.window_end}" onchange="saveAutoApply()"></div>
      <div class="stat-grid" style="margin:.75rem 0">
        <div class="stat-tile"><div class="stat-val v-blue">${st.applied_today}/${st.daily_cap}</div><div class="stat-label">Applied today / cap</div></div>
        <div class="stat-tile"><div class="stat-val v-amber">${st.available_tailored}</div><div class="stat-label">Ready to apply</div></div>
        <div class="stat-tile"><div class="stat-val v-slate">${st.jobs_per_run}</div><div class="stat-label">Jobs / run</div></div>
        <div class="stat-tile"><div class="stat-val ${st.enabled ? 'v-green' : 'v-slate'}">${st.enabled ? 'ON' : 'OFF'}</div><div class="stat-label">Schedule</div></div>
      </div>
      <div class="muted-label" style="font-size:.8rem">Last cycle: ${lastLine}</div>
      <div class="muted-label" style="font-size:.78rem;margin-top:.4rem">
        Submissions still require your standing approval — a valid human checkpoint and an auto-submit policy.
        Supervised entries always stop before submit.
      </div>
      <div class="btn-group" style="margin-top:.75rem">
        <button class="btn btn-slate btn-sm" onclick="refreshAutoApply()">↺ Refresh</button>
      </div>`;
    await refreshCalendar();
    await refreshApplyUrlCard();
  } catch (e) { el.innerHTML = `<div class="muted-label">${e.message}</div>`; }
}

// ── Weekly calendar ─────────────────────────────────────────────────────────
async function refreshCalendar() {
  let host = document.getElementById('calendar-card');
  if (!host) {
    host = document.createElement('div');
    host.className = 'card';
    host.id = 'calendar-card';
    host.style.marginTop = '1rem';
    document.getElementById('sec-autoapply').querySelector('.card').after(host);
  }
  host.innerHTML = skeleton();
  try {
    const d = await apiFetch('/api/calendar');
    const days = d.day_names, plats = d.platforms, modes = d.modes;
    const rows = (d.entries || []).map(e => {
      const modeBadge = e.mode === 'automatic'
        ? '<span class="badge b-amber">automatic</span>'
        : '<span class="badge b-info">supervised</span>';
      return `<tr>
        <td>${days[e.day_of_week]}</td>
        <td>${e.time_hhmm}</td>
        <td>${e.platform}</td>
        <td>${e.jobs_per_run}</td>
        <td>${modeBadge}</td>
        <td><input type="checkbox" ${e.enabled ? 'checked' : ''} onchange="toggleCalEntry(${e.id}, this.checked)"></td>
        <td style="white-space:nowrap">
          <button class="btn btn-blue btn-sm" onclick="runCalEntry(${e.id})">▶ Run</button>
          <button class="btn btn-slate btn-sm" style="background:#7f1d1d;color:#fff" onclick="deleteCalEntry(${e.id})">🗑</button>
        </td></tr>`;
    }).join('') || '<tr><td colspan="7" class="muted-label">No scheduled days yet. Add one below.</td></tr>';

    host.innerHTML = `
      <div class="card-label">📅 Weekly schedule <span class="muted-label">(times in ${d.timezone})</span></div>
      <div class="muted-label" style="font-size:.78rem;margin-bottom:.6rem">
        e.g. Monday 09:00 → LinkedIn · Tuesday 13:00 → Indeed · Friday 17:00 → Glassdoor. Each entry runs that platform, applies up to its jobs/run, in supervised or automatic mode.
      </div>
      <table class="data-table" style="width:100%">
        <thead><tr><th>Day</th><th>Time</th><th>Platform</th><th>Jobs/run</th><th>Mode</th><th>On</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="btn-group" style="margin-top:.75rem;flex-wrap:wrap;align-items:end">
        <label>Day <select id="cal-day">${days.map((n, i) => `<option value="${i}">${n}</option>`).join('')}</select></label>
        <label>Time <input id="cal-time" type="time" value="09:00"></label>
        <label>Platform <select id="cal-platform">${plats.map(p => `<option value="${p}">${p}</option>`).join('')}</select></label>
        <label>Jobs/run <input id="cal-jobs" type="number" min="0" max="100" value="5" style="width:70px"></label>
        <label>Mode <select id="cal-mode">${modes.map(m => `<option value="${m}">${m}</option>`).join('')}</select></label>
        <button class="btn btn-green" onclick="addCalEntry()">＋ Add to schedule</button>
      </div>`;
  } catch (e) { host.innerHTML = `<div class="muted-label">${e.message}</div>`; }
}

async function addCalEntry() {
  const body = {
    day_of_week: parseInt(document.getElementById('cal-day').value),
    time_hhmm: document.getElementById('cal-time').value,
    platform: document.getElementById('cal-platform').value,
    jobs_per_run: parseInt(document.getElementById('cal-jobs').value),
    mode: document.getElementById('cal-mode').value,
  };
  try {
    await apiFetch('/api/calendar', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    toast('Schedule entry added', 'success');
    refreshCalendar();
  } catch (e) { toast(e.message, 'error'); }
}

async function toggleCalEntry(id, enabled) {
  try { await apiFetch(`/api/calendar/${id}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled})}); }
  catch (e) { toast(e.message, 'error'); }
}

async function deleteCalEntry(id) {
  try { await apiFetch(`/api/calendar/${id}`, {method:'DELETE'}); toast('Entry removed', 'success'); refreshCalendar(); }
  catch (e) { toast(e.message, 'error'); }
}

async function runCalEntry(id) {
  try { const r = await apiFetch(`/api/calendar/${id}/run-now`, {method:'POST'}); toast(`Running ${r.platform} — planning ${r.applied_planned} application(s)`, 'success'); }
  catch (e) { toast(e.message, 'error'); }
}

// ── Apply by URL ────────────────────────────────────────────────────────────
async function refreshApplyUrlCard() {
  let host = document.getElementById('apply-url-card');
  if (!host) {
    host = document.createElement('div');
    host.className = 'card';
    host.id = 'apply-url-card';
    host.style.marginTop = '1rem';
    document.getElementById('calendar-card').after(host);
  }
  host.innerHTML = `
    <div class="card-label">🔗 Apply to a specific job by URL</div>
    <div class="muted-label" style="font-size:.78rem;margin-bottom:.6rem">
      Paste a job link you found. It's added, tailored, and run through apply. Supervised stops before submit; automatic submits (still gated by your daily cap and checkpoint).
    </div>
    <div style="display:flex;gap:.5rem;flex-wrap:wrap">
      <input id="apply-url-input" type="url" placeholder="https://…/jobs/view/…" style="flex:1;min-width:240px">
      <select id="apply-url-mode"><option value="supervised">supervised</option><option value="automatic">automatic</option></select>
      <button class="btn btn-blue" onclick="submitApplyUrl()">Add &amp; Apply</button>
    </div>
    <div id="apply-url-result" class="muted-label" style="font-size:.78rem;margin-top:.4rem"></div>`;
}

async function submitApplyUrl() {
  const url = document.getElementById('apply-url-input').value.trim();
  const mode = document.getElementById('apply-url-mode').value;
  if (!url) { toast('Enter a job URL', 'error'); return; }
  try {
    const r = await apiFetch('/api/jobs/apply-url', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url, mode})});
    if (r.error) { toast(r.error, 'error'); return; }
    document.getElementById('apply-url-result').textContent = `Added “${r.title || url}” — ${mode} run started.`;
    toast('Apply run started', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function saveAutoApply() {
  const body = {
    enabled: document.getElementById('aa-enabled').checked,
    jobs_per_run: parseInt(document.getElementById('aa-jobs').value),
    interval_minutes: parseInt(document.getElementById('aa-interval').value),
  };
  const start = document.getElementById('aa-start').value;
  const end = document.getElementById('aa-end').value;
  if (start) body.window_start = start;
  if (end) body.window_end = end;
  try {
    await apiFetch('/api/schedule', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    toast('Schedule saved', 'success');
    refreshAutoApply();
  } catch (e) { toast(e.message, 'error'); }
}

async function runAutoApplyNow() {
  try {
    const r = await apiFetch('/api/schedule/run-now', {method:'POST'});
    toast(r.applied_planned > 0 ? `Started — planning ${r.applied_planned} application(s)` : (r.reason || 'Ran'), 'success');
    refreshAutoApply();
  } catch (e) { toast(e.message, 'error'); }
}

// ── Queue ──────────────────────────────────────────────────────────────────
async function refreshQueue() {
  document.getElementById('queue-tiles').innerHTML = skeleton(1);
  try {
    const d = await apiFetch('/api/queue/snapshot');
    const colors = {pending:'v-amber', in_progress:'v-blue', done:'v-green', failed:'v-red'};
    document.getElementById('queue-tiles').innerHTML = Object.entries(d).map(([k, v]) =>
      `<div class="stat-tile"><div class="stat-val ${colors[k]||'v-slate'}">${v}</div><div class="stat-label">${k.replace(/_/g,' ')}</div></div>`
    ).join('');
  } catch (e) { toast(e.message, 'error'); }
  refreshQueuePendingQuestions();
}

async function refreshQueuePendingQuestions() {
  const body = document.getElementById('queue-pending-body');
  const saveRow = document.getElementById('queue-pending-save-row');
  const countEl = document.getElementById('pending-q-count');
  if (!body) return;
  try {
    const d = await apiFetch('/api/apply/pending-questions');
    const questions = d.questions || [];
    if (countEl) countEl.textContent = questions.length ? `(${questions.length})` : '';

    if (questions.length === 0) {
      body.innerHTML = '<div class="muted-label">No unanswered questions — all clear.</div>';
      if (saveRow) saveRow.style.display = 'none';
      return;
    }

    // Group by company+job for cleaner display
    const byJob = new Map();
    questions.forEach(q => {
      const key = `${q.job_id}`;
      if (!byJob.has(key)) byJob.set(key, {title: q.job_title, company: q.company, questions: []});
      byJob.get(key).questions.push(q);
    });

    const rows = [...byJob.values()].map(group => {
      const qRows = group.questions.map(q => {
        const input = q.options && q.options.length
          ? `<select data-qkey="${encodeURIComponent(q.label)}"
               style="flex:1;background:var(--surface-2);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:.25rem .4rem;font-size:.82rem">
               <option value="">— select —</option>
               ${q.options.map(o => `<option value="${o}">${o}</option>`).join('')}
             </select>`
          : `<input type="text" data-qkey="${encodeURIComponent(q.label)}" placeholder="Your answer…"
               style="flex:1;background:var(--surface-2);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:.25rem .4rem;font-size:.82rem">`;
        return `<div style="display:flex;gap:.5rem;align-items:center;padding:.3rem 0;border-top:1px solid var(--border)">
          <div style="flex:0 0 45%;font-size:.8rem">${_esc(q.label)}</div>
          ${input}
        </div>`;
      }).join('');

      return `<div style="margin-bottom:.75rem">
        <div style="font-size:.82rem;font-weight:600;margin-bottom:.25rem">${_esc(group.title)} <span class="muted-label">@ ${_esc(group.company)}</span></div>
        ${qRows}
      </div>`;
    }).join('');

    body.innerHTML = rows;
    if (saveRow) saveRow.style.display = '';
  } catch (e) {
    body.innerHTML = `<div class="muted-label">Error: ${e.message}</div>`;
  }
}

async function saveQueuePendingAnswers() {
  const inputs = document.querySelectorAll('#queue-pending-body [data-qkey]');
  const answers = {};
  inputs.forEach(el => {
    const label = decodeURIComponent(el.getAttribute('data-qkey'));
    const val = el.value.trim();
    if (val) answers[label] = val;
  });
  if (!Object.keys(answers).length) { toast('Fill in at least one answer first', 'warning'); return; }
  try {
    await apiFetch('/api/apply/pending-questions/answer', {method:'POST', body: JSON.stringify({answers})});
    toast(`${Object.keys(answers).length} answers saved`, 'success');
    refreshQueuePendingQuestions();
  } catch (e) { toast(e.message, 'error'); }
}

// ── Takeover / Resume ──────────────────────────────────────────────────────
function selectedPlatform() { return document.getElementById('platformSelect')?.value || 'linkedin'; }

async function refreshWorkflow() {
  document.getElementById('workflowState').innerHTML = skeleton(1);
  const platform = selectedPlatform();
  try {
    const [wf, rs] = await Promise.all([
      apiFetch(`/api/sessions/${SESSION_KEY}/workflows/${platform}`),
      apiFetch(`/api/sessions/${SESSION_KEY}/resume-step/${platform}`),
    ]);
    document.getElementById('workflowState').innerHTML =
      `<span class="badge b-info">${wf.state}</span> <span class="tl-detail">reason: ${wf.reason_code||'—'} · ${rel(wf.updated_at)}</span>`;
    document.getElementById('resumeStepSelect').value = rs.resume_step;
    document.getElementById('resumeStepState').textContent = `Resume step: ${rs.resume_step} — by ${rs.updated_by||'—'}`;
    const errEl = document.getElementById('workflowError');
    errEl.textContent = '';
    errEl.style.display = 'none';
  } catch (e) { toast(e.message, 'error'); }
}

async function requestTakeover() {
  try {
    await apiFetch(`/api/sessions/${SESSION_KEY}/workflows/${selectedPlatform()}/request-takeover`,
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reason_code: document.getElementById('reasonSelect').value})});
    toast('Takeover requested', 'success');
    refreshWorkflow(); if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) { toast(e.message, 'error'); }
}

async function markInTakeover() {
  try {
    await apiFetch(`/api/sessions/${SESSION_KEY}/workflows/${selectedPlatform()}`,
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({state:'in_takeover', reason_code: document.getElementById('reasonSelect').value})});
    toast('Marked in takeover', 'success'); refreshWorkflow();
  } catch (e) { toast(e.message, 'error'); }
}

async function pauseSession() {
  try {
    await apiFetch(`/api/sessions/${SESSION_KEY}/workflows/${selectedPlatform()}`,
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({state:'paused'})});
    toast('Session paused', 'success'); refreshWorkflow(); if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) { toast(e.message, 'error'); }
}

async function resumeSession() {
  try {
    await apiFetch(`/api/sessions/${SESSION_KEY}/workflows/${selectedPlatform()}/resume`,
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({actor:'dashboard'})});
    toast('Session resumed', 'success'); refreshWorkflow(); if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) {
    const errEl = document.getElementById('workflowError');
    errEl.textContent = e.message; errEl.style.display = '';
    toast(e.message, 'error');
  }
}

async function setResumeStep() {
  try {
    await apiFetch(`/api/sessions/${SESSION_KEY}/resume-step/${selectedPlatform()}`,
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({resume_step: document.getElementById('resumeStepSelect').value, actor:'dashboard'})});
    toast('Resume step set', 'success'); refreshWorkflow(); if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) { toast(e.message, 'error'); }
}

// ── Session Health ─────────────────────────────────────────────────────────
async function refreshHealth() {
  document.getElementById('healthPanel').innerHTML = skeleton();
  try {
    const d = await apiFetch('/api/browser/health');
    // Only show platforms that have actually been used — "critical" badges for
    // never-touched platforms (indeed, glassdoor) are noise, not signal.
    const active = (d.platforms || []).filter(p => p.last_used_at || p.active);
    const rows = active.length ? active.map(p =>
      `<tr><td>${p.platform}</td><td><span class="badge b-${p.severity}">${p.severity}</span></td><td>${(p.warnings||[]).map(w=>`<code>${w.code}</code>`).join(' ')||'—'}</td></tr>`
    ).join('') : `<tr><td colspan="3" style="color:var(--muted);font-style:italic">No active browser sessions. Run a discover or apply stage to populate.</td></tr>`;
    document.getElementById('healthPanel').innerHTML = `
      <div style="margin-bottom:.6rem;font-size:.82rem;color:var(--muted)">checkpoint_failures_24h: ${d.checkpoint_failures_24h??0}</div>
      <table><thead><tr><th>Platform</th><th>Severity</th><th>Warnings</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) { toast(e.message, 'error'); }
}

// ── Alerts ─────────────────────────────────────────────────────────────────
let _alertFilter = 'new';

async function refreshAlerts(status) {
  if (status !== undefined) _alertFilter = status;
  document.getElementById('alerts-list').innerHTML = skeleton();
  document.querySelectorAll('#alerts-filters .filter-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.status === _alertFilter)
  );
  try {
    const param = _alertFilter === 'all' ? '' : `&status=${_alertFilter}`;
    const d = await apiFetch(`/api/alerts?limit=30${param}`);
    if (!(d.alerts||[]).length) {
      document.getElementById('alerts-list').innerHTML = '<p style="color:var(--dim);font-size:.85rem">No alerts.</p>';
      return;
    }
    document.getElementById('alerts-list').innerHTML = d.alerts.map(a => {
      const col = a.severity==='critical'?'#fca5a5':a.severity==='warning'?'#fde68a':'#93c5fd';
      return `
        <div class="alert-card ${a.severity}">
          <div class="alert-header">
            <span class="alert-type" style="color:${col}">${a.alert_type} · ${a.platform||a.meta?.platform||'all'}</span>
            <span class="alert-age">${rel(a.last_seen_at)}</span>
          </div>
          <div class="alert-message">${a.message}</div>
          ${(a.occurrence_count||1)>1?`<div style="font-size:.72rem;color:var(--dim);margin-top:.2rem">×${a.occurrence_count} occurrences</div>`:''}
          <div class="alert-actions">
            ${a.status==='new'?`<button class="btn-outline btn-sm" onclick="ackAlert(${a.id})">Ack</button>`:''}
            ${a.status!=='resolved'?`<button class="btn-green btn-sm" onclick="resolveAlert(${a.id})">Resolve</button>`:''}
            <span class="badge b-${a.status}" style="margin-left:auto">${a.status}</span>
          </div>
        </div>`;
    }).join('');
  } catch (e) { toast(e.message, 'error'); }
}

async function ackAlert(id) {
  try {
    await apiFetch(`/api/alerts/${id}/ack`, {method:'POST'});
    toast('Alert acknowledged', 'success'); refreshAlerts();
  } catch (e) { toast(e.message, 'error'); }
}

async function resolveAlert(id) {
  try {
    await apiFetch(`/api/alerts/${id}/resolve`, {method:'POST'});
    toast('Alert resolved', 'success'); refreshAlerts();
  } catch (e) { toast(e.message, 'error'); }
}

async function refreshAlertBadge() {
  try {
    const d = await apiFetch('/api/alerts?status=new&limit=1');
    const badge = document.getElementById('alerts-badge');
    if (!badge) return;
    const total = d.total ?? (d.alerts||[]).length;
    badge.textContent = total;
    badge.style.display = total > 0 ? 'inline' : 'none';
  } catch (_) {}
}

// ── Guardrail Policy ───────────────────────────────────────────────────────
async function refreshPolicy() {
  document.getElementById('policy-tiles').innerHTML = skeleton(1);
  try {
    const st = await apiFetch('/api/policy/status');
    const cap = st.settings.daily_apply_cap;
    const used = st.today_apply_count;
    const remaining = st.remaining_apply_quota ?? (cap - used);
    const pct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : 0;
    const barColor = pct >= 90 ? '#ef4444' : pct >= 60 ? '#f59e0b' : '#22c55e';
    document.getElementById('policy-cap-bar').innerHTML = `
      <div style="display:flex;align-items:center;gap:.6rem;margin-bottom:.3rem">
        <span style="color:var(--muted);font-size:.8rem">Daily quota</span>
        <span style="font-size:.85rem;font-weight:600">${used} / ${cap}</span>
        <span style="color:var(--dim);font-size:.78rem">(${remaining} remaining)</span>
      </div>
      <div style="height:6px;background:var(--border);border-radius:3px;overflow:hidden">
        <div style="height:100%;width:${pct}%;background:${barColor};border-radius:3px;transition:width .4s"></div>
      </div>`;
    document.getElementById('policy-tiles').innerHTML = `
      <div class="stat-tile"><div class="stat-val ${st.checkpoint_valid?'v-green':'v-red'}">${st.checkpoint_valid?'✓':'✗'}</div><div class="stat-label">Checkpoint</div></div>
      <div class="stat-tile"><div class="stat-val v-slate">${st.settings.min_seconds_between_actions}s</div><div class="stat-label">Min between actions</div></div>`;
    document.getElementById('capInput').value = cap;
    document.getElementById('secInput').value = st.settings.min_seconds_between_actions;
  } catch (e) { toast(e.message, 'error'); }
}

async function savePolicy() {
  try {
    await apiFetch('/api/policy/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({daily_apply_cap: Number(document.getElementById('capInput').value), min_seconds_between_actions: Number(document.getElementById('secInput').value), actor:'dashboard'})});
    toast('Policy saved', 'success'); refreshPolicy(); if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) { toast(e.message, 'error'); }
}

// ── Scheduler ──────────────────────────────────────────────────────────────
async function refreshScheduler() {
  document.getElementById('schedulerPanel').innerHTML = skeleton();
  try {
    const [sched, pacing] = await Promise.all([apiFetch('/api/scheduler'), apiFetch('/api/pacing/presets')]);
    const byP = Object.fromEntries((pacing.platforms||[]).map(p => [p.platform, p]));
    const rows = (sched.platforms||[]).map(p => {
      const pp = byP[p.platform];
      const opts = (pacing.preset_options||[]).map(o =>
        `<option value="${o}" ${pp?.preset===o?'selected':''}>${o}</option>`
      ).join('');
      return `<tr>
        <td>${p.platform}</td>
        <td><input type="checkbox" ${p.paused?'checked':''} onchange="saveScheduler('${p.platform}',this.checked)" /></td>
        <td><input id="ws-${p.platform}" value="${p.window_start}" size="5" /></td>
        <td><input id="we-${p.platform}" value="${p.window_end}" size="5" /></td>
        <td><select onchange="savePreset('${p.platform}',this.value)">${opts}</select></td>
        <td style="font-size:.75rem;color:var(--muted)">${pp?`${pp.policy_knobs.min_seconds_between_actions}s / cap ${pp.policy_knobs.daily_apply_cap}`:'—'}</td>
        <td><button class="btn-outline btn-sm" onclick="saveSchedulerWindow('${p.platform}')">Save</button></td>
      </tr>`;
    }).join('');
    document.getElementById('schedulerPanel').innerHTML = `
      <table><thead><tr><th>Platform</th><th>Paused</th><th>Start</th><th>End</th><th>Pacing</th><th>Knobs</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  } catch (e) { toast(e.message, 'error'); }
}

async function saveScheduler(platform, paused) {
  try {
    await apiFetch(`/api/scheduler/${platform}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({paused, note:'dashboard toggle', actor:'dashboard'})});
    toast(`${platform} ${paused?'paused':'resumed'}`, 'success');
    if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) { toast(e.message, 'error'); }
}

async function saveSchedulerWindow(platform) {
  try {
    await apiFetch(`/api/scheduler/${platform}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({window_start: document.getElementById(`ws-${platform}`).value, window_end: document.getElementById(`we-${platform}`).value, note:'dashboard window update', actor:'dashboard'})});
    toast(`${platform} window saved`, 'success'); if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) { toast(e.message, 'error'); }
}

async function savePreset(platform, preset) {
  try {
    await apiFetch(`/api/pacing/presets/${platform}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({preset, actor:'dashboard', note:'dashboard preset update'})});
    toast(`${platform} preset → ${preset}`, 'success'); if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) { toast(e.message, 'error'); }
}

// ── Browser Profiles ───────────────────────────────────────────────────────
async function refreshProfiles() {
  document.getElementById('profiles').innerHTML = skeleton();
  try {
    const d = await apiFetch('/api/browser/profiles');
    // Show only profiles that have actually been used — never-touched browser
    // profiles flagged "critical" by default are noise, not signal.
    const profiles = (d.profiles||[]).filter(p => p.last_used_at || p.active);
    const rows = profiles.length ? profiles.map(p => {
      const sev = (p.health && p.health.severity) || 'info';
      return `<tr><td>${p.platform}</td><td><code>${p.profile_path}</code></td><td>${p.active?'✓':'—'}</td><td>${p.status}</td><td><span class="badge b-${sev}">${sev}</span></td></tr>`;
    }).join('') : `<tr><td colspan="5" style="color:var(--muted);font-style:italic">No active browser profiles yet. Launch one from Live View → Live Control to populate.</td></tr>`;
    document.getElementById('profiles').innerHTML = `
      <table><thead><tr><th>Platform</th><th>Profile Path</th><th>Active</th><th>Status</th><th>Health</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  } catch (e) { toast(e.message, 'error'); }
}

// ── Remediation ────────────────────────────────────────────────────────────
async function refreshRemediation() {
  const alertType = document.getElementById('remAlertType').value;
  const scope = document.getElementById('remScope').value;
  document.getElementById('remediationStatus').innerHTML = skeleton(2);
  document.getElementById('remediationRuns').innerHTML = skeleton();
  try {
    const pol = await apiFetch(`/api/remediation/policies?alert_type=${encodeURIComponent(alertType)}&platform_scope=${encodeURIComponent(scope)}`);
    const policy = (pol.policies||[])[0];
    if (policy) {
      document.getElementById('remEnabled').checked = !!policy.enabled;
      document.getElementById('remCooldown').value = policy.cooldown_minutes;
      document.getElementById('remMaxDay').value = policy.max_actions_per_day;
      document.getElementById('remediationStatus').innerHTML = `<pre>${JSON.stringify(policy, null, 2)}</pre>`;
    } else {
      document.getElementById('remediationStatus').innerHTML = '<p style="color:var(--dim);font-size:.82rem">No policy saved yet.</p>';
    }
    const runs = await apiFetch('/api/remediation/runs?limit=10');
    const rows = (runs.runs||[]).map(r =>
      `<tr><td>${rel(r.created_at)}</td><td>${r.alert_type}</td><td>${r.platform_scope}</td><td><span class="badge b-${r.status==='success'||r.status==='executed'?'ok':'warning'}">${r.status}</span></td><td>${r.dry_run?'dry':'live'}</td><td>${r.notes||'—'}</td></tr>`
    ).join('');
    document.getElementById('remediationRuns').innerHTML = rows
      ? `<table style="margin-top:.75rem"><thead><tr><th>When</th><th>Alert</th><th>Scope</th><th>Status</th><th>Mode</th><th>Notes</th></tr></thead><tbody>${rows}</tbody></table>`
      : '<p style="color:var(--dim);font-size:.82rem;margin-top:.5rem">No runs yet.</p>';
  } catch (e) { toast(e.message, 'error'); }
}

async function saveRemediationPolicy() {
  try {
    await apiFetch('/api/remediation/policies', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
      alert_type: document.getElementById('remAlertType').value,
      platform_scope: document.getElementById('remScope').value,
      enabled: document.getElementById('remEnabled').checked,
      cooldown_minutes: Number(document.getElementById('remCooldown').value),
      max_actions_per_day: Number(document.getElementById('remMaxDay').value),
      actor: 'dashboard',
    })});
    toast('Remediation policy saved', 'success'); refreshRemediation();
  } catch (e) { toast(e.message, 'error'); }
}

// ── Operator Timeline ──────────────────────────────────────────────────────
const EVENT_BADGE = {
  takeover_state_change:'b-info', checkpoint_approved:'b-ok', checkpoint_failed:'b-critical',
  policy_updated:'b-info', scheduler_updated:'b-info', resume_step_updated:'b-info',
  pacing_preset_updated:'b-info', browser_launched:'b-ok', browser_stopped:'b-slate',
  remediation_action:'b-warning',
};

async function refreshTimeline() {
  document.getElementById('timelinePanel').innerHTML = skeleton();
  try {
    const d = await apiFetch('/api/operator/timeline?limit=20');
    if (!(d.events||[]).length) {
      document.getElementById('timelinePanel').innerHTML = '<p style="color:var(--dim);font-size:.85rem">No events yet.</p>';
      return;
    }
    const events = d.events.map(e => {
      const details = e.details || {};
      const pairs = Object.entries(details).filter(([k]) => k !== 'actor')
        .map(([k,v]) => `${k}: <b>${typeof v==='object'?JSON.stringify(v):v}</b>`).join(' · ');
      return `
        <div class="tl-event">
          <div class="tl-meta">
            <span class="badge ${EVENT_BADGE[e.event_type]||'b-info'}">${e.event_type}</span>
            <span style="color:var(--dim);font-size:.72rem">${e.actor} · ${e.platform||'global'} · ${rel(e.created_at)}</span>
          </div>
          ${pairs?`<div class="tl-detail">${pairs}</div>`:''}
        </div>`;
    }).join('');
    document.getElementById('timelinePanel').innerHTML = `<div class="timeline">${events}</div>`;
  } catch (e) { toast(e.message, 'error'); }
}

// ── Live View ──────────────────────────────────────────────────────────────
let _liveMode = 'screenshot';
let _sseSrc = null;

function startScreenshotStream() {
  if (_sseSrc) return;
  _sseSrc = new EventSource(api('/api/live/screenshot/stream?continuous=1'));
  _sseSrc.addEventListener('screenshot', (ev) => {
    const d = JSON.parse(ev.data);
    const meta = document.getElementById('shotMeta');
    const img = document.getElementById('liveShot');
    if (!meta || !img) return;
    if (!d.available) { meta.textContent = 'No screenshots yet.'; img.removeAttribute('src'); return; }
    meta.textContent = `${d.filename} · ${rel(d.captured_at)} · ${d.size_bytes}B`;
    img.src = `${withBase(d.url)}?t=${Date.now()}`;
  });
}

async function refreshLiveMeta() {
  try {
    const d = await apiFetch('/api/live/screenshot');
    if (d.available) {
      document.getElementById('shotMeta').textContent = `${d.filename} · ${rel(d.captured_at)} · ${d.size_bytes}B`;
      document.getElementById('liveShot').src = `${withBase(d.url)}?t=${Date.now()}`;
    }
  } catch (_) {}
}

async function requestScreenshotCapture() {
  try {
    await apiFetch('/api/live/screenshot/capture', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({actor:'dashboard', reason:'manual_refresh'})});
    toast('Screenshot requested', 'info');
  } catch (e) { toast(e.message, 'error'); }
}

function toggleLiveMode(mode) {
  _liveMode = mode;
  document.getElementById('shot-panel').style.display = mode === 'screenshot' ? '' : 'none';
  document.getElementById('control-panel').style.display = mode === 'control' ? '' : 'none';
  document.querySelectorAll('#live-mode-btns button').forEach(b => {
    b.classList.toggle('btn-blue', b.dataset.mode === mode);
    b.classList.toggle('btn-outline', b.dataset.mode !== mode);
  });
  if (mode === 'control') refreshNoVNCStatus();
}

async function refreshNoVNCStatus() {
  try {
    const d = await apiFetch('/api/browser/novnc-status');
    if (!d.available) {
      document.getElementById('control-panel').innerHTML = `
        <div class="card">
          <p style="color:var(--muted)">noVNC not installed.</p>
          <p style="font-size:.8rem;color:var(--dim);margin-top:.4rem">Install: <code>sudo apt install -y xvfb x11vnc novnc websockify</code></p>
          ${d.missing?`<p style="font-size:.8rem;color:var(--dim);margin-top:.2rem">Missing: ${d.missing.join(', ')}</p>`:''}
        </div>`;
      return;
    }
    const platform = document.getElementById('novnc-platform')?.value || 'linkedin';
    const state = (d.platforms||{})[platform] || {running: false};
    document.getElementById('browser-status').innerHTML = state.running
      ? `<span class="badge b-ok">running</span>`
      : `<span class="badge b-paused">stopped</span>`;
    const frame = document.getElementById('novnc-frame');
    if (frame && state.running) {
      // Pass host+port explicitly so noVNC ignores cached localStorage values (e.g. port 6080).
      // Append ts= to bust the cache and force the iframe to reload every time (otherwise if the
      // src is already the same URL the iframe won't re-initialize and autoconnect won't fire).
      const _wsPort = window.location.port || (window.location.protocol === 'https:' ? '443' : '80');
      const _novncUrl = `${window.location.origin}/novnc/vnc.html?autoconnect=1&resize=scale` +
        `&host=${encodeURIComponent(window.location.hostname)}&port=${_wsPort}&path=novnc-ws&ts=${Date.now()}`;
      frame.src = 'about:blank';
      // Small delay then set the real URL — ensures the iframe fully unloads first
      setTimeout(() => { frame.src = _novncUrl; }, 150);
    }
  } catch (e) { toast(e.message, 'error'); }
}

function openVncTab() {
  const _wsPort = window.location.port || (window.location.protocol === 'https:' ? '443' : '80');
  const url = `${window.location.origin}/novnc/vnc.html?autoconnect=1&resize=scale` +
    `&host=${encodeURIComponent(window.location.hostname)}&port=${_wsPort}&path=novnc-ws`;
  window.open(url, '_blank');
}

async function launchBrowser() {
  const platform = document.getElementById('novnc-platform').value;
  const btn = document.getElementById('launch-btn');
  btn.disabled = true;
  try {
    await apiFetch(`/api/browser/launch/${platform}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({actor:'dashboard'})});
    toast(`${platform} browser launched`, 'success');
    await new Promise(r => setTimeout(r, 2500));
    await refreshNoVNCStatus();
    if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) { toast(e.message, 'error'); }
  finally { btn.disabled = false; }
}

async function stopBrowser() {
  const platform = document.getElementById('novnc-platform').value;
  try {
    await apiFetch(`/api/browser/stop/${platform}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({actor:'dashboard'})});
    toast(`${platform} browser stopped`, 'success');
    const frame = document.getElementById('novnc-frame');
    if (frame) frame.src = 'about:blank';
    await refreshNoVNCStatus();
    if (_loaded.has('sec-timeline')) refreshTimeline();
  } catch (e) { toast(e.message, 'error'); }
}

// ── Pipeline Funnel ────────────────────────────────────────────────────────
async function refreshPipelineFunnel() {
  document.getElementById('funnel-body').innerHTML = skeleton();
  try {
    const d = await apiFetch('/api/pipeline/funnel');
    const stages = [
      {key:'discovered', label:'Discovered', bg:'#1e3a5f', fg:'#60a5fa'},
      {key:'scored',     label:'Scored',     bg:'#312e81', fg:'#a78bfa'},
      {key:'tailored',   label:'Tailored',   bg:'#451a03', fg:'#fb923c'},
      {key:'queued',     label:'Queued',     bg:'#052e16', fg:'#4ade80'},
      {key:'applied',    label:'Applied',    bg:'#042f2e', fg:'#2dd4bf'},
    ];
    const counts = stages.map(s => d[s.key] || 0);
    const maxCount = Math.max(...counts, 1);
    const funnelBase = d.discovered || maxCount;
    const MAX_BAR_H = 60;
    const staleCount = d.stale_url || 0;
    const cols = stages.map((s, i) => {
      const count = counts[i];
      const pct = funnelBase > 0 ? Math.round(count / funnelBase * 100) : 0;
      const h = Math.max(16, Math.round(count / maxCount * MAX_BAR_H));
      const pctLabel = count > 0 ? `${pct}%` : '&nbsp;';
      return `
        <div class="funnel-col">
          <div class="funnel-pct">${pctLabel}</div>
          <div class="funnel-bar" style="background:${s.bg};width:62px;height:${h}px">
            <span style="color:${s.fg};font-weight:700;font-size:1.05rem">${count}</span>
          </div>
          <div class="funnel-label">${s.label}</div>
        </div>`;
    }).join('');
    const staleChip = staleCount > 0
      ? `<div style="margin-top:.5rem;font-size:.78rem;color:var(--muted)">⚠ ${staleCount} stale URL${staleCount>1?'s':''} skipped</div>`
      : '';
    document.getElementById('funnel-body').innerHTML = `<div class="funnel">${cols}</div>${staleChip}`;
  } catch (e) { toast(e.message, 'error'); }
  refreshLastRun();
  if (_loaded.has('sec-queue')) refreshQueuePendingQuestions();
}

async function refreshLastRun() {
  const el = document.getElementById('last-run-body');
  if (!el) return;
  try {
    const d = await apiFetch('/api/pipeline/last-run');
    if (!d.found) { el.innerHTML = '<div class="muted-label">No runs recorded yet.</div>'; return; }
    const ts = (d.timestamp_utc || '').replace('T', ' ').slice(0, 16);
    const statusColor = d.status === 'completed' ? '#22c55e' : '#ef4444';
    const stageRows = (d.stage_metrics || []).map(s => {
      const ic = s.status === 'completed' ? '✓' : s.status === 'failed' ? '✕' : '⟳';
      const sc = s.status === 'completed' ? '#22c55e' : s.status === 'failed' ? '#ef4444' : '#a78bfa';
      const dur = s.duration_ms ? `${(s.duration_ms/1000).toFixed(1)}s` : '—';
      const retries = s.retries_used ? ` (${s.retries_used} retry)` : '';
      return `<div style="display:flex;gap:.5rem;align-items:center;padding:.2rem 0;border-top:1px solid var(--border)">
        <span style="color:${sc};min-width:1rem;font-size:.8rem">${ic}</span>
        <span style="min-width:4.5rem;font-size:.82rem;font-weight:500">${s.stage}</span>
        <span class="muted-label" style="font-size:.78rem">${dur}${retries}</span>
      </div>`;
    }).join('');
    const gate = d.apply_gate_decision ? `<div class="muted-label" style="margin-top:.4rem">Gate: ${d.apply_gate_decision}</div>` : '';
    el.innerHTML = `
      <div style="display:flex;gap:1rem;align-items:baseline;margin-bottom:.5rem">
        <span style="color:${statusColor};font-weight:600">${d.status || '—'}</span>
        <span class="muted-label">${ts} UTC</span>
        <span class="muted-label">retries: ${d.total_retries ?? 0}</span>
      </div>
      ${stageRows}${gate}`;
  } catch (e) {
    el.innerHTML = `<div class="muted-label">Error: ${e.message}</div>`;
  }
}

// ── Pending Apply Questions ────────────────────────────────────────────────

// Pending-questions UI lives in the Queue section now (refreshQueuePendingQuestions / saveQueuePendingAnswers).

// ── Pipeline Jobs ──────────────────────────────────────────────────────────
let _jobFilter = 'all';
let _openJobId = null;

// tab → API status param mapping
const _TAB_STATUS = {
  'all':       '',
  'to-score':  'discovered',
  'to-tailor': 'scored',
  'to-apply':  'tailored',
  'applied':   'applied',
};

async function loadJobCounts() {
  try {
    const d = await apiFetch('/api/pipeline/funnel');
    const counts = {};
    (d.stages || []).forEach(s => { counts[s.status] = s.count; });
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const set = (id, n) => { const el = document.getElementById(id); if (el) el.textContent = n > 0 ? n : ''; };
    set('jc-all', total);
    set('jc-to-score',  counts['discovered'] || 0);
    set('jc-to-tailor', counts['scored']     || 0);
    set('jc-to-apply',  counts['tailored']   || 0);
    set('jc-applied',   counts['applied']    || 0);
    // Pulse the "Needs Tailoring" tab if there are jobs ready
    const tBtn = document.querySelector('#jobs-filters [data-tab="to-tailor"]');
    if (tBtn) tBtn.classList.toggle('filter-btn-tailor-pulse', (counts['scored'] || 0) > 0);
  } catch (_) {}
}

async function refreshPipelineJobs(tab) {
  if (tab !== undefined) _jobFilter = tab;
  document.getElementById('jobs-list').innerHTML = skeleton();
  document.querySelectorAll('#jobs-filters .filter-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === _jobFilter)
  );
  loadJobCounts();

  try {
    const statusParam = _TAB_STATUS[_jobFilter] ?? '';
    const param = statusParam ? `&status=${statusParam}` : '';
    const data = await apiFetch(`/api/pipeline/jobs?limit=100${param}`);
    const sponsorIcon = {high:'🟢', medium:'🟡', low:'🔴', none:'⛔', unknown:'❓'};

    const rows = (data.jobs||[]).map(j => {
      const tip = _esc(_scoreTooltip(j));
      const sc = j.score != null
        ? `<span class="sc ${j.score>=70?'sc-hi':j.score>=40?'sc-mid':'sc-lo'}" title="${tip}">${j.score}</span>`
        : '<span style="color:var(--dim)">—</span>';
      const isNeedsTailor = j.status === 'scored' && !j.has_tailoring;
      const rowStyle = isNeedsTailor
        ? 'cursor:pointer;background:rgba(251,191,36,.06);border-left:2px solid #fbbf24'
        : 'cursor:pointer';
      const sponsor = sponsorIcon[j.sponsorship_confidence] || '<span style="color:var(--dim)">—</span>';
      return `
        <tr style="${rowStyle}" onclick="showJobDetail(${j.job_id})">
          <td>
            ${isNeedsTailor ? '<span style="color:#fbbf24;font-size:.7rem;margin-right:.3rem">✨</span>' : ''}
            ${j.company||'—'} · ${j.title||'—'}
          </td>
          <td style="text-align:center">${sc}</td>
          <td style="text-align:center"><span class="badge b-${j.status||'discovered'}">${j.status||'discovered'}</span></td>
          <td style="text-align:center">${sponsor}</td>
          <td style="text-align:center;color:${j.has_tailoring?'#4ade80':'var(--dim)'}">${j.has_tailoring?'✓':'—'}</td>
        </tr>`;
    }).join('');

    const empty = _jobFilter === 'to-tailor'
      ? 'No scored jobs awaiting tailoring. Run <strong>Score</strong> first.'
      : 'No jobs found.';
    document.getElementById('jobs-list').innerHTML = `
      <table>
        <thead><tr>
          <th>Company / Title</th>
          <th style="text-align:center">Score</th>
          <th style="text-align:center">Status</th>
          <th style="text-align:center">Sponsor</th>
          <th style="text-align:center">Tailored</th>
        </tr></thead>
        <tbody>${rows || `<tr><td colspan="5" style="color:var(--dim);text-align:center;padding:1.5rem">${empty}</td></tr>`}</tbody>
      </table>`;
  } catch (e) { toast(e.message, 'error'); }
}

async function showJobDetail(jobId) {
  _openJobId = jobId;
  document.getElementById('drawer-overlay').classList.add('open');
  document.getElementById('job-drawer').classList.add('open');
  document.getElementById('drawer-title').textContent = 'Loading…';
  document.getElementById('drawer-subtitle').textContent = '';
  document.getElementById('drawer-content').innerHTML = skeleton(4);
  try {
    const j = await apiFetch(`/api/pipeline/jobs/${jobId}`);
    document.getElementById('drawer-title').textContent = j.company || '—';
    document.getElementById('drawer-subtitle').textContent = j.title || '—';
    const sc = j.score != null
      ? `<span class="sc ${j.score>=70?'sc-hi':j.score>=40?'sc-mid':'sc-lo'}">${j.score}</span>`
      : '—';
    const reasons = (j.score_reasons||[]).map(r=>`<span class="rtag">${r}</span>`).join('');
    const sponsorIcon = {high:'🟢', medium:'🟡', low:'🔴', none:'⛔', unknown:'❓'};
    const artifacts = (j.artifacts||[]).map(a =>
      `<a class="drawer-artifact" href="${a.path}" target="_blank">📄 ${a.kind}: ${a.path.split('/').pop()}</a>`
    ).join('');
    document.getElementById('drawer-content').innerHTML = `
      <div class="drawer-lbl">Status</div>
      <span class="badge b-${j.status||'discovered'}" style="display:inline-block;margin-bottom:.75rem">${j.status||'discovered'}</span>

      <div class="drawer-lbl">Score</div>
      <div style="margin-bottom:.75rem">${sc} ${sponsorIcon[j.sponsorship_confidence]||''}<div style="margin-top:.3rem">${reasons||'<span style="color:var(--dim);font-size:.8rem">No scoring data</span>'}</div></div>

      ${artifacts?`<div class="drawer-lbl">Artifacts</div><div style="margin-bottom:.75rem">${artifacts}</div>`:''}

      <div class="drawer-lbl">Source</div>
      <div style="color:var(--muted);font-size:.82rem;margin-bottom:.5rem">${j.source||'—'} · ${j.location||'—'}</div>

      <div class="drawer-lbl">Discovered</div>
      <div style="color:var(--muted);font-size:.82rem;margin-bottom:.5rem">${rel(j.discovered_at)}</div>

      ${j.notes?`<div class="drawer-lbl">Notes</div><div style="color:var(--muted);font-size:.82rem">${j.notes}</div>`:''}
      ${j.url?`<div style="margin-top:.75rem"><a href="${j.url}" target="_blank" style="color:#60a5fa;font-size:.82rem">Open listing ↗</a></div>`:''}`;
  } catch (e) { toast(e.message, 'error'); }
}

function closeDrawer() {
  _openJobId = null;
  document.getElementById('drawer-overlay').classList.remove('open');
  document.getElementById('job-drawer').classList.remove('open');
}

// ── Init ───────────────────────────────────────────────────────────────────
// Apply saved theme on load
apiFetch('/api/settings').then(d => {
  applyTheme(d.theme);
  updateLlmIndicator(d);
  document.querySelectorAll('input[name="submitPolicy"]').forEach(r => {
    r.checked = r.value === d.submit_policy;
  });
}).catch(() => {});

// ── Admin dashboard ─────────────────────────────────────────────────────────
async function refreshAdmin() {
  const el = document.getElementById('admin-panel');
  el.innerHTML = skeleton();
  try {
    const d = await apiFetch('/api/admin/overview');
    const platforms = Object.entries(d.applied_by_platform || {})
      .map(([p, n]) => `<span class="badge b-info">${p}: ${n}</span>`).join(' ') || '<span class="muted-label">none yet</span>';
    const rows = (d.accounts || []).map(a => `
      <tr>
        <td>${a.is_admin ? '👑 ' : ''}${_esc(a.display_name || a.username)}<div class="muted-label" style="font-size:.7rem">@${_esc(a.username)}</div></td>
        <td>${(a.created_at || '').slice(0, 10)}</td>
        <td>${a.onboarded ? '<span class="badge b-ok">yes</span>' : '<span class="badge b-amber">no</span>'}</td>
        <td>${a.jobs_discovered}</td>
        <td>${a.applied_total}</td>
        <td>${a.applied_last_7d}</td>
        <td class="muted-label" style="font-size:.72rem">${a.last_applied_at ? rel(a.last_applied_at) : '—'}</td>
      </tr>`).join('') || '<tr><td colspan="7" class="muted-label">No accounts yet — share the invite link.</td></tr>';

    el.innerHTML = `
      <div class="stat-grid" style="margin-bottom:.75rem">
        <div class="stat-tile"><div class="stat-val v-blue">${d.accounts_total}</div><div class="stat-label">Accounts</div></div>
        <div class="stat-tile"><div class="stat-val v-green">${d.accounts_new_last_7d}</div><div class="stat-label">New this week</div></div>
        <div class="stat-tile"><div class="stat-val v-slate">${d.accounts_onboarded}</div><div class="stat-label">Onboarded</div></div>
        <div class="stat-tile"><div class="stat-val v-amber">${d.active_sessions}</div><div class="stat-label">Active sessions</div></div>
        <div class="stat-tile"><div class="stat-val v-green">${d.applied_total}</div><div class="stat-label">Applications (all)</div></div>
        <div class="stat-tile"><div class="stat-val v-blue">${d.applied_last_7d}</div><div class="stat-label">Applications 7d</div></div>
      </div>
      <div style="margin-bottom:.75rem">${platforms}</div>
      <table class="data-table" style="width:100%">
        <thead><tr><th>User</th><th>Joined</th><th>Onboarded</th><th>Jobs found</th><th>Applied</th><th>7d</th><th>Last apply</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (e) { el.innerHTML = `<div class="muted-label">${e.message}</div>`; }
}

async function maybeShowAdminNav() {
  try {
    const r = await fetch('/api/admin/overview');
    if (r.ok) document.getElementById('admin-nav').style.display = '';
  } catch (e) { /* hidden when not admin */ }
}

// ── Accounts: auth bootstrap, onboarding wizard, account chip ──────────────
let _authMode = 'login';

function authTab(mode) {
  _authMode = mode;
  document.getElementById('auth-tab-login').classList.toggle('active', mode === 'login');
  document.getElementById('auth-tab-register').classList.toggle('active', mode === 'register');
  document.getElementById('auth-display-row').style.display = mode === 'register' ? '' : 'none';
  document.getElementById('auth-submit-btn').textContent = mode === 'register' ? 'Create account' : 'Sign in';
}

async function authSubmit() {
  const body = {
    username: document.getElementById('auth-username').value.trim(),
    password: document.getElementById('auth-password').value,
  };
  if (_authMode === 'register') body.display_name = document.getElementById('auth-display').value.trim();
  const err = document.getElementById('auth-error');
  err.textContent = '';
  try {
    const r = await fetch(`/api/auth/${_authMode}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const d = await r.json();
    if (!r.ok) { err.textContent = d.detail || 'failed'; return; }
    document.getElementById('auth-overlay').style.display = 'none';
    await bootstrapAccount();
  } catch (e) { err.textContent = e.message; }
}

async function authLogout() {
  try { await fetch('/api/auth/logout', {method:'POST'}); } catch (e) {}
  location.reload();
}

function copyInviteLink() {
  const url = location.origin + '/?signup=1';
  navigator.clipboard?.writeText(url);
  toast('Invite link copied — share it with a friend. They create their own separate account.', 'success');
}

async function refreshAccountChip(user) {
  const chip = document.getElementById('account-chip');
  chip.style.display = '';
  document.getElementById('account-name').textContent = user.display_name || user.username;
  try {
    const s = await apiFetch('/api/stats/applications');
    const per = Object.entries(s.applied_by_platform || {}).map(([p, n]) => `${p} ${n}`).join(' · ');
    document.getElementById('account-stats').textContent =
      `${s.applied_total} application${s.applied_total === 1 ? '' : 's'}${per ? ' — ' + per : ''}`;
  } catch (e) { /* stats are best-effort */ }
}

async function showOnboarding() {
  const d = await apiFetch('/api/onboarding');
  const host = document.getElementById('onboard-questions');
  host.innerHTML = d.questions.map(q => {
    if (q.type === 'bool') {
      return `<div class="settings-row" style="justify-content:space-between;align-items:center">
        <label>${q.label}</label>
        <label class="toggle-switch"><input type="checkbox" id="ob-${q.key}"><span class="toggle-slider"></span></label>
      </div>`;
    }
    const inputType = q.type === 'number' ? 'number' : 'text';
    return `<div class="settings-row" style="flex-direction:column;align-items:stretch">
      <label class="settings-field-label" style="min-width:auto">${q.label}</label>
      <input id="ob-${q.key}" type="${inputType}" class="settings-input" placeholder="${q.hint || ''}" style="width:100%">
    </div>`;
  }).join('');
  document.getElementById('onboard-overlay').style.display = 'block';
}

function _readFileB64(input) {
  return new Promise((resolve) => {
    const f = input.files && input.files[0];
    if (!f) return resolve(null);
    const reader = new FileReader();
    reader.onload = () => resolve({filename: f.name, b64: String(reader.result).split(',')[1]});
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(f);
  });
}

async function submitOnboarding() {
  const err = document.getElementById('onboard-error');
  err.textContent = '';
  try {
    const d = await apiFetch('/api/onboarding');
    const answers = {};
    for (const q of d.questions) {
      const el = document.getElementById(`ob-${q.key}`);
      if (!el) continue;
      answers[q.key] = q.type === 'bool' ? el.checked : el.value;
    }
    await apiFetch('/api/onboarding', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({answers})});

    for (const [kind, inputId] of [['resume', 'onboard-resume-file'], ['cover', 'onboard-cover-file']]) {
      const file = await _readFileB64(document.getElementById(inputId));
      if (file) {
        await apiFetch('/api/onboarding/template', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({kind, filename: file.filename, content_b64: file.b64})});
      }
    }

    const key = document.getElementById('onboard-api-key').value.trim();
    if (key) {
      await apiFetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({anthropic_api_key: key, llm_enabled: true, llm_provider: 'anthropic'})});
    }

    document.getElementById('onboard-overlay').style.display = 'none';
    toast('Setup complete — welcome aboard!', 'success');
    refreshOps();
  } catch (e) { err.textContent = e.message; }
}

async function bootstrapAccount() {
  try {
    const me = await (await fetch('/api/auth/me')).json();
    if (!me.authenticated) {
      // No user session. If signups are open or ?signup=1, show the overlay;
      // otherwise fall through to legacy single-user cockpit behaviour.
      const wantsSignup = new URLSearchParams(location.search).has('signup');
      if (wantsSignup || me.signups_allowed) {
        document.getElementById('auth-overlay').style.display = 'block';
        if (wantsSignup) authTab('register');
      }
      return;
    }
    await refreshAccountChip(me.user);
    if (!me.user.onboarded) await showOnboarding();
  } catch (e) { /* cockpit still usable in single-user mode */ }
}

bootstrapAccount();
maybeShowAdminNav();
showSection('sec-command');
refreshAlertBadge();
refreshLogsBadge();

setInterval(refreshAlertBadge, 15000);
setInterval(refreshLogsBadge, 30000);
setInterval(() => { if (_loaded.has('sec-health'))     refreshHealth();     }, 10000);
setInterval(() => { if (_loaded.has('sec-policy'))     refreshPolicy();     }, 10000);
setInterval(() => { if (_loaded.has('sec-scheduler'))  refreshScheduler();  }, 15000);
setInterval(() => { if (_loaded.has('sec-autoapply') && document.getElementById('sec-autoapply').classList.contains('active')) refreshAutoApply(); }, 15000);
setInterval(() => { if (_loaded.has('sec-timeline'))   refreshTimeline();   }, 15000);
setInterval(() => { if (_loaded.has('sec-command'))    refreshOps();        }, 15000);
setInterval(() => { if (_loaded.has('sec-alerts'))     refreshAlerts();     }, 15000);
setInterval(() => { if (_loaded.has('sec-logs'))       refreshLogs();       }, 30000);

// ── Settings ───────────────────────────────────────────────────────────────

// Scoring/tailoring models (text reasoning — no vision needed)
const _MODEL_OPTIONS = {
  anthropic: [
    { value: 'claude-haiku-4-5-20251001', label: 'claude-haiku-4-5 — fast, cheap' },
    { value: 'claude-sonnet-4-6',         label: 'claude-sonnet-4-6 — balanced ✦ recommended' },
    { value: 'claude-opus-4-7',           label: 'claude-opus-4-7 — most capable' },
  ],
  openai: [
    { value: 'gpt-4.1-nano',  label: 'gpt-4.1-nano — fastest, cheapest' },
    { value: 'gpt-4.1-mini',  label: 'gpt-4.1-mini — fast, cheap' },
    { value: 'gpt-4o-mini',   label: 'gpt-4o-mini — fast, cheap' },
    { value: 'gpt-4.1',       label: 'gpt-4.1 — balanced, latest' },
    { value: 'gpt-4o',        label: 'gpt-4o — balanced' },
  ],
  openrouter: [
    // ── FREE (confirmed working) ───────────────────────────────────────────
    { value: 'nvidia/nemotron-3-super-120b-a12b:free',    label: 'nemotron-3-super-120b — FREE ✓' },
    { value: 'openai/gpt-oss-120b:free',                  label: 'gpt-oss-120b — FREE ✓' },
    { value: 'meta-llama/llama-3.3-70b-instruct:free',    label: 'llama-3.3-70b — FREE (may rate-limit)' },
    { value: 'google/gemma-4-31b-it:free',                label: 'gemma-4-31b — FREE (may rate-limit)' },
    // ── PAID — Scoring / Tailoring recommended ────────────────────────────
    { value: 'google/gemini-2.0-flash-001',               label: 'gemini-2.0-flash — paid ✦ recommended' },
    { value: 'deepseek/deepseek-chat-v3-0324',            label: 'deepseek-v3 — paid, very cheap' },
    { value: 'anthropic/claude-3-haiku',                  label: 'claude-3-haiku — paid, fast' },
    { value: 'anthropic/claude-3.5-haiku',                label: 'claude-3.5-haiku — paid, fast' },
    { value: 'anthropic/claude-sonnet-4-6',               label: 'claude-sonnet-4-6 — paid, balanced' },
    { value: 'openai/gpt-4.1-mini',                       label: 'gpt-4.1-mini — paid, fast' },
    { value: 'openai/gpt-4.1',                            label: 'gpt-4.1 — paid' },
    { value: 'openai/gpt-4o-mini',                        label: 'gpt-4o-mini — paid' },
    { value: 'x-ai/grok-3-mini-beta',                     label: 'grok-3-mini — paid, fast' },
  ],
};

// Vision-capable models only (required for LLM browser agent screenshots)
const _VISION_MODEL_OPTIONS = {
  anthropic: [
    { value: '',                      label: 'Auto (claude-sonnet-4-6)' },
    { value: 'claude-haiku-4-5-20251001', label: 'claude-haiku-4-5 — fast, cheap' },
    { value: 'claude-sonnet-4-6',     label: 'claude-sonnet-4-6 — balanced ✦ recommended' },
    { value: 'claude-opus-4-7',       label: 'claude-opus-4-7 — most capable' },
  ],
  openai: [
    { value: '',               label: 'Auto (gpt-4o)' },
    { value: 'gpt-4o-mini',    label: 'gpt-4o-mini — fast, cheap' },
    { value: 'gpt-4o',         label: 'gpt-4o — balanced ✦ recommended' },
    { value: 'gpt-4.1-mini',   label: 'gpt-4.1-mini — fast' },
    { value: 'gpt-4.1',        label: 'gpt-4.1 — latest' },
  ],
  openrouter: [
    { value: '',                                           label: 'Auto (gemini-2.0-flash)' },
    // ── FREE (vision confirmed working) ───────────────────────────────────
    { value: 'nvidia/nemotron-nano-12b-v2-vl:free',       label: 'nemotron-nano-12b-vl — FREE ✓ vision' },
    // ── PAID — Browser vision recommended ────────────────────────────────
    { value: 'google/gemini-2.0-flash-001',               label: 'gemini-2.0-flash — paid ✦ recommended, vision' },
    { value: 'anthropic/claude-3-haiku',                  label: 'claude-3-haiku — paid, vision' },
    { value: 'anthropic/claude-3.5-haiku',                label: 'claude-3.5-haiku — paid, vision' },
    { value: 'anthropic/claude-sonnet-4-6',               label: 'claude-sonnet-4-6 — paid, best vision' },
    { value: 'openai/gpt-4o-mini',                        label: 'gpt-4o-mini — paid, vision' },
    { value: 'openai/gpt-4o',                             label: 'gpt-4o — paid, vision' },
    { value: 'openai/gpt-4.1-mini',                       label: 'gpt-4.1-mini — paid, vision' },
    { value: 'openai/gpt-4.1',                            label: 'gpt-4.1 — paid, vision' },
    { value: 'x-ai/grok-3-beta',                          label: 'grok-3 — paid, vision' },
  ],
};

function updateModelDropdowns(provider) {
  const options = _MODEL_OPTIONS[provider] || _MODEL_OPTIONS.anthropic;
  ['scoring-model', 'tailoring-model'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = options.map(o =>
      `<option value="${o.value}"${o.value === current ? ' selected' : ''}>${o.label}</option>`
    ).join('');
    if (!options.find(o => o.value === current)) sel.value = options[0].value;
  });
  // Discovery model — vision-only list
  const vOptions = _VISION_MODEL_OPTIONS[provider] || _VISION_MODEL_OPTIONS.anthropic;
  const dSel = document.getElementById('discovery-model');
  if (dSel) {
    const cur = dSel.value;
    dSel.innerHTML = vOptions.map(o =>
      `<option value="${o.value}"${o.value === cur ? ' selected' : ''}>${o.label}</option>`
    ).join('');
  }
}

async function refreshSettings() {
  try {
    const d = await apiFetch('/api/settings');
    // API Keys — show preview text, not actual value
    ['anthropic', 'openai', 'openrouter'].forEach(p => {
      const input = document.getElementById(`key-${p}`);
      if (input && d.api_keys[p]?.preview) {
        input.placeholder = d.api_keys[p].preview;
      }
    });
    // LLM toggle
    const toggle = document.getElementById('llm-toggle');
    if (toggle) toggle.checked = d.llm_enabled;
    // Live discover toggle
    const ldToggle = document.getElementById('live-discover-toggle');
    if (ldToggle) ldToggle.checked = !!d.live_discover;
    // Portal checkboxes
    const portals = d.portals || ['linkedin'];
    ['linkedin', 'indeed', 'glassdoor', 'handshake'].forEach(p => {
      const cb = document.getElementById(`portal-${p}`);
      if (cb) cb.checked = portals.includes(p);
    });
    // Discovery model — set after updateModelDropdowns has populated the options
    const dmEl = document.getElementById('discovery-model');
    if (dmEl) dmEl.value = d.discovery_model || '';
    // Discovery limits
    const djEl = document.getElementById('discover-max-jobs');
    if (djEl) djEl.value = d.discover_max_jobs ?? 40;
    const duEl = document.getElementById('discover-max-urls');
    if (duEl) duEl.value = d.discover_max_urls ?? 4;
    const daEl = document.getElementById('discover-max-actions');
    if (daEl) daEl.value = d.discover_max_actions ?? 15;
    // Theme cards
    document.querySelectorAll('.theme-card').forEach(card => {
      card.classList.toggle('active', card.dataset.theme === d.theme);
    });
    applyTheme(d.theme);
    // Selects — populate model dropdowns for the current provider first
    updateModelDropdowns(d.llm_provider || 'anthropic');
    const selectMap = {
      'llm-provider': 'llm_provider',
      'scoring-model': 'scoring_model',
      'tailoring-model': 'tailoring_model',
      'browser-mode': 'browser_mode',
      'default-submit-policy': 'submit_policy',
    };
    Object.entries(selectMap).forEach(([id, key]) => {
      const el = document.getElementById(id);
      if (el && d[key]) el.value = d[key];
    });
    // Min score
    const ms = document.getElementById('min-score');
    if (ms) ms.value = d.min_score;
    // Discovery URLs
    renderDiscoveryUrls(d.discovery_urls || []);
    // Submit policy radio in Command Center
    document.querySelectorAll('input[name="submitPolicy"]').forEach(r => {
      r.checked = r.value === d.submit_policy;
    });
    // LLM indicator
    updateLlmIndicator(d);
  } catch (e) { toast(e.message, 'error'); }
  // Load master resume separately
  try {
    const mr = await apiFetch('/api/settings/master-resume');
    const ta = document.getElementById('master-resume');
    if (ta) ta.value = mr.content;
  } catch (_) {}
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme || 'dark');
}

function _llmDot(color, text) {
  return `<span style="display:inline-flex;align-items:center;gap:.4rem;font-size:.78rem">
    <span style="width:8px;height:8px;background:${color};border-radius:50%;display:inline-block"></span>
    <span style="color:${color}">${text}</span></span>`;
}

async function updateLlmIndicator(settings) {
  const el = document.getElementById('llm-indicator');
  if (!el) return;
  if (!settings.llm_enabled) {
    el.innerHTML = _llmDot('#f59e0b', 'Rule-based mode · no credits needed');
    return;
  }
  // Enabled — verify it's actually usable (truthful), not just flagged on.
  try {
    const h = await apiFetch('/api/llm/health');
    if (h.ready) {
      el.innerHTML = _llmDot('#22c55e', `AI mode · ${h.provider} · ${settings.scoring_model || h.model || ''}`);
    } else {
      el.innerHTML = _llmDot('#ef4444', `AI enabled but no ${h.provider || ''} key — falling back to rules`);
    }
  } catch (e) {
    el.innerHTML = _llmDot('#22c55e', `AI mode · ${settings.llm_provider} · ${settings.scoring_model}`);
  }
}

async function saveSetting(key, value) {
  try {
    await apiFetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }) });
  } catch (e) { toast(e.message, 'error'); }
}

async function saveApiKey(provider, value) {
  if (!value || value.includes('●')) return; // placeholder or masked — skip
  await saveSetting(`${provider}_api_key`, value);
  const input = document.getElementById(`key-${provider}`);
  if (input) { input.value = ''; input.placeholder = value.slice(0, 7) + '●●●●●●●●'; }
  toast(`${provider} key saved`, 'success');
}

async function testApiKey(provider) {
  const keyEl = document.getElementById(`key-${provider}`);
  const resultEl = document.getElementById(`test-${provider}-result`);
  const rawKey = keyEl?.value;
  const btn = document.getElementById(`test-${provider}`);
  if (btn) btn.disabled = true;
  try {
    const key = rawKey && !rawKey.includes('●') ? rawKey : '__stored__';
    const d = await apiFetch('/api/settings/test-key', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, key }) });
    if (resultEl) resultEl.innerHTML = d.ok
      ? '<span style="color:#22c55e">✓ Connected</span>'
      : `<span style="color:#ef4444">✕ ${d.error}</span>`;
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<span style="color:#ef4444">✕ ${e.message}</span>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function toggleKeyVisibility(inputId, btn) {
  const el = document.getElementById(inputId);
  if (!el) return;
  el.type = el.type === 'password' ? 'text' : 'password';
  btn.textContent = '👁';
}

function setTheme(theme) {
  applyTheme(theme);
  saveSetting('theme', theme);
  document.querySelectorAll('.theme-card').forEach(c => c.classList.toggle('active', c.dataset.theme === theme));
}

async function saveLlmEnabled(enabled) {
  await saveSetting('llm_enabled', enabled);
  if (typeof refreshOps === 'function' && _loaded && _loaded.has('sec-command')) refreshOps();
}

async function savePortals() {
  const portals = ['linkedin', 'indeed', 'glassdoor', 'handshake']
    .filter(p => document.getElementById(`portal-${p}`)?.checked);
  if (!portals.length) return; // keep at least one
  await saveSetting('portals', portals);
}

async function saveMasterResume() {
  const content = document.getElementById('master-resume')?.value || '';
  try {
    await apiFetch('/api/settings/master-resume', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }) });
    toast('Master resume saved', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

function renderDiscoveryUrls(urls) {
  const el = document.getElementById('discovery-urls-list');
  if (!el) return;
  el.innerHTML = urls.map((u, i) => `
    <div style="display:flex;align-items:center;gap:.4rem;margin-bottom:.3rem">
      <span style="flex:1;font-family:monospace;font-size:.72rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${u}</span>
      <button class="btn btn-slate btn-sm" onclick="removeDiscoveryUrl(${i})">✕</button>
    </div>`).join('');
}

async function addDiscoveryUrl() {
  const input = document.getElementById('new-discovery-url');
  const url = input?.value?.trim();
  if (!url) return;
  const d = await apiFetch('/api/settings');
  const urls = [...(d.discovery_urls || []), url];
  await saveSetting('discovery_urls', urls);
  if (input) input.value = '';
  renderDiscoveryUrls(urls);
  toast('URL added', 'success');
}

async function removeDiscoveryUrl(index) {
  const d = await apiFetch('/api/settings');
  const urls = (d.discovery_urls || []).filter((_, i) => i !== index);
  await saveSetting('discovery_urls', urls);
  renderDiscoveryUrls(urls);
}

// ── Pipeline control ───────────────────────────────────────────────────────

let _pipelineEventSource = null;

async function runPipelineStage(stage, logElId, portals) {
  const logEl = document.getElementById(logElId || 'pipeline-log');
  if (logEl) logEl.innerHTML = `<span style="color:var(--dim)">Starting ${stage}…</span>`;

  if (_pipelineEventSource) { _pipelineEventSource.close(); _pipelineEventSource = null; }

  const body = { stage };
  if (portals && portals.length) body.portals = portals;

  try {
    const d = await apiFetch('/api/pipeline/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) });
    streamPipelineRun(d.run_id, logEl);
  } catch (e) { toast(e.message, 'error'); }
}

function runPipelineFromLive(stage) {
  // Run only for the platform currently selected in the live browser dropdown.
  // "all" still scopes to the selected platform — use headless Run All for multi-platform.
  const platform = document.getElementById('novnc-platform')?.value || 'linkedin';
  return runPipelineStage(stage, 'live-pipeline-log', [platform]);
}

function streamPipelineRun(runId, logEl) {
  const es = new EventSource(`/api/pipeline/run/${runId}/stream`);
  _pipelineEventSource = es;

  es.addEventListener('stage', e => {
    const d = JSON.parse(e.data);
    if (!logEl) return;
    const icon = d.status === 'completed' ? '<span style="color:#22c55e">✓</span>'
      : d.status === 'failed' ? '<span style="color:#ef4444">✕</span>'
      : '<span style="color:#a78bfa">⟳</span>';
    const dur = d.duration_ms ? ` (${d.duration_ms}ms)` : '';
    logEl.innerHTML += `<div>${icon} ${d.stage} — ${d.message}${dur}</div>`;
    logEl.scrollTop = logEl.scrollHeight;
  });

  es.addEventListener('done', e => {
    const d = JSON.parse(e.data);
    if (logEl) logEl.innerHTML += `<div style="color:${d.status === 'completed' ? '#22c55e' : '#ef4444'}">Pipeline ${d.status}</div>`;
    es.close();
    _pipelineEventSource = null;
    if (typeof refreshPipelineFunnel === 'function' && _loaded && _loaded.has('sec-funnel')) refreshPipelineFunnel();
    if (typeof refreshPipelineJobs === 'function' && _loaded && _loaded.has('sec-jobs')) refreshPipelineJobs();
    if (typeof refreshOps === 'function') refreshOps();
    if (typeof refreshLastRun === 'function' && _loaded && _loaded.has('sec-funnel')) refreshLastRun();
    refreshDocuments();
    if (_loaded && _loaded.has('sec-queue')) refreshQueuePendingQuestions();
    loadJobCounts();
  });

  es.addEventListener('error', () => {
    if (logEl) logEl.innerHTML += '<div style="color:#ef4444">✕ Connection lost</div>';
    es.close();
    _pipelineEventSource = null;
  });
}

// ── Logs ───────────────────────────────────────────────────────────────────

let _logsFilter = 'all';

async function refreshLogs(level) {
  if (level !== undefined) _logsFilter = level;
  document.querySelectorAll('#logs-filters .filter-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.level === _logsFilter)
  );
  document.getElementById('logs-list').innerHTML = skeleton(3);
  try {
    const param = _logsFilter === 'all' ? '' : `?level=${_logsFilter}`;
    const d = await apiFetch(`/api/logs${param}`);
    const levelBadge = {
      error: '<span class="badge" style="background:#ef444422;color:#f87171;border:1px solid #ef444440">error</span>',
      warn:  '<span class="badge" style="background:#f59e0b22;color:#fbbf24;border:1px solid #f59e0b40">warn</span>',
      info:  '<span class="badge" style="background:#3b82f622;color:#93c5fd;border:1px solid #3b82f640">info</span>',
    };
    const sourceBadge = s => `<span style="color:var(--dim);font-size:.72rem;font-family:monospace">${s}</span>`;
    const rows = (d.logs || []).map(e => {
      const badge = levelBadge[e.level] || levelBadge.info;
      const details = e.details
        ? `<div style="margin-top:.3rem;font-size:.75rem;color:var(--dim);font-family:monospace;white-space:pre-wrap">${JSON.stringify(e.details, null, 2)}</div>`
        : '';
      return `<div style="padding:.6rem 0;border-bottom:1px solid var(--border)">
        <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
          ${badge} ${sourceBadge(e.source)}
          <span style="flex:1;font-size:.85rem">${e.message}</span>
          <span style="color:var(--dim);font-size:.75rem;white-space:nowrap">${rel(e.created_at)}</span>
        </div>
        ${details}
      </div>`;
    }).join('');
    document.getElementById('logs-list').innerHTML = rows ||
      '<div style="color:var(--dim);text-align:center;padding:2rem;font-size:.85rem">No log entries yet.</div>';
  } catch (e) { toast(e.message, 'error'); }
}

async function clearLogs() {
  if (!confirm('Clear all log entries?')) return;
  try {
    await apiFetch('/api/logs', { method: 'DELETE' });
    toast('Logs cleared', 'info');
    refreshLogs();
    refreshLogsBadge();
  } catch (e) { toast(e.message, 'error'); }
}

async function refreshLogsBadge() {
  try {
    const d = await apiFetch('/api/logs?level=error&limit=50');
    const count = (d.logs || []).length;
    const badge = document.getElementById('logs-error-badge');
    if (badge) {
      badge.style.display = count > 0 ? '' : 'none';
      badge.textContent = count > 9 ? '9+' : count;
    }
  } catch (_) {}
}

// ── Generated documents ────────────────────────────────────────────────────

async function refreshDocuments() {
  const el = document.getElementById('documents-list');
  if (!el) return;
  try {
    const d = await apiFetch('/api/artifacts?limit=100');
    const items = d.artifacts || [];
    if (!items.length) {
      el.innerHTML = '<div class="muted-label" style="padding:.5rem 0">No documents yet. Run the tailor stage to generate your first PDF resume and cover letter.</div>';
      return;
    }

    // Group by job_id so resume+cover+screenshots for the same job stay together
    const groups = new Map();
    for (const item of items) {
      const key = item.job_id != null ? `job_${item.job_id}` : `anon__${item.job_title || ''}__${item.job_company || ''}`;
      if (!groups.has(key)) groups.set(key, { meta: item, docs: [] });
      groups.get(key).docs.push(item);
    }

    const html = [...groups.values()].map(({ meta, docs }) => {
      const ts = (meta.created_at || '').replace('T', ' ').slice(0, 16);
      const job = [meta.job_title, meta.job_company].filter(Boolean).join(' @ ') || 'Unknown job';

      const docRows = docs.filter(d => d.kind !== 'apply_screenshot').map(doc => {
        const kindLabel = doc.kind === 'resume' ? '📄 Resume' : '✉ Cover Letter';
        const ext = (doc.ext || 'md').toUpperCase();
        const extBadge = `<span class="badge ${doc.ext === 'pdf' ? 'badge-pdf' : 'badge-md'}">${ext}</span>`;
        const previewBtn = doc.file_exists
          ? `<a class="btn btn-slate btn-sm" href="/api/artifacts/${doc.id}/view" target="_blank" title="Preview in browser">👁 Preview</a>`
          : '';
        const dlBtn = doc.file_exists
          ? `<a class="btn btn-blue btn-sm" href="/api/artifacts/${doc.id}/download" download="${doc.filename || 'document'}">⬇ Download</a>`
          : `<span class="muted-label" style="font-size:.75rem;font-style:italic">file pruned</span>`;
        const delBtn = `<button class="btn btn-slate btn-sm" style="background:#7f1d1d;color:#fff" onclick="deleteArtifact(${doc.id}, this)" title="Delete this document">🗑</button>`;
        const fname = doc.filename || '—';
        return `<div class="doc-row">
          <span class="doc-kind">${kindLabel} ${extBadge}</span>
          <span class="doc-filename muted-label" title="${doc.path || ''}">${fname}</span>
          ${previewBtn}
          ${dlBtn}
          ${delBtn}
        </div>`;
      }).join('');

      const screenshots = docs.filter(d => d.kind === 'apply_screenshot');
      const shotStrip = screenshots.length ? `
        <div style="margin-top:.5rem">
          <div style="font-size:.72rem;color:var(--muted);margin-bottom:.3rem">📷 Apply Screenshots</div>
          <div style="display:flex;gap:.5rem;flex-wrap:wrap">
            ${screenshots.map(s => {
              const step = (s.filename || '').replace(/apply-\d+-|-\d+\.png/g, '').replace(/-/g, ' ') || 'step';
              return s.file_exists
                ? `<div style="text-align:center;position:relative">
                     <a href="/api/artifacts/${s.id}/view" target="_blank">
                       <img src="/api/artifacts/${s.id}/view" style="width:120px;height:72px;object-fit:cover;border-radius:4px;border:1px solid var(--border)" title="${_esc(step)}">
                     </a>
                     <div style="font-size:.65rem;color:var(--muted);margin-top:.15rem">${_esc(step)}</div>
                     <div style="display:flex;gap:.2rem;justify-content:center;margin-top:.2rem">
                       <a class="btn btn-slate btn-sm" style="font-size:.65rem;padding:.15rem .4rem" href="/api/artifacts/${s.id}/download" download="${s.filename}">⬇</a>
                       <button class="btn btn-slate btn-sm" style="font-size:.65rem;padding:.15rem .4rem;background:#7f1d1d;color:#fff" onclick="deleteArtifact(${s.id}, this)" title="Delete">🗑</button>
                     </div>
                   </div>`
                : '';
            }).join('')}
          </div>
        </div>` : '';

      return `<div class="doc-group">
        <div class="doc-group-header">
          <span class="doc-job-name">${_esc(job)}</span>
          <span class="muted-label" style="font-size:.78rem">${ts}</span>
        </div>
        ${docRows}${shotStrip}
      </div>`;
    }).join('');

    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<div class="muted-label">Error loading documents: ${e.message}</div>`;
  }
}

function _esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Build a hover tooltip explaining why a job scored what it scored.
// Heuristic scorer: 60 (role match) + 40 (location/remote match) + 5 (visa sponsor) = 105 max.
// If a category contributed nothing, surface that as the "why low" reason.
function _scoreTooltip(j) {
  if (j.score == null) return 'Not scored yet';
  const reasons = Array.isArray(j.score_reasons) ? j.score_reasons : [];
  const matched = new Set(reasons.map(r => String(r).toLowerCase()));
  const hasFullRole = matched.has('role keyword match');
  const hasPartialRole = matched.has('partial role match');
  const hasFullLoc = matched.has('location match');
  const hasPartialLoc = matched.has('partial location match');

  const lines = [`Score: ${j.score}/105`];
  if (reasons.length) lines.push('Why: ' + reasons.join(', '));

  const missing = [];
  if (!hasFullRole && !hasPartialRole) {
    missing.push(`title "${j.title || '?'}" doesn't match your target roles`);
  } else if (hasPartialRole && !hasFullRole) {
    missing.push('title only loosely matches a target role (partial credit)');
  }
  if (!hasFullLoc && !hasPartialLoc) {
    missing.push(`location "${j.location || 'unknown'}" isn't on your list and isn't remote`);
  } else if (hasPartialLoc && !hasFullLoc) {
    missing.push(`location "${j.location || ''}" only partially matches (city/state only)`);
  }
  if (!matched.has('sponsors visas')) {
    missing.push('no explicit visa sponsorship signal');
  }
  if (missing.length) lines.push('Drag: ' + missing.join('; '));

  if (j.score < 70) lines.push('Below your min_score threshold — tailor will skip this one.');
  return lines.join('\n');
}

// ── Headless pipeline ──────────────────────────────────────────────────────

async function refreshHeadlessSection() {
  // Load sponsorship URLs into the headless section display
  try {
    const d = await apiFetch('/api/settings');
    const urls = d.discovery_urls || [];
    const sponsorEl = document.getElementById('sponsor-urls-list');
    if (sponsorEl) {
      if (urls.length === 0) {
        sponsorEl.textContent = 'No sponsorship source URLs added yet.';
      } else {
        sponsorEl.innerHTML = urls.map(u =>
          `<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.25rem">
            <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${u}</span>
            <button class="btn btn-slate btn-sm" onclick="removeSponsorUrl('${encodeURIComponent(u)}')">✕</button>
          </div>`
        ).join('');
      }
    }
  } catch (_) {}
}

async function runHeadlessPipeline(stage) {
  const logEl = document.getElementById('headless-log');
  if (logEl) logEl.innerHTML = `<span style="color:var(--dim)">Starting ${stage}…</span>`;
  if (_pipelineEventSource) { _pipelineEventSource.close(); _pipelineEventSource = null; }
  try {
    const d = await apiFetch('/api/pipeline/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage, mode: 'headed' }) });
    streamPipelineRun(d.run_id, logEl);
  } catch (e) { toast(e.message, 'error'); }
}

async function pruneArtifacts() {
  const keep = parseInt(document.getElementById('prune-keep')?.value || '50', 10);
  const resultEl = document.getElementById('prune-result');
  if (resultEl) resultEl.textContent = 'Pruning…';
  try {
    const d = await apiFetch(`/api/artifacts/prune?keep=${keep}`, { method: 'POST' });
    const msg = d.deleted_files === 0
      ? 'Nothing to prune'
      : `Removed ${d.deleted_files} file${d.deleted_files === 1 ? '' : 's'}, ${d.deleted_rows} record${d.deleted_rows === 1 ? '' : 's'}`;
    if (resultEl) resultEl.textContent = msg;
    toast(msg, 'info');
    refreshDocuments();
  } catch (e) {
    if (resultEl) resultEl.textContent = '';
    toast(e.message, 'error');
  }
}

async function deleteArtifact(artifactId, btn) {
  if (!confirm('Delete this document? This cannot be undone.')) return;
  if (btn) btn.disabled = true;
  try {
    await apiFetch(`/api/artifacts/${artifactId}`, { method: 'DELETE' });
    toast('Deleted', 'info');
    refreshDocuments();
  } catch (e) {
    toast(e.message, 'error');
    if (btn) btn.disabled = false;
  }
}

async function clearAllScreenshots() {
  if (!confirm('Delete ALL apply screenshots? This will free disk space but you will lose visual proof of past apply runs.')) return;
  try {
    const d = await apiFetch('/api/artifacts/screenshots/all', { method: 'DELETE' });
    toast(`Removed ${d.deleted_files} files, ${d.deleted_rows} records`, 'info');
    refreshDocuments();
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function addSponsorUrl(url) {
  url = (url || '').trim();
  if (!url || !url.startsWith('http')) { toast('Enter a valid URL starting with http', 'error'); return; }
  try {
    const d = await apiFetch('/api/settings');
    const urls = d.discovery_urls || [];
    if (urls.includes(url)) { toast('URL already in list', 'info'); return; }
    urls.push(url);
    await apiFetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ discovery_urls: urls }) });
    toast('Sponsorship source added', 'success');
    const inp = document.getElementById('sponsor-url-input');
    if (inp) inp.value = '';
    refreshHeadlessSection();
  } catch (e) { toast(e.message, 'error'); }
}

async function removeSponsorUrl(encodedUrl) {
  const url = decodeURIComponent(encodedUrl);
  try {
    const d = await apiFetch('/api/settings');
    const urls = (d.discovery_urls || []).filter(u => u !== url);
    await apiFetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ discovery_urls: urls }) });
    toast('URL removed', 'info');
    refreshHeadlessSection();
  } catch (e) { toast(e.message, 'error'); }
}

async function addJobUrl() {
  const input = document.getElementById('add-url-input');
  const url = input?.value?.trim();
  if (!url) return;
  input.disabled = true;
  try {
    const d = await apiFetch('/api/jobs/add-url', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }) });
    if (d.error) { toast(`Failed: ${d.error}`, 'error'); }
    else {
      toast(d.duplicate ? `Already in pipeline: ${d.title}` : `Added: ${d.title}`, d.duplicate ? 'info' : 'success');
      if (input) input.value = '';
      if (typeof refreshOps === 'function') refreshOps();
    }
  } catch (e) { toast(e.message, 'error'); }
  finally { if (input) input.disabled = false; }
}
