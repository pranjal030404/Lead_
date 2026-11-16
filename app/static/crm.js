/* UI for: Tasks, Security (MFA / sign out everywhere), Integrations (webhooks) and the Audit log.
   Loaded after app.js and shares its helpers (api, post, patch, del, esc, pill, ago, table, toast). */

ADMIN_ONLY['/integrations'] = 1;
ADMIN_ONLY['/auditlog'] = 1;
ADMIN_ONLY['/growth'] = 1;

// ---------------------------------------------------------------- tasks ---
function dueLabel(task) {
  if (!task.due_at) return '<span class="faint">no due date</span>';
  const overdue = task.status === 'OPEN' && new Date(task.due_at) < new Date();
  return `<span class="${overdue ? 'pill HOT' : 'faint'}">${esc(date(task.due_at))}${overdue ? ' · overdue' : ''}</span>`;
}

routes['/tasks'] = async (params) => {
  const status = params.status || 'OPEN';
  const [data, leads] = await Promise.all([
    api(`/api/tasks?status=${encodeURIComponent(status)}`),
    api('/api/leads?limit=100').catch(() => ({ leads: [] })),
  ]);
  const leadName = new Map((leads.leads || []).map((l) => [l.id, l.business_name]));
  const rows = data.tasks.map((t) => `<tr>
    <td><input type="checkbox" ${t.status === 'DONE' ? 'checked' : ''}
          onchange="toggleTask(${t.id}, this.checked)" title="Mark done"></td>
    <td><b style="${t.status === 'DONE' ? 'text-decoration:line-through;opacity:.6' : ''}">${esc(t.title)}</b>
      ${t.notes ? `<div class="faint" style="font-size:11.5px">${esc(t.notes)}</div>` : ''}</td>
    <td>${t.lead_id ? `<a href="#/leads" onclick="openLead(${t.lead_id});return false">${esc(leadName.get(t.lead_id) || '#' + t.lead_id)}</a>` : '<span class="faint">—</span>'}</td>
    <td class="nowrap">${dueLabel(t)}</td>
    <td class="right"><button class="sm danger" onclick="removeTask(${t.id})">✕</button></td></tr>`);

  const tab = (value, label) =>
    `<a class="btn ${status === value ? 'primary' : ''}" href="#/tasks?status=${value}">${label}</a>`;
  view.innerHTML = `
    <div class="page-head"><div><h1>Tasks</h1>
      <div class="subtitle">Follow-ups that aren't tied to a date on a lead. Logging a positive reply adds one automatically.</div></div>
      <div class="flex">${tab('OPEN', 'Open')}${tab('DONE', 'Done')}${tab('ALL', 'All')}<a class="btn" href="/api/tasks/export">Export CSV</a></div></div>
    <div class="card" style="margin-bottom:16px"><div class="row">
      <div><label>Task</label><input id="tkTitle" placeholder="Call the owner about the new site"></div>
      <div class="shrink" style="width:230px"><label>Lead (optional)</label>
        <select id="tkLead"><option value="">—</option>${(leads.leads || []).map((l) =>
          `<option value="${l.id}">${esc(l.business_name)}</option>`).join('')}</select></div>
      <div class="shrink" style="width:170px"><label>Due</label><input id="tkDue" type="date"></div>
      <div class="shrink" style="align-self:flex-end"><button class="primary" onclick="addTask()">Add task</button></div>
    </div></div>
    ${table(['', 'Task', 'Lead', 'Due', ''], rows,
      status === 'DONE' ? 'Nothing completed yet.' : 'No open tasks — nice.')}`;
};

async function addTask() {
  const title = document.getElementById('tkTitle').value.trim();
  if (!title) return toast('Give the task a title', 'err');
  const lead = document.getElementById('tkLead').value;
  const due = document.getElementById('tkDue').value;
  try {
    await post('/api/tasks', { title, lead_id: lead ? Number(lead) : null,
                               due_at: due ? new Date(due + 'T09:00:00').toISOString() : null });
    toast('Task added', 'ok');
    navigate(); refreshTaskBadge();
  } catch (e) { toast(e.message, 'err'); }
}
async function toggleTask(id, done) {
  await patch(`/api/tasks/${id}`, { status: done ? 'DONE' : 'OPEN' });
  navigate(); refreshTaskBadge();
}
async function removeTask(id) { await del(`/api/tasks/${id}`); navigate(); refreshTaskBadge(); }

async function refreshTaskBadge() {
  try {
    const d = await api('/api/tasks?status=OPEN&overdue=true');
    document.querySelectorAll('[data-badge="tasks"]').forEach((n) => {
      n.textContent = d.count || ''; n.style.display = d.count ? '' : 'none';
    });
  } catch (_) { /* best effort */ }
}

// ------------------------------------------------------------- security ---
routes['/security'] = async () => {
  const mfa = await api('/api/auth/mfa');
  const u = me().user || {};
  view.innerHTML = `
    <div class="page-head"><div><h1>Security</h1>
      <div class="subtitle">Protect ${esc(u.username || 'your account')} with a second factor and control active sessions.</div></div></div>
    <div class="grid cols-2">
      <div class="card"><h3 style="margin-top:0">Two-factor authentication
        ${mfa.enabled ? '<span class="pill FRESH">on</span>' : '<span class="pill COLD">off</span>'}</h3>
        <div id="mfaBody">${mfa.enabled ? `
          <p class="faint">Sign-in asks for a 6-digit code from your authenticator app.</p>
          <label>Enter a current code to turn it off</label>
          <div class="flex"><input id="mfaCode" inputmode="numeric" maxlength="6" placeholder="123456" style="max-width:140px">
          <button class="danger" onclick="mfaDisable()">Turn off</button></div>` : `
          <p class="faint">Use Google Authenticator, 1Password, Authy or any TOTP app.</p>
          <button class="primary" onclick="mfaStart()">Set up</button>`}</div></div>
      <div class="card"><h3 style="margin-top:0">Sessions</h3>
        <p class="faint">Signs you out on every device and browser, including this one.
        Changing your password does the same.</p>
        <button class="danger" onclick="logoutAll()">Sign out everywhere</button></div>
    </div>`;
};

async function mfaStart() {
  try {
    const d = await post('/api/auth/mfa/setup');
    document.getElementById('mfaBody').innerHTML = `
      <p>1. In your authenticator app choose <b>Enter a setup key</b> and paste:</p>
      <div class="mono" style="word-break:break-all;user-select:all;padding:8px;border:1px solid var(--line,#444);border-radius:8px">${esc(d.secret)}</div>
      <p class="faint" style="margin:6px 0">On a phone, <a href="${esc(d.otpauth_uri)}">tap here to add it automatically</a>.</p>
      <p>2. Enter the 6-digit code it shows:</p>
      <div class="flex"><input id="mfaCode" inputmode="numeric" maxlength="6" placeholder="123456" style="max-width:140px">
      <button class="primary" onclick="mfaEnable()">Turn on</button></div>`;
  } catch (e) { toast(e.message, 'err'); }
}
async function mfaEnable() {
  try { await post('/api/auth/mfa/enable', { code: document.getElementById('mfaCode').value }); toast('Two-factor is on', 'ok'); navigate(); }
  catch (e) { toast(e.message, 'err'); }
}
async function mfaDisable() {
  try { await post('/api/auth/mfa/disable', { code: document.getElementById('mfaCode').value }); toast('Two-factor is off', 'ok'); navigate(); }
  catch (e) { toast(e.message, 'err'); }
}
async function logoutAll() {
  try { await post('/api/auth/logout-all'); } finally { window.location.href = '/login'; }
}

// --------------------------------------------------------- integrations ---
routes['/integrations'] = async () => {
  const [hooks, log, jobs] = await Promise.all([api('/api/webhooks'), api('/api/webhooks/deliveries?limit=20'), api('/api/jobs')]);
  const rows = hooks.webhooks.map((h) => `<tr>
    <td class="mono" style="word-break:break-all">${esc(h.url)}</td>
    <td>${h.events.map((e) => `<span class="pill">${esc(e)}</span>`).join(' ')}</td>
    <td class="faint nowrap">${ago(h.created_at)}</td>
    <td class="right nowrap"><button class="sm" onclick="testHook(${h.id})">Send test</button>
      <button class="sm danger" onclick="removeHook(${h.id})">✕</button></td></tr>`);
  const drows = log.deliveries.map((d) => `<tr>
    <td class="faint nowrap">${ago(d.at)}</td><td>${esc(d.event)}</td>
    <td>${d.error ? `<span class="pill HOT">${esc(d.error)}</span>` : `<span class="pill FRESH">${esc(d.status_code)}</span>`}</td></tr>`);
  view.innerHTML = `
    <div class="page-head"><div><h1>Integrations</h1>
      <div class="subtitle">Send events to Zapier, n8n or your own server. Each request is signed:
      <span class="mono">X-Signature: sha256=HMAC(secret, body)</span>.</div></div></div>
    <div id="hookSecret"></div>
    <div class="card" style="margin-bottom:16px"><div class="row">
      <div><label>Endpoint URL (https, public)</label><input id="whUrl" placeholder="https://hooks.example.com/leadgen"></div>
      <div class="shrink" style="align-self:flex-end"><button class="primary" onclick="addHook()">Add webhook</button></div></div>
      <div class="flex" style="margin-top:10px;flex-wrap:wrap">${hooks.events.filter((e) => e !== 'ping').map((e) =>
        `<label style="display:flex;gap:6px;align-items:center"><input type="checkbox" class="whEv" value="${esc(e)}" checked> ${esc(e)}</label>`).join('')}</div></div>
    ${table(['URL', 'Events', 'Added', ''], rows, 'No webhooks yet.')}
    <h3 style="margin-top:22px">Recent deliveries</h3>
    ${table(['When', 'Event', 'Result'], drows, 'Nothing delivered yet.')}
    <h3 style="margin-top:22px">Delivery queue</h3>
    <div class="faint" style="margin-bottom:8px">Failed deliveries are retried automatically after 1 min, 5 min, 30 min and 2 h, then marked failed.
      Pending: <b>${jobs.stats.PENDING || 0}</b> · Running: <b>${jobs.stats.RUNNING || 0}</b> · Failed: <b>${jobs.stats.FAILED || 0}</b></div>
    ${table(['Job', 'State', 'Attempts', 'Next try', 'Last error', ''], jobs.jobs.map((j) => `<tr>
      <td>#${j.id} ${esc(j.kind)}</td><td>${pill(j.status)}</td><td class="num">${j.attempts}/${j.max_attempts}</td>
      <td class="faint nowrap">${j.status === 'PENDING' ? esc(date(j.run_at, true)) : '—'}</td>
      <td class="faint" style="max-width:260px;word-break:break-word">${esc(j.last_error || '')}</td>
      <td class="right">${j.status === 'FAILED' ? `<button class="sm" onclick="retryJob(${j.id})">Retry</button>` : ''}</td></tr>`),
      'Queue is empty.')}`;
};

async function addHook() {
  const events = [...document.querySelectorAll('.whEv:checked')].map((c) => c.value).concat('ping');
  try {
    const h = await post('/api/webhooks', { url: document.getElementById('whUrl').value.trim(), events });
    await routes['/integrations']({});
    document.getElementById('hookSecret').innerHTML = `<div class="banner warn">
      <b>Signing secret — shown once:</b> <span class="mono" style="user-select:all">${esc(h.secret)}</span></div>`;
  } catch (e) { toast(e.message, 'err'); }
}
async function testHook(id) {
  const r = await post(`/api/webhooks/${id}/test`);
  toast(r.error ? `Failed: ${r.error}` : `Delivered (HTTP ${r.status_code})`, r.error ? 'err' : 'ok');
  navigate();
}
async function retryJob(id) { try { await post(`/api/jobs/${id}/retry`); toast('Queued for retry', 'ok'); navigate(); } catch (e) { toast(e.message, 'err'); } }
async function removeHook(id) { await del(`/api/webhooks/${id}`); navigate(); }

// ------------------------------------------------------------ audit log ---
routes['/auditlog'] = async () => {
  const d = await api('/api/audit-log?limit=200');
  const rows = d.entries.map((e) => `<tr>
    <td class="faint nowrap">${esc(date(e.at, true))}</td><td>${esc(e.username || '—')}</td>
    <td><span class="pill">${esc(e.method)}</span></td><td class="mono" style="word-break:break-all">${esc(e.path)}</td>
    <td>${e.status >= 400 ? `<span class="pill HOT">${esc(e.status)}</span>` : esc(e.status)}</td>
    <td class="faint mono">${esc(e.ip || '')}</td></tr>`);
  view.innerHTML = `
    <div class="page-head"><div><h1>Audit log</h1>
      <div class="subtitle">Every change made through the app: who, what, when, from where. Request contents are never recorded.</div></div></div>
    ${table(['When', 'User', 'Method', 'Path', 'Status', 'IP'], rows, 'No changes recorded yet.')}`;
};

refreshTaskBadge();
setInterval(refreshTaskBadge, 60000);

// ------------------------------------------------------------ campaigns ---
let cmpTemplates = [];

routes['/campaigns'] = async (params) => {
  if (params.id) return campaignDetail(Number(params.id));
  const [data, tpl] = await Promise.all([api('/api/campaigns'), api('/api/templates?type=email').catch(() => [])]);
  cmpTemplates = (Array.isArray(tpl) ? tpl : []).map((t) => t.name);
  const rows = data.campaigns.map((c) => `<tr onclick="location.hash='#/campaigns?id=${c.id}'" style="cursor:pointer">
    <td><b>${esc(c.name)}</b><div class="faint" style="font-size:11.5px">${c.steps.length} step${c.steps.length === 1 ? '' : 's'}</div></td>
    <td>${pill(c.status)}</td>
    <td class="num">${c.stats.enrolled}</td><td class="num">${c.stats.active}</td>
    <td class="num">${c.stats.awaiting_approval}</td><td class="num">${c.stats.sent}</td>
    <td class="num">${c.stats.replied}</td><td class="num">${c.stats.completed}</td></tr>`);
  view.innerHTML = `
    <div class="page-head"><div><h1>Campaigns</h1>
      <div class="subtitle">Multi-step email sequences. Every message still waits in <a href="#/approvals">Approve sends</a> —
      nothing goes out without you. A reply, opt-out or bad address stops a lead's sequence automatically.</div></div></div>
    <div class="card" style="margin-bottom:16px">
      <h3 style="margin-top:0">New campaign</h3>
      <div class="row"><div><label>Name</label><input id="cpName" placeholder="Dentists in Pune — website offer"></div></div>
      <label style="margin-top:10px">Steps <span class="faint">(delay counts from when the previous email was actually sent)</span></label>
      <div id="cpSteps"></div>
      <div class="flex" style="margin-top:8px"><button class="sm" onclick="addStepRow()">+ Add step</button></div>
      <div class="row" style="margin-top:12px">
        <div><label>Auto-pick leads: niche contains</label><input id="cpNiche" placeholder="(any)"></div>
        <div><label>City contains</label><input id="cpCity" placeholder="(any)"></div>
        <div class="shrink" style="width:130px"><label>Min score (0–10)</label><input id="cpMin" type="number" min="0" max="10" placeholder="e.g. 5"></div>
        <div class="shrink" style="align-self:flex-end"><button class="primary" onclick="createCampaign()">Create campaign</button></div>
      </div></div>
    ${table(['Campaign', 'Status', 'Enrolled', 'Active', 'Awaiting approval', 'Sent', 'Replied', 'Completed'], rows,
      'No campaigns yet — build your first sequence above.')}`;
  addStepRow(); addStepRow(3);
};

function addStepRow(delay = 0) {
  const wrap = document.getElementById('cpSteps');
  const n = wrap.children.length + 1;
  const opts = cmpTemplates.map((t, i) => `<option value="${esc(t)}" ${n > 1 && i === Math.min(n - 1, cmpTemplates.length - 1) ? 'selected' : ''}>${esc(t)}</option>`).join('');
  const div = document.createElement('div');
  div.className = 'row cpStep'; div.style.marginBottom = '6px';
  div.innerHTML = `<div class="shrink" style="width:60px;align-self:center"><b>#${n}</b></div>
    <div><select class="cpTpl">${opts}</select>
      <select class="cpTplB" title="Optional: A/B test a second template (half the leads get each)" style="margin-top:4px"><option value="">A/B: no second template</option>${cmpTemplates.map((t) => `<option value="${esc(t)}">B: ${esc(t)}</option>`).join('')}</select></div>
    <div class="shrink" style="width:300px;display:flex;align-items:center;gap:8px;white-space:nowrap"><input class="cpDelay" type="number" min="0" max="90" value="${delay}" title="Days to wait" style="width:80px"> <span class="faint">days after ${n === 1 ? 'enrolment' : 'previous email'}</span></div>
    <div class="shrink"><button class="sm danger" onclick="this.closest('.cpStep').remove()">✕</button></div>`;
  wrap.appendChild(div);
}

async function createCampaign() {
  const steps = [...document.querySelectorAll('.cpStep')].map((r) => ({
    template: r.querySelector('.cpTpl').value, template_b: r.querySelector('.cpTplB').value || null, delay_days: Number(r.querySelector('.cpDelay').value) || 0 }));
  const name = document.getElementById('cpName').value.trim();
  if (!name) return toast('Name the campaign', 'err');
  if (!steps.length) return toast('Add at least one step', 'err');
  const criteria = {};
  const niche = document.getElementById('cpNiche').value.trim(), city = document.getElementById('cpCity').value.trim();
  const min = document.getElementById('cpMin').value;
  if (niche) criteria.niche = niche; if (city) criteria.city = city; if (min !== '') criteria.min_score = Number(min);
  try {
    const c = await post('/api/campaigns', { name, steps, criteria: Object.keys(criteria).length ? criteria : null });
    toast('Campaign created', 'ok'); location.hash = `#/campaigns?id=${c.id}`;
  } catch (e) { toast(e.message, 'err'); }
}

async function campaignDetail(id) {
  const [c, en] = await Promise.all([api(`/api/campaigns/${id}`), api(`/api/campaigns/${id}/enrollments`)]);
  const s = c.stats, crit = c.criteria || {};
  const critText = Object.keys(crit).length
    ? Object.entries(crit).map(([k, v]) => `${esc(k.replace('_', ' '))}: ${esc(v)}`).join(' · ') : 'none set';
  const stepRows = c.steps.map((st, i) => `<tr><td>#${i + 1}</td><td>${esc(st.template)}${st.template_b ? ` <span class="faint">vs</span> ${esc(st.template_b)}` : ''}</td>
    <td class="faint">${st.delay_days === 0 ? (i === 0 ? 'immediately' : 'right after previous is sent') : st.delay_days + ' day(s) after ' + (i === 0 ? 'enrolment' : 'previous is sent')}</td></tr>`);
  const abRows = (s.variants || []).map((v) => {
    const rate = (x) => (x.sent ? Math.round((1000 * x.replied) / x.sent) / 10 : 0);
    return `<tr><td>#${v.position + 1}</td><td>A: ${esc(v.A.template)}</td><td class="num">${v.A.sent}</td><td class="num">${v.A.replied}</td><td class="num">${rate(v.A)}%</td></tr>
      <tr><td></td><td>B: ${esc(v.B.template)}</td><td class="num">${v.B.sent}</td><td class="num">${v.B.replied}</td><td class="num">${rate(v.B)}%</td></tr>`;
  });
  const abBlock = abRows.length ? `<h3 style="margin-top:20px">A/B results</h3>${table(['Step', 'Template', 'Sent', 'Replied', 'Reply rate'], abRows)}` : '';
  const rows = en.enrollments.map((e) => `<tr>
    <td><a href="#/leads" onclick="openLead(${e.lead_id});return false"><b>${esc(e.business_name)}</b></a>
      <div class="faint" style="font-size:11.5px">${esc(e.email || '')}</div></td>
    <td>${pill(e.status)}</td>
    <td>${e.status === 'ACTIVE' ? `step ${Math.min(e.next_position + 1, c.steps.length)} of ${c.steps.length}` : esc(e.stop_reason || 'finished all steps')}</td>
    <td class="faint nowrap">${e.status === 'ACTIVE' && e.next_due_at ? esc(date(e.next_due_at)) : '—'}</td>
    <td class="right">${e.status === 'ACTIVE' ? `<button class="sm danger" onclick="stopEnrollment(${id}, ${e.id})">Stop</button>` : ''}</td></tr>`);
  view.innerHTML = `
    <div class="page-head"><div><h1>${esc(c.name)} ${pill(c.status)}</h1>
      <div class="subtitle"><a href="#/campaigns">← All campaigns</a></div></div>
      <div class="flex">
        ${c.status === 'ACTIVE'
          ? `<button onclick="setCampaignStatus(${id}, 'PAUSED')">Pause</button>
             <button onclick="runCampaign(${id})">Run now</button>`
          : `<button class="primary" onclick="setCampaignStatus(${id}, 'ACTIVE')">${c.status === 'DRAFT' ? 'Activate' : 'Resume'}</button>`}
        <button class="danger" onclick="deleteCampaign(${id})">Delete</button></div></div>
    <div class="grid cols-5">${stat('Enrolled', s.enrolled)}${stat('Active', s.active)}
      ${stat('Awaiting approval', s.awaiting_approval, s.awaiting_approval ? '<a href="#/approvals">review</a>' : '')}
      ${stat('Sent', s.sent)}${stat('Replied', s.replied)}</div>
    <div class="card" style="margin:16px 0"><h3 style="margin-top:0">Add leads</h3>
      <p class="faint" style="margin:0 0 8px">Auto-pick uses this campaign's filters (${critText}) over your NEW/CONTACTED leads with an email.
      Leads that replied, opted out, are in another campaign, or have no usable email are skipped and counted.</p>
      <div class="flex"><button class="primary" onclick="enrollCriteria(${id})">Enrol matching leads</button></div></div>
    <h3>Sequence</h3>${table(['Step', 'Template', 'Timing'], stepRows)}${abBlock}
    <h3 style="margin-top:20px">Leads in this campaign</h3>
    ${table(['Lead', 'State', 'Progress / reason', 'Next step due', ''], rows, 'No leads enrolled yet.')}`;
}

async function setCampaignStatus(id, status) {
  try { await patch(`/api/campaigns/${id}`, { status }); toast(status === 'ACTIVE' ? 'Campaign active' : 'Campaign paused', 'ok'); navigate(); }
  catch (e) { toast(e.message, 'err'); }
}
async function runCampaign(id) {
  try { const r = await post(`/api/campaigns/${id}/run`); toast(`${r.queued} queued for approval · ${r.stopped} stopped · ${r.completed} completed`, 'ok'); navigate(); }
  catch (e) { toast(e.message, 'err'); }
}
async function enrollCriteria(id) {
  try {
    const r = await post(`/api/campaigns/${id}/enroll`, { use_criteria: true });
    const skipped = Object.entries(r.skipped).map(([k, v]) => `${v} ${k}`).join(', ');
    toast(`Enrolled ${r.enrolled}${skipped ? ' · skipped: ' + skipped : ''}`, r.enrolled ? 'ok' : '');
    navigate();
  } catch (e) { toast(e.message, 'err'); }
}
async function stopEnrollment(cid, eid) { await post(`/api/campaigns/${cid}/enrollments/${eid}/stop`); navigate(); }
async function deleteCampaign(id) {
  if (!confirm('Delete this campaign? Messages from it that are still waiting for approval will be cancelled.')) return;
  await del(`/api/campaigns/${id}`); location.hash = '#/campaigns';
}

// ------------------------------------------------------------- pipeline ---
// Replaces the read-only board in app.js: drag a card to another column to move the lead;
// admins can configure stages (labels, colours, follow-up days, add/remove/reorder).
const fmtMoney = (n, cur = 'INR') => {
  try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur }).format(n); }
  catch (_) { return `${cur} ${Number(n).toFixed(2)}`; }
};

routes['/pipeline'] = async () => {
  const stages = await api('/api/pipeline');
  const columns = stages.map((stage) => `
    <div class="card pipeCol" data-stage="${esc(stage.name)}"
         ondragover="event.preventDefault();this.style.outline='2px dashed ' + '${esc(stage.color)}'"
         ondragleave="this.style.outline=''" ondrop="dropLead(event, '${esc(stage.name)}')">
      <div class="flex between" style="margin-bottom:10px">
        <b style="color:${esc(stage.color)}">${esc(stage.label)}</b><span class="faint">${stage.count}</span></div>
      ${stage.leads.map((lead) => `
        <div draggable="true" ondragstart="event.dataTransfer.setData('text/plain', ${lead.id})"
             onclick="openLead(${lead.id})" style="padding:8px 0;border-top:1px solid var(--line-soft);cursor:grab">
          <div class="flex" style="gap:6px">${scoreChip(lead.lead_score, lead.lead_priority)}
            <b style="font-size:12.5px">${esc(lead.business_name)}</b></div>
          <div class="faint" style="font-size:11px">${esc(lead.city || '')}
            ${lead.last_contacted_date ? `· ${ago(lead.last_contacted_date)}` : ''}</div></div>`).join('')
        || '<div class="faint" style="font-size:12px">Drop a lead here</div>'}
    </div>`).join('');
  view.innerHTML = `
    <div class="page-head"><div><h1>Pipeline</h1>
      <div class="subtitle">Every deal, by stage. Drag a card to move it, click it to open the lead.</div></div>
      <div class="flex">${isAdmin() ? '<button onclick="toggleStageConfig()">Configure stages</button>' : ''}</div></div>
    <div id="stageConfig" style="display:none"></div>
    <div class="grid cols-4">${columns}</div>`;
};

async function dropLead(event, stage) {
  event.preventDefault();
  event.currentTarget.style.outline = '';
  const id = Number(event.dataTransfer.getData('text/plain'));
  if (!id) return;
  try { await post(`/api/leads/${id}/stage`, { stage }); navigate(); } catch (e) { toast(e.message, 'err'); }
}

async function toggleStageConfig() {
  const box = document.getElementById('stageConfig');
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  const { stages } = await api('/api/pipeline/stages');
  box.style.display = '';
  box.innerHTML = `<div class="card" style="margin-bottom:16px"><h3 style="margin-top:0">Stages</h3>
    <p class="faint" style="margin-top:0">Core stages (🔒) can be relabelled and recoloured but not removed - replies, meetings and proposals move leads through them.
    “Follow-up days” is how long a lead may sit in the stage before it shows up as stale.</p>
    ${stages.map((s, i) => `<div class="row" style="margin-bottom:6px;align-items:center">
      <div class="shrink" style="width:30px">${s.is_core ? '🔒' : ''}</div>
      <div><input id="sl${s.id}" value="${esc(s.label)}" maxlength="40"></div>
      <div class="shrink" style="width:70px"><input id="sc${s.id}" type="color" value="${esc(s.color || '#64748b')}"></div>
      <div class="shrink" style="width:90px"><input id="sd${s.id}" type="number" min="0" max="90" value="${s.auto_follow_up_days || 0}" title="Follow-up days"></div>
      <div class="shrink faint" style="width:70px">${s.count} lead${s.count === 1 ? '' : 's'}</div>
      <div class="shrink nowrap"><button class="sm" onclick="saveStage(${s.id})">Save</button>
        <button class="sm" ${i === 0 ? 'disabled' : ''} onclick="moveStage(${s.id}, -1)">↑</button>
        <button class="sm" ${i === stages.length - 1 ? 'disabled' : ''} onclick="moveStage(${s.id}, 1)">↓</button>
        ${s.is_core ? '' : `<button class="sm danger" onclick="removeStage(${s.id}, ${s.count})">✕</button>`}</div></div>`).join('')}
    <div class="row" style="margin-top:12px"><div><label>New stage</label><input id="nsLabel" placeholder="e.g. Demo booked"></div>
      <div class="shrink" style="width:70px"><label>Colour</label><input id="nsColor" type="color" value="#14b8a6"></div>
      <div class="shrink" style="align-self:flex-end"><button class="primary" onclick="addStage()">Add stage</button></div></div></div>`;
  window.__stages = stages;
}
async function saveStage(id) {
  try {
    await patch(`/api/pipeline/stages/${id}`, { label: document.getElementById('sl' + id).value,
      color: document.getElementById('sc' + id).value, auto_follow_up_days: Number(document.getElementById('sd' + id).value) || 0 });
    toast('Saved', 'ok'); navigate();
  } catch (e) { toast(e.message, 'err'); }
}
async function moveStage(id, dir) {
  const ids = window.__stages.map((s) => s.id), i = ids.indexOf(id), j = i + dir;
  if (j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  try { await post('/api/pipeline/stages/reorder', { ids }); navigate(); } catch (e) { toast(e.message, 'err'); }
}
async function addStage() {
  try {
    await post('/api/pipeline/stages', { label: document.getElementById('nsLabel').value, color: document.getElementById('nsColor').value });
    toast('Stage added', 'ok'); navigate();
  } catch (e) { toast(e.message, 'err'); }
}
async function removeStage(id, count) {
  let target = '';
  if (count) {
    target = prompt(`${count} lead(s) are in this stage. Move them to which stage? (e.g. CONTACTED)`, 'CONTACTED');
    if (!target) return;
  }
  try { await del(`/api/pipeline/stages/${id}${target ? '?move_to=' + encodeURIComponent(target.trim().toUpperCase()) : ''}`); toast('Stage removed', 'ok'); navigate(); }
  catch (e) { toast(e.message, 'err'); }
}

// ------------------------------------------------------------- meetings ---
routes['/meetings'] = async (params) => {
  const when = params.when || 'upcoming';
  const [data, leads] = await Promise.all([api(`/api/meetings?when=${when}`), api('/api/leads?limit=100').catch(() => ({ leads: [] }))]);
  const rows = data.meetings.map((m) => `<tr>
    <td class="nowrap"><b>${esc(new Date(m.starts_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }))}</b>
      <div class="faint" style="font-size:11.5px">${m.duration_min} min</div></td>
    <td><b>${esc(m.title)}</b>${m.location ? `<div class="faint" style="font-size:11.5px">${esc(m.location)}</div>` : ''}</td>
    <td><a href="#/leads" onclick="openLead(${m.lead_id});return false">${esc(m.business_name)}</a></td>
    <td>${pill(m.status)}</td>
    <td class="right nowrap">
      ${m.status === 'SCHEDULED' ? `<button class="sm" onclick="setMeeting(${m.id}, 'COMPLETED')">Done</button>
        <button class="sm" onclick="setMeeting(${m.id}, 'NO_SHOW')">No-show</button>
        <button class="sm" onclick="setMeeting(${m.id}, 'CANCELLED')">Cancel</button>` : ''}
      <a class="btn sm" href="/api/meetings/${m.id}/ics" title="Add to calendar">📅 .ics</a>
      <button class="sm danger" onclick="deleteMeeting(${m.id})">✕</button></td></tr>`);
  const tab = (v, l) => `<a class="btn ${when === v ? 'primary' : ''}" href="#/meetings?when=${v}">${l}</a>`;
  view.innerHTML = `
    <div class="page-head"><div><h1>Meetings</h1>
      <div class="subtitle">Booking a meeting moves the lead to “Meeting set”, stops any email sequence, and adds a prep task.
      Marking it done adds a “Send proposal” task.</div></div><div class="flex">${tab('upcoming', 'Upcoming')}${tab('past', 'Past')}
      <a class="btn" href="/api/meetings/calendar.ics" title="Import all upcoming meetings into Google/Apple/Outlook calendar">📅 Calendar (.ics)</a>
      <a class="btn" href="/api/meetings/export">CSV</a></div></div>
    <div class="card" style="margin-bottom:16px"><div class="row">
      <div class="shrink" style="width:220px"><label>Lead</label><select id="mtLead">${(leads.leads || []).map((l) =>
        `<option value="${l.id}">${esc(l.business_name)}</option>`).join('')}</select></div>
      <div><label>Title</label><input id="mtTitle" placeholder="Intro call"></div>
      <div class="shrink" style="width:210px"><label>When</label><input id="mtWhen" type="datetime-local"></div>
      <div class="shrink" style="width:90px"><label>Minutes</label><input id="mtDur" type="number" min="5" max="480" value="30"></div>
      <div><label>Location / link</label><input id="mtLoc" placeholder="https://meet.google.com/…"></div>
      <div class="shrink" style="align-self:flex-end"><button class="primary" onclick="addMeeting()">Schedule</button></div></div></div>
    ${table(['When', 'Meeting', 'Lead', 'Status', ''], rows, when === 'past' ? 'No past meetings.' : 'Nothing scheduled.')}`;
};
async function addMeeting() {
  const when = document.getElementById('mtWhen').value, title = document.getElementById('mtTitle').value.trim();
  if (!title || !when) return toast('Add a title and a date/time', 'err');
  try {
    const m = await post('/api/meetings', { lead_id: Number(document.getElementById('mtLead').value), title,
      starts_at: new Date(when).toISOString(), duration_min: Number(document.getElementById('mtDur').value) || 30,
      location: document.getElementById('mtLoc').value });
    toast(m.conflicts.length ? `Scheduled — overlaps with “${m.conflicts[0].title}”` : 'Meeting scheduled', m.conflicts.length ? '' : 'ok');
    navigate();
  } catch (e) { toast(e.message, 'err'); }
}
async function setMeeting(id, status) { await patch(`/api/meetings/${id}`, { status }); navigate(); }
async function deleteMeeting(id) { await del(`/api/meetings/${id}`); navigate(); }

// ------------------------------------------------------------ proposals ---
routes['/proposals'] = async (params) => {
  if (params.id) return proposalDetail(Number(params.id));
  if (params.new) return proposalBuilder(params);
  const status = params.status || '';
  const [data, rev] = await Promise.all([api('/api/proposals' + (status ? `?status=${status}` : '')), api('/api/proposals/revenue')]);
  const rows = data.proposals.map((p) => `<tr onclick="location.hash='#/proposals?id=${p.id}'" style="cursor:pointer">
    <td class="mono nowrap">${esc(p.number)}</td><td><b>${esc(p.title)}</b>
      <div class="faint" style="font-size:11.5px">${esc(p.business_name || '')}</div></td>
    <td>${pill(p.effective_status)}</td><td class="num nowrap">${esc(fmtMoney(p.total, p.currency))}</td>
    <td class="faint nowrap">${ago(p.updated_at)}</td></tr>`);
  const tab = (v, l) => `<a class="btn ${status === v ? 'primary' : ''}" href="#/proposals${v ? '?status=' + v : ''}">${l}</a>`;
  view.innerHTML = `
    <div class="page-head"><div><h1>Proposals</h1>
      <div class="subtitle">Quote a lead, send a private link they can accept or decline, and track won revenue.</div></div>
      <div class="flex"><a class="btn" href="/api/proposals/export">Export CSV</a><a class="btn primary" href="#/proposals?new=1">New proposal</a></div></div>
    <div class="grid cols-5">${stat('Won revenue', fmtMoney(rev.won_total))}${stat('Open (sent)', fmtMoney(rev.open_value))}
      ${stat('Accepted', rev.accepted)}${stat('Declined', rev.declined)}${stat('Win rate', rev.win_rate === null ? '—' : rev.win_rate + '%')}</div>
    ${rev.by_month.length ? `<div class="card" style="margin:16px 0"><b>Won by month</b><div class="faint" style="margin-top:6px">
      ${rev.by_month.map((m) => `${esc(m.month)}: <b>${esc(fmtMoney(m.won))}</b>`).join(' · ')}</div></div>` : ''}
    <div class="flex" style="margin:16px 0 8px">${tab('', 'All')}${tab('DRAFT', 'Drafts')}${tab('SENT', 'Sent')}${tab('ACCEPTED', 'Accepted')}${tab('DECLINED', 'Declined')}${tab('EXPIRED', 'Expired')}</div>
    ${table(['No.', 'Proposal', 'Status', 'Total', 'Updated'], rows, 'No proposals yet.')}`;
};

async function proposalBuilder(params) {
  const leads = await api('/api/leads?limit=100').catch(() => ({ leads: [] }));
  view.innerHTML = `
    <div class="page-head"><div><h1>New proposal</h1><div class="subtitle"><a href="#/proposals">← All proposals</a></div></div></div>
    <div class="card"><div class="row">
      <div class="shrink" style="width:240px"><label>Lead</label><select id="ppLead">${(leads.leads || []).map((l) =>
        `<option value="${l.id}" ${String(l.id) === params.lead ? 'selected' : ''}>${esc(l.business_name)}</option>`).join('')}</select></div>
      <div><label>Title</label><input id="ppTitle" placeholder="Website redesign + SEO"></div>
      <div class="shrink" style="width:90px"><label>Currency</label><input id="ppCur" value="INR" maxlength="3"></div></div>
      <label style="margin-top:12px">Line items</label><div id="ppItems"></div>
      <div class="flex" style="margin-top:6px"><button class="sm" onclick="addPpItem()">+ Add item</button></div>
      <div class="row" style="margin-top:12px">
        <div class="shrink" style="width:120px"><label>Discount %</label><input id="ppDisc" type="number" min="0" max="100" value="0" oninput="ppTotals()"></div>
        <div class="shrink" style="width:120px"><label>Tax %</label><input id="ppTax" type="number" min="0" max="100" value="0" oninput="ppTotals()"></div>
        <div class="shrink" style="width:170px"><label>Valid until</label><input id="ppValid" type="date"></div>
        <div style="text-align:right;align-self:flex-end"><div class="faint" id="ppSub"></div><b style="font-size:20px" id="ppTotal"></b></div></div>
      <label style="margin-top:12px">Notes / terms (shown to the client)</label><textarea id="ppNotes" rows="4" placeholder="Payment terms, timeline, what's included…"></textarea>
      <div class="flex" style="margin-top:12px"><button class="primary" onclick="saveProposal()">Save draft</button></div></div>`;
  addPpItem(); ppTotals();
}
function addPpItem() {
  const d = document.createElement('div');
  d.className = 'row ppItem'; d.style.marginBottom = '6px';
  d.innerHTML = `<div><input class="ppDesc" placeholder="Description"></div>
    <div class="shrink" style="width:80px"><input class="ppQty" type="number" min="0.01" step="any" value="1" oninput="ppTotals()"></div>
    <div class="shrink" style="width:130px"><input class="ppPrice" type="number" min="0" step="any" value="0" oninput="ppTotals()"></div>
    <div class="shrink"><button class="sm danger" onclick="this.closest('.ppItem').remove();ppTotals()">✕</button></div>`;
  document.getElementById('ppItems').appendChild(d);
}
function ppValues() {
  return [...document.querySelectorAll('.ppItem')].map((r) => ({ description: r.querySelector('.ppDesc').value.trim(),
    qty: Number(r.querySelector('.ppQty').value), unit_price: Number(r.querySelector('.ppPrice').value) }));
}
function ppTotals() {   // display only - the server recomputes every figure exactly
  const sub = ppValues().reduce((a, i) => a + (i.qty || 0) * (i.unit_price || 0), 0);
  const disc = sub * (Number(document.getElementById('ppDisc').value) || 0) / 100;
  const tax = (sub - disc) * (Number(document.getElementById('ppTax').value) || 0) / 100;
  const cur = document.getElementById('ppCur').value.toUpperCase() || 'INR';
  document.getElementById('ppSub').textContent = `Subtotal ${fmtMoney(sub, cur)}`;
  document.getElementById('ppTotal').textContent = fmtMoney(sub - disc + tax, cur);
}
async function saveProposal() {
  const items = ppValues();
  if (!document.getElementById('ppTitle').value.trim()) return toast('Give the proposal a title', 'err');
  if (items.some((i) => !i.description || !(i.qty > 0))) return toast('Every item needs a description and a quantity', 'err');
  const valid = document.getElementById('ppValid').value;
  try {
    const p = await post('/api/proposals', { lead_id: Number(document.getElementById('ppLead').value),
      title: document.getElementById('ppTitle').value.trim(), currency: document.getElementById('ppCur').value || 'INR', items,
      discount_pct: Number(document.getElementById('ppDisc').value) || 0, tax_pct: Number(document.getElementById('ppTax').value) || 0,
      valid_until: valid || null, notes: document.getElementById('ppNotes').value });
    toast('Draft saved', 'ok'); location.hash = `#/proposals?id=${p.id}`;
  } catch (e) { toast(e.message, 'err'); }
}

async function proposalDetail(id) {
  const p = await api(`/api/proposals/${id}`);
  const link = `${location.origin}/p/${p.share_token}`;
  const st = p.effective_status;
  const lines = p.items.map((i) => `<tr><td>${esc(i.description)}</td><td class="num">${esc(i.qty)}</td>
    <td class="num">${esc(fmtMoney(i.unit_price, p.currency))}</td><td class="num">${esc(fmtMoney(i.line_total, p.currency))}</td></tr>`).join('');
  view.innerHTML = `
    <div class="page-head"><div><h1>${esc(p.title)} ${pill(st)}</h1>
      <div class="subtitle"><a href="#/proposals">← All proposals</a> · ${esc(p.number)} · ${esc(p.business_name || '')}</div></div>
      <div class="flex"><a class="btn" href="/api/proposals/${id}/pdf" target="_blank" rel="noopener">Download PDF</a>
        ${p.status === 'DRAFT' ? `<button class="primary" onclick="sendProposal(${id}, false)">Mark as sent</button>
           <button onclick="sendProposal(${id}, true)" title="Queues an email with the link for your approval">Send by email…</button>
           <button class="danger" onclick="deleteProposal(${id})">Delete</button>` : ''}
        ${st === 'SENT' ? `<button class="primary" onclick="recordDecision(${id}, true)">Client accepted</button>
           <button onclick="recordDecision(${id}, false)">Client declined</button>` : ''}</div></div>
    ${p.status !== 'DRAFT' ? `<div class="card" style="margin-bottom:16px"><b>Client link</b>
      <div class="mono" style="word-break:break-all;user-select:all;margin:6px 0">${esc(link)}</div>
      <div class="flex"><button class="sm" onclick="navigator.clipboard.writeText('${esc(link)}');toast('Link copied','ok')">Copy link</button>
      <a class="btn sm" href="${esc(link)}" target="_blank" rel="noopener">Open client view</a></div>
      <div class="faint" style="margin-top:6px">Anyone with this link can view and answer the proposal. ${p.valid_until ? 'Valid until ' + esc(date(p.valid_until)) + '.' : ''}</div></div>` : ''}
    ${table(['Item', 'Qty', 'Price', 'Amount'], [lines])}
    <div class="card" style="margin-top:12px;text-align:right">
      <div class="faint">Subtotal ${esc(fmtMoney(p.subtotal, p.currency))}</div>
      ${p.discount ? `<div class="faint">Discount (${esc(p.discount_pct)}%) −${esc(fmtMoney(p.discount, p.currency))}</div>` : ''}
      ${p.tax ? `<div class="faint">Tax (${esc(p.tax_pct)}%) ${esc(fmtMoney(p.tax, p.currency))}</div>` : ''}
      <b style="font-size:22px">${esc(fmtMoney(p.total, p.currency))}</b></div>
    ${p.notes ? `<div class="card" style="margin-top:12px"><b>Notes</b><div style="white-space:pre-wrap;margin-top:6px">${esc(p.notes)}</div></div>` : ''}`;
}
async function sendProposal(id, email) {
  try {
    const r = await post(`/api/proposals/${id}/send`, { queue_email: email });
    toast(email ? 'Sent — the email is waiting in Approve sends' : 'Marked as sent', 'ok'); navigate();
  } catch (e) { toast(e.message, 'err'); }
}
async function recordDecision(id, accepted) {
  if (!confirm(accepted ? 'Record that the client ACCEPTED? The lead becomes WON.' : 'Record that the client declined?')) return;
  try { await post(`/api/proposals/${id}/decision`, { accepted }); navigate(); } catch (e) { toast(e.message, 'err'); }
}
async function deleteProposal(id) {
  if (!confirm('Delete this draft?')) return;
  await del(`/api/proposals/${id}`); location.hash = '#/proposals';
}

// ------------------------------------------------------------- segments ---
const SEG_FIELDS = [['niche', 'Niche'], ['city', 'City'], ['status', 'Status'], ['source', 'Source'], ['tag', 'Tag'], ['q', 'Text contains']];

routes['/segments'] = async (params) => {
  const [data, camps] = await Promise.all([api('/api/segments'), api('/api/campaigns').catch(() => ({ campaigns: [] }))]);
  const campOpts = camps.campaigns.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const rows = data.segments.map((sg) => `<tr>
    <td><b>${esc(sg.name)}</b><div class="faint" style="font-size:11.5px">${Object.entries(sg.filters).map(([k, v]) =>
      `${esc(k.replace('_', ' '))}: ${esc(v)}`).join(' · ')}</div></td>
    <td class="num"><b>${sg.count}</b></td>
    <td class="right nowrap">
      <button class="sm" onclick="location.hash='#/segments?id=${sg.id}'">View leads</button>
      ${camps.campaigns.length ? `<select id="segc${sg.id}" style="width:150px">${campOpts}</select>
        <button class="sm" onclick="enrollSegment(${sg.id})">Enrol in campaign</button>` : ''}
      <button class="sm danger" onclick="deleteSegment(${sg.id})">✕</button></td></tr>`);
  let detail = '';
  if (params.id) {
    const d = await api(`/api/segments/${params.id}/leads`);
    detail = `<h3 style="margin-top:22px">Leads in this segment (${d.count})</h3>` + table(['Score', 'Business', 'City', 'Status', 'Email'],
      d.leads.map((l) => `<tr onclick="openLead(${l.id})" style="cursor:pointer"><td>${scoreChip(l.lead_score, l.lead_priority)}</td>
        <td><b>${esc(l.business_name)}</b></td><td>${esc(l.city || '')}</td><td>${pill(l.status)}</td>
        <td class="faint">${esc(l.email || '—')}</td></tr>`), 'No leads match right now.');
  }
  view.innerHTML = `
    <div class="page-head"><div><h1>Segments</h1>
      <div class="subtitle">Save a set of filters once and reuse it. Counts are live — a segment always reflects your leads as they are now.</div></div></div>
    <div class="card" style="margin-bottom:16px"><h3 style="margin-top:0">New segment</h3>
      <div class="row"><div><label>Name</label><input id="sgName" placeholder="Hot dentists in Pune"></div>
        <div class="shrink" style="width:130px"><label>Priority</label><select id="sgPriority"><option value="">any</option>
          <option>HOT</option><option>WARM</option><option>COLD</option></select></div>
        <div class="shrink" style="width:110px"><label>Min quality</label><input id="sgQuality" type="number" min="0" max="10" placeholder="0–10"></div>
        <div class="shrink" style="width:140px"><label>Has website</label><select id="sgWeb"><option value="">any</option><option value="1">yes</option><option value="0">no</option></select></div></div>
      <div class="row" style="margin-top:8px">${SEG_FIELDS.map(([k, label]) =>
        `<div><label>${label}</label><input id="sg_${k}"></div>`).join('')}
        <div class="shrink" style="align-self:flex-end"><button class="primary" onclick="createSegment()">Save segment</button></div></div></div>
    ${table(['Segment', 'Leads', ''], rows, 'No segments yet.')}${detail}`;
};

async function createSegment() {
  const filters = {};
  SEG_FIELDS.forEach(([k]) => { const v = document.getElementById('sg_' + k).value.trim(); if (v) filters[k] = v; });
  const pr = document.getElementById('sgPriority').value, mq = document.getElementById('sgQuality').value, web = document.getElementById('sgWeb').value;
  if (pr) filters.priority = pr;
  if (mq !== '') filters.min_quality = Number(mq);
  if (web !== '') filters.has_website = web === '1';
  const name = document.getElementById('sgName').value.trim();
  if (!name) return toast('Name the segment', 'err');
  try { await post('/api/segments', { name, filters }); toast('Segment saved', 'ok'); navigate(); } catch (e) { toast(e.message, 'err'); }
}
async function deleteSegment(id) { await del(`/api/segments/${id}`); location.hash = '#/segments'; navigate(); }
async function enrollSegment(id) {
  const cid = document.getElementById('segc' + id).value;
  try {
    const r = await post(`/api/campaigns/${cid}/enroll`, { segment_id: id });
    const skipped = Object.entries(r.skipped).map(([k, v]) => `${v} ${k}`).join(', ');
    toast(`Enrolled ${r.enrolled}${skipped ? ' · skipped: ' + skipped : ''}`, r.enrolled ? 'ok' : '');
  } catch (e) { toast(e.message, 'err'); }
}

// ------------------------------------------------- feature-based nav hiding ---
// The server enforces permissions on every request; this just stops showing menu items
// the signed-in user can't use.
const FEATURE_NAV = { search: ['#/search', '#/discover', '#/targets', '#/map'], outreach: ['#/approvals', '#/templates'],
  campaigns: ['#/campaigns', '#/segments'], meetings: ['#/meetings'], proposals: ['#/proposals'], tasks: ['#/tasks'] };
(function applyFeatureNav() {
  let tries = 0;
  const timer = setInterval(() => {
    const user = window.__me && window.__me.user;
    if (user) {
      clearInterval(timer);
      (user.denied_features || []).forEach((f) => (FEATURE_NAV[f] || []).forEach((href) => {
        document.querySelectorAll(`#nav a[href="${href}"]`).forEach((a) => { a.style.display = 'none'; });
      }));
    } else if (++tries > 40) clearInterval(timer);
  }, 250);
})();

// --------------------------------------------------------------- growth ---
async function saveGrowth(patch) {
  try { await put('/api/growth/settings', patch); toast('Saved', 'ok'); navigate(); }
  catch (e) { toast(e.message, 'err'); }
}
async function saveThresholds() {
  await saveGrowth({ mql_threshold: Number(document.getElementById('gMql').value),
                     sql_threshold: Number(document.getElementById('gSql').value) });
}
function copyCaptureUrl(url) { navigator.clipboard.writeText(url).then(() => toast('Link copied', 'ok')); }

routes['/growth'] = async () => {
  const d = await api('/api/growth');
  const s = d.settings, c = d.capture, f = d.finder, q = d.qualification;
  const fullUrl = c.url.startsWith('http') ? c.url : location.origin + c.url;
  const srcRows = d.sources.map((r) => `<tr><td>${esc(r.source)}</td><td class="num">${r.leads}</td>
    <td class="num">${r.contacted}</td><td class="num">${r.won}</td><td class="num">${r.lost}</td>
    <td class="num">${r.win_rate}%</td></tr>`);
  const emailPct = f.total_leads ? Math.round((100 * f.leads_with_email) / f.total_leads) : 0;
  view.innerHTML = `
    <div class="page-head"><div><h1>Growth</h1>
      <div class="subtitle">Admin-only controls for inbound capture, the free email finder, lead qualification and sources.</div></div></div>
    <div class="grid" style="display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">
      <div class="card"><h3 style="margin-top:0">Inbound form</h3>
        <label style="display:flex;align-items:center;gap:8px"><input type="checkbox" style="width:auto" ${s.capture_enabled ? 'checked' : ''}
          onchange="saveGrowth({capture_enabled:this.checked})"> Form is live</label>
        <div class="faint" style="margin:8px 0">Share this link or put it on your website. Add <span class="mono">?utm_source=…&amp;utm_campaign=…</span> to track where leads come from.</div>
        <div class="mono" style="word-break:break-all">${esc(fullUrl)}</div>
        ${c.public_url_set ? '' : '<div class="faint" style="margin-top:6px">Set PUBLIC_URL in .env so links are correct outside this browser.</div>'}
        <div class="flex" style="margin-top:10px"><button class="sm" onclick="copyCaptureUrl('${esc(fullUrl)}')">Copy link</button>
          <a class="btn sm" href="${esc(c.url)}" target="_blank" rel="noopener">Open</a></div>
        <div style="margin-top:10px">${c.inbound_leads} inbound leads &middot; ${c.last_7_days} in the last 7 days</div></div>
      <div class="card"><h3 style="margin-top:0">Free email finder</h3>
        <label style="display:flex;align-items:center;gap:8px"><input type="checkbox" style="width:auto" ${s.finder_enabled ? 'checked' : ''}
          onchange="saveGrowth({finder_enabled:this.checked})"> Run automatically during enrichment</label>
        <div class="faint" style="margin:8px 0">Deep site crawl, mail-server check, then name-pattern guesses. Guesses are never sent to.</div>
        <div>${f.leads_with_email} of ${f.total_leads} leads have an email (${emailPct}%)</div>
        <div>${f.guessed_addresses} guessed addresses waiting for review</div></div>
      <div class="card"><h3 style="margin-top:0">Lead qualification</h3>
        <div class="faint" style="margin-bottom:8px">Inbound enquiries and leads whose overall score (0–100) is at or above the SQL line are sales-ready; the MQL line marks leads worth nurturing.</div>
        <div class="flex" style="gap:12px;align-items:end">
          <div><label>MQL from score</label><input id="gMql" type="number" min="0" max="100" value="${s.mql_threshold}" style="width:90px"></div>
          <div><label>SQL from score</label><input id="gSql" type="number" min="0" max="100" value="${s.sql_threshold}" style="width:90px"></div>
          <button class="sm" onclick="saveThresholds()">Save</button></div>
        <div style="margin-top:12px"><b>${q.SQL}</b> SQL &middot; <b>${q.MQL}</b> MQL &middot; ${q.UNQUALIFIED} unqualified <span class="faint">(open leads)</span></div></div>
    </div>
    <h3 style="margin-top:24px">Lead sources</h3>
    ${table(['Source', 'Leads', 'Contacted', 'Won', 'Lost', 'Win rate'], srcRows, 'No leads yet.')}`;
};
