/* Arthvex LeadGen - single-page UI, no build step, no dependencies. */

const view = document.getElementById('view');
const drawerRoot = document.getElementById('drawerRoot');

// ------------------------------------------------------------- helpers ---
async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (response.status === 401) { window.location.href = '/login'; throw new Error('auth'); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

const post = (path, data) => api(path, { method: 'POST', body: JSON.stringify(data ?? {}) });
const patch = (path, data) => api(path, { method: 'PATCH', body: JSON.stringify(data) });
const del = (path) => api(path, { method: 'DELETE' });

function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function toast(message, kind = '') {
  const existing = document.getElementById('toast');
  if (existing) existing.remove();
  const node = document.createElement('div');
  node.id = 'toast';
  node.className = kind;
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 3800);
}

function date(value, withTime = false) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 10);
  return withTime ? parsed.toLocaleString() : parsed.toLocaleDateString();
}

function ago(value) {
  if (!value) return '—';
  const days = Math.floor((Date.now() - new Date(value).getTime()) / 86400000);
  if (Number.isNaN(days)) return '—';
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  return `${days}d ago`;
}

const pill = (value) => value ? `<span class="pill ${esc(value)}">${esc(String(value).replace(/_/g, ' '))}</span>` : '';
const scoreChip = (score, priority) =>
  `<span class="score ${priority === 'HOT' ? 's-hot' : priority === 'WARM' ? 's-warm' : ''}">${score ?? 0}</span>`;

function stat(label, value, hint = '', cls = '') {
  return `<div class="card stat ${cls}">
    <div class="label">${esc(label)}</div>
    <div class="value">${esc(value)}</div>
    ${hint ? `<div class="hint">${hint}</div>` : ''}
  </div>`;
}

function table(headers, rows, emptyMessage = 'Nothing here yet.') {
  if (!rows.length) return `<div class="card"><div class="empty">${esc(emptyMessage)}</div></div>`;
  return `<div class="table-wrap"><table>
    <thead><tr>${headers.map((h) => `<th class="${h.num ? 'num' : ''}">${esc(h.label ?? h)}</th>`).join('')}</tr></thead>
    <tbody>${rows.join('')}</tbody></table></div>`;
}

// -------------------------------------------------------------- router ---
const routes = {};
let currentPath = '';

function navigate() {
  const hash = window.location.hash.slice(1) || '/dashboard';
  const [path, queryString] = hash.split('?');
  currentPath = path;
  const params = Object.fromEntries(new URLSearchParams(queryString || ''));

  document.querySelectorAll('#nav a').forEach((link) => {
    link.classList.toggle('active', link.getAttribute('href') === `#${path}`);
  });

  const render = routes[path] || routes['/dashboard'];
  view.innerHTML = '<div class="loading">Loading...</div>';
  Promise.resolve(render(params)).catch((error) => {
    view.innerHTML = `<div class="banner err">${esc(error.message)}</div>`;
  });
}

window.addEventListener('hashchange', navigate);

async function refreshBadges() {
  try {
    const stats = await api('/api/stats');
    const set = (name, value, quiet = false) => {
      document.querySelectorAll(`[data-badge="${name}"]`).forEach((node) => {
        node.textContent = value || '';
        node.style.display = value ? '' : 'none';
        node.classList.toggle('quiet', quiet);
      });
    };
    set('leads', stats.total_leads, true);
    set('followups', stats.follow_ups_due);
    set('approvals', stats.pending_approval);
  } catch (_) { /* badge refresh is best-effort */ }
}

// ----------------------------------------------------------- dashboard ---
routes['/dashboard'] = async () => {
  const [stats, conversions] = await Promise.all([
    api('/api/stats'), api('/api/stats/conversions'),
  ]);
  const quota = stats.quota.google;

  const warnings = [];
  if (quota.warning) {
    warnings.push(`<div class="banner warn">API usage is at ${quota.pct}% of today's cap
      (${quota.used_today}/${quota.cap}). Searches stop automatically when it hits 100%.</div>`);
  }
  if (stats.unresolved_failures) {
    warnings.push(`<div class="banner err">${stats.unresolved_failures} unresolved job
      failure(s). <a href="#/automation" style="color:inherit">Review them</a>.</div>`);
  }
  if (stats.pending_approval) {
    warnings.push(`<div class="banner info">${stats.pending_approval} message(s) waiting for
      your approval. <a href="#/approvals" style="color:inherit">Review the queue</a>.</div>`);
  }

  const funnel = conversions.steps.map((step) => `
    <div style="margin-bottom:10px">
      <div class="flex between" style="font-size:12px">
        <span>${esc(step.from.replace(/_/g, ' '))} → ${esc(step.to.replace(/_/g, ' '))}</span>
        <span class="muted">${step.to_count}/${step.from_count} · <b>${step.rate}%</b></span>
      </div>
      <div class="bar"><span style="width:${Math.min(step.rate, 100)}%"></span></div>
    </div>`).join('');

  const niches = stats.by_niche.map((row) => `<tr>
      <td>${esc(row.niche)}</td><td class="num">${row.n}</td>
      <td class="num" style="color:var(--hot)">${row.hot}</td></tr>`);
  const cities = stats.by_city.map((row) => `<tr>
      <td>${esc(row.city)}</td><td class="num">${row.n}</td>
      <td class="num" style="color:var(--hot)">${row.hot}</td></tr>`);

  view.innerHTML = `
    <div class="page-head">
      <div><h1>Dashboard</h1>
        <div class="subtitle">${stats.new_this_week} new leads this week</div></div>
      <div class="flex">
        <a class="btn" href="#/followups">Today's follow-ups</a>
        <a class="btn primary" href="#/search">New search</a>
      </div>
    </div>
    ${warnings.join('')}
    <div class="grid cols-5" style="margin-bottom:14px">
      ${stat('Total leads', stats.total_leads)}
      ${stat('Hot leads', stats.hot_leads, 'score 8-10', 'hot')}
      ${stat('No website', stats.no_website, 'easiest pitch', 'accent')}
      ${stat('Follow-ups due', stats.follow_ups_due)}
      ${stat('Deals won', stats.won, `${stats.won_this_month} this month`, 'ok')}
    </div>
    <div class="grid cols-2">
      <div class="card"><h3>Conversion funnel</h3>${funnel ||
        '<div class="empty">No pipeline movement yet.</div>'}
        <div class="muted" style="font-size:12px;margin-top:12px">
          ${conversions.results_seen} search results seen →
          ${conversions.reached.NEW} leads kept (${conversions.search_to_lead}%)
        </div>
      </div>
      <div class="card"><h3>API budget today</h3>
        <div class="value" style="font-size:27px;font-weight:650;font-variant-numeric:tabular-nums">
          ${quota.used_today}<span class="faint" style="font-size:16px"> / ${quota.cap}</span></div>
        <div class="bar ${quota.pct > 90 ? 'danger' : quota.pct > 70 ? '' : 'ok'}"
             style="margin:10px 0"><span style="width:${Math.min(quota.pct, 100)}%"></span></div>
        <dl class="kv" style="margin-top:14px">
          <dt>Provider</dt><dd>${esc(stats.quota.provider)}</dd>
          <dt>Hunter.io</dt><dd>${stats.quota.hunter.used_this_month} /
            ${stats.quota.hunter.cap} this month</dd>
          <dt>Pending enrichment</dt><dd>${stats.pending_enrichment}</dd>
        </dl>
      </div>
      <div class="card"><h3>By niche</h3>
        ${table([{ label: 'Niche' }, { label: 'Leads', num: true }, { label: 'Hot', num: true }],
          niches, 'Run a search to see this.')}</div>
      <div class="card"><h3>By city</h3>
        ${table([{ label: 'City' }, { label: 'Leads', num: true }, { label: 'Hot', num: true }],
          cities, 'Run a search to see this.')}</div>
    </div>`;
};

// -------------------------------------------------------------- search ---
routes['/search'] = async () => {
  const [providers, history] = await Promise.all([
    api('/api/providers'), api('/api/search/history?limit=15'),
  ]);

  const rows = history.map((run) => `<tr onclick="location.hash='#/leads?search=${run.id}'">
      <td class="faint">#${run.id}</td>
      <td>${esc(run.niche)}</td>
      <td>${esc(run.area || run.city)}</td>
      <td class="num">${run.results_found}</td>
      <td class="num" style="color:var(--ok)">${run.new_leads}</td>
      <td class="num faint">${run.duplicates_skipped}</td>
      <td>${pill(run.status)}</td>
      <td class="faint nowrap">${date(run.started_at, true)}</td></tr>`);

  view.innerHTML = `
    <div class="page-head"><div><h1>Search</h1>
      <div class="subtitle">Every search is logged, so you never search the same place twice.</div>
    </div></div>

    <div class="card" style="margin-bottom:16px">
      <div class="row">
        <div><label>Niche</label><input id="niche" placeholder="restaurants" value="restaurants"></div>
        <div><label>City</label><input id="city" placeholder="Faridabad"></div>
        <div><label>Area / sector (optional)</label><input id="area" placeholder="Sector 15"></div>
        <div><label>Country (optional)</label><input id="country" placeholder="India"></div>
        <div class="shrink" style="width:110px"><label>Max results</label>
          <input id="max" type="number" value="20" min="1" max="60"></div>
        <div class="shrink" style="width:160px"><label>Provider</label>
          <select id="provider">${providers.available.map((p) =>
            `<option value="${p.key}" ${p.key === providers.active ? 'selected' : ''}
              ${p.ready ? '' : 'disabled'}>${esc(p.label)}${p.ready ? '' : ' - no key'}</option>`
          ).join('')}</select></div>
        <div class="shrink"><button class="primary" id="go">Search</button></div>
      </div>
      <div class="flex wrap" style="margin-top:10px;gap:10px;align-items:center">
        <button class="sm" id="useLocation">Search near me</button>
        <span class="shrink" style="width:150px"><label style="font-size:11px">Radius</label>
          <select id="radius">
            <option value="1000">1 km</option>
            <option value="3000" selected>3 km</option>
            <option value="5000">5 km</option>
            <option value="10000">10 km</option>
            <option value="25000">25 km</option>
          </select></span>
        <span id="locationNote" class="faint" style="font-size:12px"></span>
      </div>
      <div id="preview" class="muted" style="margin-top:10px;font-size:12.5px"></div>
    </div>

    <div id="progress"></div>
    <h3>Recent searches</h3>
    ${table(['#', 'Niche', 'Where', { label: 'Found', num: true }, { label: 'New', num: true },
      { label: 'Dupes', num: true }, 'Status', 'When'], rows, 'No searches yet.')}`;

  const fields = ['niche', 'city', 'area', 'country'].map((id) => document.getElementById(id));
  const previewBox = document.getElementById('preview');

  async function updatePreview() {
    const [niche, city, area, country] = fields.map((f) => f.value.trim());
    if (!niche || !city) { previewBox.textContent = ''; return; }
    try {
      const data = await api(`/api/search/preview?niche=${encodeURIComponent(niche)}`
        + `&city=${encodeURIComponent(city)}&area=${encodeURIComponent(area)}`
        + `&country=${encodeURIComponent(country)}`);
      previewBox.innerHTML = `${pill(data.coverage.status)} ${esc(data.message)}`;
    } catch (_) { previewBox.textContent = ''; }
  }
  fields.forEach((field) => field.addEventListener('blur', updatePreview));
  updatePreview();

  const basePayload = () => ({
    niche: document.getElementById('niche').value.trim(),
    city: document.getElementById('city').value.trim(),
    area: document.getElementById('area').value.trim(),
    country: document.getElementById('country').value.trim(),
    max_results: Number(document.getElementById('max').value) || 20,
    provider: document.getElementById('provider').value,
  });

  async function runSearch(button, payload) {
    button.disabled = true;
    try {
      const started = await post('/api/search', payload);
      pollSearch(started.search_id);
      return started;
    } catch (error) {
      toast(error.message, 'err');
      return null;
    } finally {
      button.disabled = false;
    }
  }

  document.getElementById('go').addEventListener('click', async (event) => {
    const payload = basePayload();
    if (!payload.niche || !payload.city) return toast('Niche and city are both required', 'err');
    await runSearch(event.currentTarget, payload);
  });

  document.getElementById('useLocation').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const note = document.getElementById('locationNote');
    const payload = basePayload();
    if (!payload.niche) return toast('Pick a niche first', 'err');

    button.disabled = true;
    note.textContent = 'Asking your browser for your location...';
    try {
      const { lat, lng, accuracy } = await currentPosition();
      // The city is left to the server: it reverse-geocodes the point so the
      // lead records and coverage map read as a place, not a decimal pair.
      payload.city = '';
      payload.area = '';
      payload.lat = lat;
      payload.lng = lng;
      payload.radius_m = Number(document.getElementById('radius').value);
      note.textContent = `Located to about ${Math.round(accuracy)}m — searching...`;
      const started = await runSearch(button, payload);
      if (started) {
        document.getElementById('city').value = started.city || '';
        document.getElementById('area').value = started.area || '';
        note.textContent = `Searching ${(started.radius_m / 1000).toFixed(1)}km around `
          + `${started.area ? started.area + ', ' : ''}${started.city}`;
      } else {
        note.textContent = '';
      }
    } catch (error) {
      note.textContent = '';
      toast(error.message, 'err');
    } finally {
      button.disabled = false;
    }
  });
};

async function pollSearch(searchId) {
  const box = document.getElementById('progress');
  if (!box) return;
  const run = await api(`/api/search/${searchId}/status`);

  if (run.status === 'RUNNING') {
    box.innerHTML = `<div class="card" style="margin-bottom:16px">
      <div class="flex between"><b>Search #${searchId} running</b>
        <button class="sm" onclick="cancelSearch(${searchId})">Cancel</button></div>
      <div class="muted" style="margin:8px 0">${esc(run.progress || 'Working...')}</div>
      <div class="bar"><span style="width:100%;opacity:.5"></span></div></div>`;
    setTimeout(() => pollSearch(searchId), 1200);
    return;
  }

  if (run.status !== 'COMPLETED') {
    box.innerHTML = `<div class="banner ${run.status === 'STOPPED_QUOTA' ? 'warn' : 'err'}">
      Search #${searchId} ${esc(run.status)}: ${esc(run.error_message || '')}</div>`;
    return;
  }

  const leads = (run.leads || []).map((lead) => `
    <tr onclick="openLead(${lead.id})">
      <td>${scoreChip(lead.lead_score, lead.lead_priority)}</td>
      <td>${esc(lead.business_name)}</td>
      <td>${pill(lead.lead_priority)}</td>
      <td class="mono">${esc(lead.phone || '—')}</td>
      <td class="faint">${lead.website_url ? esc(lead.website_url).slice(0, 40) : 'no website'}</td>
    </tr>`);

  box.innerHTML = `<div class="banner info">Search #${searchId} done —
      <b>${run.new_leads} new leads</b>, ${run.duplicates_skipped} duplicates skipped,
      ${run.results_found} results seen.
      ${run.hot_count ? `<b style="color:var(--hot)">${run.hot_count} HOT</b>.` : ''}</div>
    ${table(['Score', 'Business', 'Priority', 'Phone', 'Website'], leads,
      'No new leads - everything found was already in your database.')}`;
  refreshBadges();
}

async function cancelSearch(searchId) {
  await post(`/api/search/${searchId}/cancel`);
  toast('Cancelling after the current lead...');
}

// --------------------------------------------------------------- leads ---
let leadFilters = { limit: 50, offset: 0, sort: 'created_at', direction: 'desc' };

routes['/leads'] = async (params) => {
  if (params.priority) leadFilters.priority = params.priority;
  if (params.status) leadFilters.status = params.status;
  const facets = await api('/api/leads/facets');
  view.innerHTML = `
    <div class="page-head">
      <div><h1>Leads</h1><div class="subtitle" id="leadCount"></div></div>
      <div class="flex">
        <button id="exportBtn">Export CSV</button>
        <button id="dedupeBtn">Find duplicates</button>
      </div>
    </div>
    <div class="card" style="margin-bottom:14px">
      <div class="row">
        <div style="flex:2"><label>Search</label>
          <input id="fq" placeholder="name, phone, email, address" value="${esc(leadFilters.q || '')}"></div>
        <div><label>Priority</label><select id="fpriority">
          <option value="">Any</option>${['HOT', 'WARM', 'COLD'].map((p) =>
            `<option ${leadFilters.priority === p ? 'selected' : ''}>${p}</option>`).join('')}
        </select></div>
        <div><label>Status</label><select id="fstatus">
          <option value="">Any</option>${facets.statuses.map((s) =>
            `<option ${leadFilters.status === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}
        </select></div>
        <div><label>City</label><select id="fcity"><option value="">Any</option>
          ${facets.cities.map((c) => `<option ${leadFilters.city === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}
        </select></div>
        <div><label>Niche</label><select id="fniche"><option value="">Any</option>
          ${facets.niches.map((n) => `<option ${leadFilters.niche === n ? 'selected' : ''}>${esc(n)}</option>`).join('')}
        </select></div>
        <div><label>Website</label><select id="fweb">
          <option value="">Any</option><option value="false">No website</option>
          <option value="true">Has website</option></select></div>
        <div class="shrink"><button class="primary" id="applyBtn">Apply</button></div>
        <div class="shrink"><button id="clearBtn">Clear</button></div>
      </div>
      <div class="flex wrap" style="margin-top:10px;font-size:12px">
        <span class="faint">Quick:</span>
        <button class="sm" data-quick="hot">Hot leads</button>
        <button class="sm" data-quick="nosite">No website</button>
        <button class="sm" data-quick="new">Never contacted</button>
        <span class="spacer"></span>
        <span id="bulkBar"></span>
      </div>
    </div>
    <div id="leadTable"><div class="loading">Loading leads...</div></div>`;

  const read = () => {
    leadFilters = {
      ...leadFilters,
      q: document.getElementById('fq').value.trim() || undefined,
      priority: document.getElementById('fpriority').value || undefined,
      status: document.getElementById('fstatus').value || undefined,
      city: document.getElementById('fcity').value || undefined,
      niche: document.getElementById('fniche').value || undefined,
      has_website: document.getElementById('fweb').value || undefined,
      offset: 0,
    };
  };

  document.getElementById('applyBtn').onclick = () => { read(); loadLeads(); };
  document.getElementById('fq').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { read(); loadLeads(); }
  });
  document.getElementById('clearBtn').onclick = () => {
    leadFilters = { limit: 50, offset: 0, sort: 'created_at', direction: 'desc' };
    navigate();
  };
  document.getElementById('exportBtn').onclick = () => {
    const query = new URLSearchParams(
      Object.entries(leadFilters).filter(([k, v]) =>
        v !== undefined && !['limit', 'offset', 'sort', 'direction'].includes(k))
    );
    window.location.href = `/api/leads/export?${query}`;
  };
  document.getElementById('dedupeBtn').onclick = async () => {
    const result = await post('/api/leads/dedupe');
    toast(`${result.merged.length} duplicate(s) merged`, 'ok');
    loadLeads();
  };
  document.querySelectorAll('[data-quick]').forEach((button) => {
    button.onclick = () => {
      const kind = button.dataset.quick;
      leadFilters = { limit: 50, offset: 0, sort: 'lead_score', direction: 'desc' };
      if (kind === 'hot') leadFilters.priority = 'HOT';
      if (kind === 'nosite') leadFilters.has_website = 'false';
      if (kind === 'new') leadFilters.status = 'NEW';
      loadLeads();
    };
  });

  if (params.search) leadFilters.q = undefined;
  loadLeads();
};

const selected = new Set();

async function loadLeads() {
  const container = document.getElementById('leadTable');
  if (!container) return;
  const query = new URLSearchParams(
    Object.entries(leadFilters).filter(([, v]) => v !== undefined && v !== '')
  );
  const data = await api(`/api/leads?${query}`);
  const countBox = document.getElementById('leadCount');
  if (countBox) countBox.textContent =
    `${data.total} lead${data.total === 1 ? '' : 's'} matching`;

  const rows = data.leads.map((lead) => `<tr onclick="openLead(${lead.id})">
    <td onclick="event.stopPropagation()">
      <input type="checkbox" style="width:auto" data-select="${lead.id}"
        ${selected.has(lead.id) ? 'checked' : ''}></td>
    <td>${scoreChip(lead.lead_score, lead.lead_priority)}</td>
    <td><b>${esc(lead.business_name)}</b>
      <div class="faint" style="font-size:11.5px">${esc(lead.area || '')}${lead.area && lead.city ? ', ' : ''}${esc(lead.city || '')}</div></td>
    <td>${pill(lead.lead_priority)}</td>
    <td>${pill(lead.status)}</td>
    <td class="mono nowrap">${esc(lead.phone || '—')}</td>
    <td class="faint" style="max-width:180px;overflow:hidden;text-overflow:ellipsis">
      ${lead.website_url
        ? `<span title="${esc(lead.website_url)}">${lead.website_score ?? '?'}/10</span>`
        : '<span style="color:var(--accent)">none</span>'}</td>
    <td class="num faint">${lead.data_quality_score}/10</td>
    <td class="faint nowrap">${ago(lead.created_at)}</td>
  </tr>`);

  const pages = Math.ceil(data.total / data.limit) || 1;
  const page = Math.floor(data.offset / data.limit) + 1;

  container.innerHTML = table(
    ['', 'Score', 'Business', 'Priority', 'Status', 'Phone', 'Site',
      { label: 'Data', num: true }, 'Found'],
    rows,
    'No leads match these filters. Try Clear, or run a search.'
  ) + (data.total > data.limit ? `<div class="flex between" style="margin-top:12px">
      <span class="faint">Page ${page} of ${pages}</span>
      <span class="flex">
        <button class="sm" ${page <= 1 ? 'disabled' : ''} onclick="pageLeads(-1)">Previous</button>
        <button class="sm" ${page >= pages ? 'disabled' : ''} onclick="pageLeads(1)">Next</button>
      </span></div>` : '');

  container.querySelectorAll('[data-select]').forEach((box) => {
    box.onchange = () => {
      const id = Number(box.dataset.select);
      box.checked ? selected.add(id) : selected.delete(id);
      renderBulkBar();
    };
  });
  renderBulkBar();
}

function pageLeads(direction) {
  leadFilters.offset = Math.max(0, (leadFilters.offset || 0) + direction * leadFilters.limit);
  loadLeads();
}

function renderBulkBar() {
  const bar = document.getElementById('bulkBar');
  if (!bar) return;
  if (!selected.size) { bar.innerHTML = ''; return; }
  bar.innerHTML = `<span class="flex"><b>${selected.size} selected</b>
    <select id="bulkStatus" style="width:150px">
      ${['CONTACTED', 'REPLIED', 'MEETING_SET', 'PROPOSAL_SENT', 'WON', 'LOST']
        .map((s) => `<option>${s}</option>`).join('')}
    </select>
    <button class="sm" onclick="applyBulkStatus()">Set status</button>
    <button class="sm ghost" onclick="clearSelection()">Clear</button></span>`;
}

async function applyBulkStatus() {
  const status = document.getElementById('bulkStatus').value;
  await post('/api/leads/bulk-status', { ids: [...selected], status });
  toast(`${selected.size} lead(s) updated`, 'ok');
  selected.clear();
  loadLeads();
}

function clearSelection() { selected.clear(); loadLeads(); }

// --------------------------------------------------------- lead drawer ---
let drawerLead = null;

async function openLead(leadId) {
  drawerLead = await api(`/api/leads/${leadId}`);
  renderDrawer('details');
}

function closeDrawer() { drawerRoot.innerHTML = ''; drawerLead = null; }

function renderDrawer(tab) {
  const lead = drawerLead;
  if (!lead) return;

  const tabs = ['details', 'outreach', 'timeline', 'analysis'];
  const body = {
    details: drawerDetails, outreach: drawerOutreach,
    timeline: drawerTimeline, analysis: drawerAnalysis,
  }[tab](lead);

  drawerRoot.innerHTML = `
    <div class="drawer-backdrop" onclick="closeDrawer()"></div>
    <div class="drawer">
      <div class="drawer-head">
        <div>
          <h2>${esc(lead.business_name)}</h2>
          <div class="muted">${esc([lead.area, lead.city, lead.country].filter(Boolean).join(', '))}
            · ${esc(lead.niche || 'no niche')} · ${esc(lead.source || '')}</div>
          <div class="flex" style="margin-top:8px">
            ${scoreChip(lead.lead_score, lead.lead_priority)} ${pill(lead.lead_priority)}
            ${pill(lead.status)}
            <span class="faint" style="font-size:12px">data ${lead.data_quality_score}/10</span>
          </div>
        </div>
        <button class="ghost" onclick="closeDrawer()">✕</button>
      </div>
      <div class="tabs">${tabs.map((name) =>
        `<button class="${name === tab ? 'active' : ''}" onclick="renderDrawer('${name}')">
          ${name[0].toUpperCase() + name.slice(1)}</button>`).join('')}</div>
      ${body}
    </div>`;
}

function drawerDetails(lead) {
  const link = (url, label) => url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener" style="color:var(--info)">${esc(label)}</a>`
    : '<span class="faint">—</span>';
  const statuses = ['NEW', 'CONTACTED', 'REPLIED', 'MEETING_SET', 'PROPOSAL_SENT',
    'NEGOTIATION', 'WON', 'LOST'];

  return `
    <div class="flex wrap" style="margin-bottom:16px">
      ${lead.phone ? `<a class="btn sm" href="tel:${esc(lead.phone)}">Call</a>` : ''}
      ${lead.whatsapp ? `<a class="btn sm" target="_blank" rel="noopener"
        href="https://wa.me/${esc(lead.whatsapp)}">WhatsApp</a>` : ''}
      ${lead.email ? `<a class="btn sm" href="mailto:${esc(lead.email)}">Email</a>` : ''}
      ${lead.google_maps_url ? `<a class="btn sm" target="_blank" rel="noopener"
        href="${esc(lead.google_maps_url)}">Maps</a>` : ''}
      <button class="sm" onclick="enrichLead(${lead.id})">Re-enrich</button>
      <button class="sm danger" onclick="deleteLead(${lead.id})">Delete</button>
    </div>

    <div class="card" style="margin-bottom:14px">
      <div class="row">
        <div><label>Status</label><select id="dStatus">${statuses.map((s) =>
          `<option ${lead.status === s ? 'selected' : ''}>${s}</option>`).join('')}</select></div>
        <div><label>Follow up on</label>
          <input id="dFollow" type="date" value="${esc((lead.follow_up_date || '').slice(0, 10))}"></div>
        <div class="shrink"><button class="primary" onclick="saveLeadFields(${lead.id})">Save</button></div>
      </div>
    </div>

    <dl class="kv">
      <dt>Owner</dt><dd>${esc(lead.owner_name) || '<span class="faint">unknown</span>'}</dd>
      <dt>Phone</dt><dd class="mono">${esc(lead.phone) || '—'}
        ${lead.phone_valid ? '' : '<span class="faint">(unvalidated)</span>'}</dd>
      <dt>Email</dt><dd class="mono">${esc(lead.email) || '—'}</dd>
      <dt>Address</dt><dd>${esc(lead.address) || '—'}</dd>
      <dt>Website</dt><dd>${link(lead.website_url, lead.website_url || 'none')}</dd>
      <dt>Instagram</dt><dd>${link(lead.instagram_url, 'profile')}</dd>
      <dt>Facebook</dt><dd>${link(lead.facebook_url, 'profile')}</dd>
      <dt>LinkedIn</dt><dd>${link(lead.linkedin_url, 'profile')}</dd>
      <dt>Google rating</dt><dd>${lead.google_rating
        ? `${lead.google_rating} (${lead.google_reviews || 0} reviews)` : '—'}</dd>
      <dt>Touchpoints</dt><dd>${lead.total_touchpoints}</dd>
      <dt>Last contacted</dt><dd>${date(lead.last_contacted_date, true)}</dd>
      <dt>Found</dt><dd>${date(lead.found_date, true)}</dd>
      <dt>Enrichment</dt><dd>${esc(lead.enrichment_status)}
        ${lead.enrichment_error ? `<div class="faint">${esc(lead.enrichment_error)}</div>` : ''}</dd>
    </dl>

    <div class="field" style="margin-top:16px">
      <label>Notes</label>
      <textarea id="dNotes" style="min-height:70px">${esc(lead.notes || '')}</textarea>
      <button class="sm" style="margin-top:6px" onclick="saveNotes(${lead.id})">Save notes</button>
    </div>`;
}

function drawerAnalysis(lead) {
  const issues = (lead.website_issues || []).map((i) => `<li>${esc(i)}</li>`).join('');
  const reasons = (lead.score_reasons || []).map((r) => `<li>${esc(r)}</li>`).join('');
  return `
    <h3>Why this lead scored ${lead.lead_score}/10</h3>
    <ul class="issues reasons">${reasons || '<li class="faint">Not scored yet.</li>'}</ul>
    <h3 style="margin-top:20px">Website analysis</h3>
    ${lead.website_url ? `<dl class="kv" style="margin-bottom:12px">
      <dt>Score</dt><dd>${lead.website_score ?? '?'}/10</dd>
      <dt>Platform</dt><dd>${esc(lead.website_platform) || 'unknown / custom'}</dd>
      <dt>HTTPS</dt><dd>${lead.website_ssl ? 'yes' : 'no'}</dd>
      <dt>Mobile-friendly</dt><dd>${lead.website_mobile_friendly ? 'yes' : 'no'}</dd>
      <dt>Load time</dt><dd>${lead.website_speed_ms ? `${lead.website_speed_ms} ms` : '—'}</dd>
      <dt>Copyright year</dt><dd>${esc(lead.website_last_updated) || '—'}</dd>
      <dt>Checked</dt><dd>${date(lead.website_checked_at, true)}</dd>
    </dl>` : '<div class="banner info">No website at all — this is the easiest pitch you can make.</div>'}
    <ul class="issues">${issues || '<li class="faint">No issues recorded.</li>'}</ul>`;
}

function drawerTimeline(lead) {
  const items = (lead.interactions || []).map((entry) => `<li>
      <div class="when">${date(entry.date, true)} · ${esc(entry.type)}
        ${entry.automated ? '· automated' : ''}</div>
      <div><b>${esc(entry.subject || entry.outcome || entry.type)}</b></div>
      ${entry.content ? `<div class="muted" style="font-size:12.5px;white-space:pre-wrap">${
        esc(entry.content.slice(0, 400))}</div>` : ''}
      ${entry.next_step ? `<div class="faint">Next: ${esc(entry.next_step)}</div>` : ''}
    </li>`).join('');

  return `
    <div class="card" style="margin-bottom:16px">
      <h3>Log an interaction</h3>
      <div class="row">
        <div><label>Type</label><select id="iType">
          ${['call', 'email', 'whatsapp', 'meeting', 'note'].map((t) =>
            `<option>${t}</option>`).join('')}</select></div>
        <div><label>Outcome</label><input id="iOutcome" placeholder="no answer / interested"></div>
        <div><label>New status</label><select id="iStatus"><option value="">Leave as is</option>
          ${['CONTACTED', 'REPLIED', 'MEETING_SET', 'PROPOSAL_SENT', 'NEGOTIATION', 'WON', 'LOST']
            .map((s) => `<option>${s}</option>`).join('')}</select></div>
        <div><label>Follow up on</label><input id="iFollow" type="date"></div>
      </div>
      <div class="field" style="margin-top:10px"><label>Notes</label>
        <textarea id="iContent" style="min-height:60px"></textarea></div>
      <button class="primary" onclick="logInteraction(${lead.id})">Log it</button>
    </div>
    <ul class="timeline">${items || '<li class="faint">No interactions logged yet.</li>'}</ul>`;
}

function drawerOutreach(lead) {
  const queued = (lead.queued_messages || []).map((message) => `
    <div class="card" style="margin-bottom:8px">
      <div class="flex between"><span>${pill(message.status)}
        <span class="faint">${esc(message.step)} · ${esc(message.template_name)}</span></span>
        <span class="faint">${date(message.created_at)}</span></div>
    </div>`).join('');

  return `
    <div class="card" style="margin-bottom:14px">
      <h3>Generate a message</h3>
      <div class="row">
        <div><label>Template</label><select id="tplSelect"></select></div>
        <div class="shrink"><button class="primary" onclick="generateMessage(${lead.id})">Generate</button></div>
      </div>
      <div id="msgOut" style="margin-top:12px"></div>
    </div>
    <h3>Queued for approval</h3>
    ${queued || '<div class="empty">Nothing queued for this lead.</div>'}`;
}

(async function preloadTemplates() {
  window.__templates = [];
  try { window.__templates = await api('/api/templates'); } catch (_) { /* ignore */ }
})();

document.addEventListener('click', (event) => {
  // Fill the template dropdown lazily whenever the outreach tab renders.
  const select = document.getElementById('tplSelect');
  if (select && !select.options.length && window.__templates) {
    select.innerHTML = window.__templates.map((t) =>
      `<option value="${esc(t.name)}">${esc(t.name)} (${esc(t.type)})</option>`).join('');
  }
});

async function generateMessage(leadId) {
  const template = document.getElementById('tplSelect').value;
  const out = document.getElementById('msgOut');
  try {
    const message = await api(`/api/leads/${leadId}/message?template=${encodeURIComponent(template)}`,
      { method: 'POST' });
    out.innerHTML = `
      ${message.subject ? `<div class="field"><label>Subject</label>
        <input id="mSubject" value="${esc(message.subject)}"></div>` : ''}
      <div class="field"><label>Body</label>
        <textarea id="mBody" style="min-height:180px">${esc(message.body)}</textarea></div>
      <div class="flex wrap">
        ${message.to ? `<button class="primary"
          onclick="queueForApproval(${leadId}, '${esc(template)}')">Queue for approval</button>`
          : '<span class="faint">No email on this lead — use WhatsApp or add an address.</span>'}
        ${message.whatsapp_link ? `<a class="btn" target="_blank" rel="noopener"
          href="${esc(message.whatsapp_link)}">Open in WhatsApp</a>` : ''}
        <button onclick="copyMessage()">Copy</button>
      </div>
      <div class="faint" style="margin-top:8px;font-size:11.5px">
        WhatsApp opens a pre-filled chat for you to send by hand — this tool never
        auto-sends WhatsApp messages.</div>`;
  } catch (error) { toast(error.message, 'err'); }
}

function copyMessage() {
  const body = document.getElementById('mBody');
  navigator.clipboard.writeText(body.value).then(() => toast('Copied', 'ok'));
}

async function queueForApproval(leadId, template) {
  try {
    await post('/api/approvals/queue', { lead_id: leadId, template, step: 'manual' });
    toast('Queued — approve it on the Approve sends page', 'ok');
    refreshBadges();
  } catch (error) { toast(error.message, 'err'); }
}

async function saveLeadFields(leadId) {
  const payload = {
    status: document.getElementById('dStatus').value,
    follow_up_date: document.getElementById('dFollow').value || null,
  };
  drawerLead = { ...await patch(`/api/leads/${leadId}`, payload), ...{ interactions: drawerLead.interactions, queued_messages: drawerLead.queued_messages } };
  toast('Saved', 'ok');
  renderDrawer('details');
  if (currentPath === '/leads') loadLeads();
  refreshBadges();
}

async function saveNotes(leadId) {
  await patch(`/api/leads/${leadId}`, { notes: document.getElementById('dNotes').value });
  toast('Notes saved', 'ok');
}

async function enrichLead(leadId) {
  toast('Enriching — checking the website, this takes a few seconds...');
  try {
    await api(`/api/leads/${leadId}/enrich`, { method: 'POST' });
    await openLead(leadId);
    toast('Enrichment complete', 'ok');
    if (currentPath === '/leads') loadLeads();
  } catch (error) { toast(error.message, 'err'); }
}

async function deleteLead(leadId) {
  if (!confirm('Permanently delete this lead and its entire history? This cannot be undone.')) return;
  await del(`/api/leads/${leadId}`);
  closeDrawer();
  toast('Lead deleted', 'ok');
  if (currentPath === '/leads') loadLeads();
}

async function logInteraction(leadId) {
  const payload = {
    type: document.getElementById('iType').value,
    outcome: document.getElementById('iOutcome').value,
    content: document.getElementById('iContent').value,
    follow_up_date: document.getElementById('iFollow').value || null,
    new_status: document.getElementById('iStatus').value || null,
  };
  await post(`/api/leads/${leadId}/interactions`, payload);
  await openLead(leadId);
  renderDrawer('timeline');
  toast('Logged', 'ok');
  refreshBadges();
}

// ---------------------------------------------------------- follow-ups ---
routes['/followups'] = async () => {
  const [dueToday, overdue, upcoming, suggested] = await Promise.all([
    api('/api/follow-ups/today'), api('/api/follow-ups/overdue'),
    api('/api/follow-ups/upcoming'), api('/api/follow-ups/suggested'),
  ]);

  // Dated rows can be cleared or pushed out. Cadence rows only get Log: they're
  // computed from last_contacted_date, so setting a follow-up date wouldn't
  // actually take one off the list, and a button that appears to do nothing is
  // worse than no button.
  const dated = (lead) => `<td class="nowrap" onclick="event.stopPropagation()">
    <button class="sm" onclick="followUpDone(${lead.id})" title="Clear the follow-up date">Done</button>
    <button class="sm" onclick="snoozeFollowUp(${lead.id}, 3)" title="Push out 3 days">+3d</button>
    <button class="sm" onclick="openLead(${lead.id})" title="Log a call, email or note">Log</button>
  </td>`;
  const logOnly = (lead) => `<td class="nowrap" onclick="event.stopPropagation()">
    <button class="sm" onclick="openLead(${lead.id})" title="Log a call, email or note">Log</button>
  </td>`;

  const row = (lead, extra = '') => `<tr onclick="openLead(${lead.id})">
    <td>${scoreChip(lead.lead_score, lead.lead_priority)}</td>
    <td><b>${esc(lead.business_name)}</b>
      <div class="faint" style="font-size:11.5px">${esc(lead.city || '')}</div></td>
    <td>${pill(lead.status)}</td>
    <td class="mono nowrap">${esc(lead.phone || '—')}</td>
    <td class="faint nowrap">${ago(lead.last_contacted_date)}</td>
    ${extra}</tr>`;

  view.innerHTML = `
    <div class="page-head"><div><h1>Follow-ups</h1>
      <div class="subtitle">80% of deals close after the 5th touch. This page is that.</div></div>
    </div>
    ${overdue.length ? `<h3 style="color:var(--hot)">Overdue (${overdue.length})</h3>
      ${table(['Score', 'Business', 'Status', 'Phone', 'Last contact', 'Due', ''],
        overdue.map((l) => row(l,
          `<td class="faint">${date(l.follow_up_date)}</td>${dated(l)}`)))}
      <div style="height:20px"></div>` : ''}

    <h3>Due today (${dueToday.length})</h3>
    ${table(['Score', 'Business', 'Status', 'Phone', 'Last contact', 'Due', ''],
      dueToday.map((l) => row(l,
        `<td class="faint">${date(l.follow_up_date)}</td>${dated(l)}`)),
      'Nothing due today.')}

    <h3 style="margin-top:24px">Cadence says these are due (${suggested.length})</h3>
    <div class="subtitle" style="margin-bottom:10px">Contacted leads that have gone quiet,
      by the Day 3 / 7 / 14 / 21 rule — even without an explicit follow-up date.</div>
    ${table(['Score', 'Business', 'Status', 'Phone', 'Last contact', 'Step', 'Do this', ''],
      suggested.map((l) => row(l,
        `<td>${pill(l.step.replace(' ', '_'))}</td><td class="muted">${esc(l.action)}</td>`
        + logOnly(l))),
      'Nobody has gone quiet on you.')}

    <h3 style="margin-top:24px">Next 7 days (${upcoming.length})</h3>
    ${table(['Score', 'Business', 'Status', 'Phone', 'Last contact', 'Due', ''],
      upcoming.map((l) => row(l,
        `<td class="faint">${date(l.follow_up_date)}</td>${dated(l)}`)),
      'Nothing scheduled.')}`;
};

async function followUpDone(leadId) {
  await patch(`/api/leads/${leadId}`, { follow_up_date: null });
  toast('Follow-up cleared', 'ok');
  navigate();
}

async function snoozeFollowUp(leadId, days) {
  const when = new Date();
  when.setDate(when.getDate() + days);
  await patch(`/api/leads/${leadId}`, { follow_up_date: when.toISOString().slice(0, 10) });
  toast(`Snoozed ${days} days`, 'ok');
  navigate();
}

// ----------------------------------------------------------------- map ---

let leafletReady = null;

// Leaflet is ~150KB and only this one page needs it, so it loads on first
// visit rather than on every page load. Vendored locally, not from a CDN: the
// tool is meant to run on your own box without depending on someone else's.
function loadLeaflet() {
  if (leafletReady) return leafletReady;
  leafletReady = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/static/vendor/leaflet.js';
    s.onload = () => resolve(window.L);
    s.onerror = () => reject(new Error('Could not load the map library'));
    document.head.appendChild(s);
  });
  return leafletReady;
}

const PRIORITY_COLOUR = { HOT: '#f85149', WARM: '#d29922', COLD: '#6e7681' };

let mapInstance = null;

routes['/map'] = async () => {
  view.innerHTML = `
    <div class="page-head">
      <div><h1>Map</h1>
        <div class="subtitle" id="mapCount">Loading leads...</div></div>
      <div class="flex">
        <select id="mapPriority" class="shrink">
          <option value="">All priorities</option>
          <option value="HOT">Hot only</option>
          <option value="WARM">Warm only</option>
          <option value="COLD">Cold only</option>
        </select>
        <button id="mapLocate">Find me</button>
      </div>
    </div>
    <div class="card" style="padding:0;overflow:hidden">
      <div id="mapCanvas" style="height:calc(100vh - 210px);min-height:420px"></div>
    </div>
    <div class="flex wrap" style="margin-top:10px;font-size:12px;gap:14px">
      <span><span class="dot-key" style="background:${PRIORITY_COLOUR.HOT}"></span>Hot</span>
      <span><span class="dot-key" style="background:${PRIORITY_COLOUR.WARM}"></span>Warm</span>
      <span><span class="dot-key" style="background:${PRIORITY_COLOUR.COLD}"></span>Cold</span>
      <span class="faint">Bigger dot = higher score. Click a dot for the lead.</span>
    </div>`;

  let L;
  try {
    L = await loadLeaflet();
  } catch (err) {
    document.getElementById('mapCanvas').innerHTML =
      `<div class="empty">${esc(err.message)}. Check that
       /static/vendor/leaflet.js is present.</div>`;
    return;
  }

  mapInstance = L.map('mapCanvas', { scrollWheelZoom: true })
    .setView([22.5, 78.9], 4);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(mapInstance);

  const layer = L.layerGroup().addTo(mapInstance);

  async function draw() {
    const priority = document.getElementById('mapPriority').value;
    const data = await api(`/api/geo/map${priority ? `?priority=${priority}` : ''}`);
    layer.clearLayers();

    const points = [];
    data.leads.forEach((lead) => {
      const colour = PRIORITY_COLOUR[lead.lead_priority] || PRIORITY_COLOUR.COLD;
      const marker = L.circleMarker([lead.lat, lead.lng], {
        radius: 5 + (lead.lead_score || 0) * 0.6,
        color: colour, fillColor: colour, fillOpacity: 0.65, weight: 1.5,
      });
      marker.bindPopup(`
        <div style="min-width:190px">
          <b>${esc(lead.business_name)}</b><br>
          <span style="color:${colour}">${esc(lead.lead_priority)} &middot;
            ${lead.lead_score}/10</span>
          <span style="color:#666"> &middot; ${esc(lead.status || '')}</span><br>
          <span style="color:#666">${esc([lead.area, lead.city].filter(Boolean).join(', '))}</span><br>
          ${lead.phone ? `<span style="color:#666">${esc(lead.phone)}</span><br>` : ''}
          ${lead.has_website ? '' : '<b style="color:#c0392b">No website</b><br>'}
          <a href="#" onclick="closeMapPopupAndOpen(${lead.id});return false">Open lead</a>
          &nbsp;|&nbsp;
          <a target="_blank" rel="noopener"
             href="https://www.google.com/maps/search/?api=1&query=${lead.lat},${lead.lng}">
             Google Maps</a>
        </div>`);
      marker.addTo(layer);
      points.push([lead.lat, lead.lng]);
    });

    document.getElementById('mapCount').textContent =
      `${data.count} lead${data.count === 1 ? '' : 's'} with coordinates`;
    if (points.length) mapInstance.fitBounds(points, { padding: [40, 40], maxZoom: 15 });
  }

  document.getElementById('mapPriority').onchange = draw;
  document.getElementById('mapLocate').onclick = async () => {
    try {
      const { lat, lng } = await currentPosition();
      mapInstance.setView([lat, lng], 14);
      L.circleMarker([lat, lng], {
        radius: 9, color: '#58a6ff', fillColor: '#58a6ff', fillOpacity: 0.35, weight: 2,
      }).addTo(mapInstance).bindPopup('You are here').openPopup();
    } catch (err) {
      toast(err.message, 'err');
    }
  };

  await draw();
};

function closeMapPopupAndOpen(leadId) {
  if (mapInstance) mapInstance.closePopup();
  openLead(leadId);
}

/** Browser geolocation, promisified, with errors a person can act on. */
function currentPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('This browser has no location support'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude,
                         accuracy: pos.coords.accuracy }),
      (err) => {
        const reasons = {
          1: 'Location permission denied — allow it in your browser, then retry',
          2: 'Your location is unavailable right now',
          3: 'Timed out getting your location',
        };
        reject(new Error(reasons[err.code] || 'Could not get your location'));
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
  });
}

// ------------------------------------------------------------ pipeline ---
routes['/pipeline'] = async () => {
  const stages = await api('/api/pipeline');
  const columns = stages.map((stage) => `
    <div class="card">
      <div class="flex between" style="margin-bottom:10px">
        <b style="color:${esc(stage.color)}">${esc(stage.name.replace(/_/g, ' '))}</b>
        <span class="faint">${stage.count}</span>
      </div>
      ${stage.leads.map((lead) => `
        <div onclick="openLead(${lead.id})" style="padding:8px 0;border-top:1px solid var(--line-soft);cursor:pointer">
          <div class="flex" style="gap:6px">${scoreChip(lead.lead_score, lead.lead_priority)}
            <b style="font-size:12.5px">${esc(lead.business_name)}</b></div>
          <div class="faint" style="font-size:11px">${esc(lead.city || '')}
            ${lead.last_contacted_date ? `· ${ago(lead.last_contacted_date)}` : ''}</div>
        </div>`).join('') || '<div class="faint" style="font-size:12px">Empty</div>'}
    </div>`).join('');

  view.innerHTML = `
    <div class="page-head"><div><h1>Pipeline</h1>
      <div class="subtitle">Every deal, by stage. Click a card to open the lead.</div></div></div>
    <div class="grid cols-4">${columns}</div>`;
};

// ------------------------------------------------------------ coverage ---
routes['/coverage'] = async () => {
  const data = await api('/api/coverage');
  if (!data.rows.length) {
    view.innerHTML = `<div class="page-head"><h1>Coverage map</h1></div>
      <div class="card"><div class="empty">No searches logged yet.
        <a href="#/search" style="color:var(--info)">Run your first search</a>.</div></div>`;
    return;
  }

  const index = {};
  data.rows.forEach((row) => { index[`${row.city}||${row.niche}`] = row; });

  const colors = {
    FRESH: 'background:#12261e;color:#3fb950',
    STALE: 'background:#3a2f10;color:#e3b341',
    EXHAUSTED: 'background:#21262d;color:#6e7681',
    NEVER: 'background:#3d1d1d;color:#ff7b72',
  };

  const header = data.niches.map((n) => `<th>${esc(n)}</th>`).join('');
  const body = data.cities.map((city) => `<tr>
      <td><b>${esc(city)}</b></td>
      ${data.niches.map((niche) => {
        const cell = index[`${city}||${niche}`];
        if (!cell) return '<td class="faint" style="text-align:center">·</td>';
        return `<td style="cursor:pointer" title="${cell.times_searched} searches, ${
          cell.total_leads_found} leads, last ${date(cell.last_searched)}"
          onclick="location.hash='#/leads'">
          <div class="coverage-cell" style="${colors[cell.status] || ''}">
            ${cell.total_leads_found}</div></td>`;
      }).join('')}
    </tr>`).join('');

  view.innerHTML = `
    <div class="page-head"><div><h1>Coverage map</h1>
      <div class="subtitle">Green = searched recently. Amber = stale, worth another look.
        Grey = exhausted. Blank = never searched.</div></div></div>
    <div class="table-wrap"><table>
      <thead><tr><th>City</th>${header}</tr></thead><tbody>${body}</tbody></table></div>
    <div class="flex wrap" style="margin-top:14px;font-size:12px">
      ${Object.entries(colors).map(([name, style]) =>
        `<span class="coverage-cell" style="${style}">${name}</span>`).join('')}
    </div>`;
};

// ----------------------------------------------------------- approvals ---
routes['/approvals'] = async () => {
  const [pending, identity] = await Promise.all([
    api('/api/approvals?status=PENDING'), api('/api/identity'),
  ]);

  const cards = pending.map((message) => `
    <div class="card" style="margin-bottom:12px" id="approval-${message.id}">
      <div class="flex between" style="margin-bottom:8px">
        <div><b>${esc(message.business_name)}</b>
          <span class="faint">· ${esc(message.city || '')} · ${esc(message.step)}</span>
          ${pill(message.lead_priority)}</div>
        <div class="faint">${esc(message.to_address || '')}</div>
      </div>
      ${message.subject ? `<div class="field"><label>Subject</label>
        <input value="${esc(message.subject)}" data-subject="${message.id}"></div>` : ''}
      <textarea data-body="${message.id}">${esc(message.body)}</textarea>
      <div class="flex" style="margin-top:10px">
        <button class="primary sm" onclick="approveMessage(${message.id})">Approve</button>
        <button class="sm" onclick="skipMessage(${message.id})">Skip</button>
        <button class="sm ghost" onclick="openLead(${message.lead_id})">Open lead</button>
      </div>
    </div>`).join('');

  view.innerHTML = `
    <div class="page-head">
      <div><h1>Approve sends</h1>
        <div class="subtitle">Nothing leaves this system until you approve it here.</div></div>
      <div class="flex">
        <button onclick="generateFollowups()">Generate due follow-ups</button>
        ${pending.length ? '<button class="primary" onclick="approveAll()">Approve all</button>' : ''}
      </div>
    </div>
    ${identity.dry_run ? `<div class="banner warn"><b>Dry-run mode is on.</b>
      Approved messages are logged, not actually sent. Leave it on for a week, read what
      it would have sent, then set DRY_RUN=false in .env.</div>` : ''}
    ${cards || '<div class="card"><div class="empty">Nothing waiting for approval.</div></div>'}
    ${pending.length ? `<div class="flex" style="margin-top:16px">
      <button onclick="sendApproved()">Send approved now</button>
      <span class="faint" style="font-size:12px">Otherwise they go out automatically
        every 30 minutes, rate-limited.</span></div>` : ''}`;
};

async function approveMessage(id) {
  const subject = document.querySelector(`[data-subject="${id}"]`);
  const body = document.querySelector(`[data-body="${id}"]`);
  await patch(`/api/approvals/${id}`, {
    subject: subject ? subject.value : null, body: body ? body.value : null,
  });
  await post(`/api/approvals/${id}/approve`);
  document.getElementById(`approval-${id}`)?.remove();
  toast('Approved', 'ok');
  refreshBadges();
}

async function skipMessage(id) {
  await post(`/api/approvals/${id}/skip`);
  document.getElementById(`approval-${id}`)?.remove();
  toast('Skipped');
  refreshBadges();
}

async function approveAll() {
  const ids = [...document.querySelectorAll('[data-body]')].map((n) => Number(n.dataset.body));
  await post('/api/approvals/approve-all', { ids });
  toast(`${ids.length} approved`, 'ok');
  navigate();
}

async function generateFollowups() {
  const result = await post('/api/approvals/generate');
  toast(`${result.generated} generated, ${result.marked_lost} marked lost`, 'ok');
  navigate();
}

async function sendApproved() {
  const result = await post('/api/approvals/send');
  toast(`Sent ${result.sent}${result.dry_run ? ' (dry run)' : ''}, ${result.failed} failed`,
    result.failed ? 'err' : 'ok');
  refreshBadges();
}

// ---------------------------------------------------------- automation ---
routes['/automation'] = async () => {
  const [status, log, failures] = await Promise.all([
    api('/api/automation/status'), api('/api/automation/log?limit=25'),
    api('/api/automation/failures'),
  ]);
  const s = status.switches;

  const toggle = (key, name, description) => `
    <div class="switch">
      <div class="name">${esc(name)}<small>${esc(description)}</small></div>
      <button class="toggle ${s[key] ? 'on' : ''}" onclick="flipSwitch('${key}', ${!s[key]})"></button>
    </div>`;

  const jobRows = status.jobs.map((job) => `<tr>
      <td>${esc(job.id)}</td>
      <td class="faint">${job.next_run ? date(job.next_run, true) : 'not scheduled'}</td>
      <td class="right"><button class="sm" onclick="runJob('${esc(job.id)}')">Run now</button></td>
    </tr>`);

  view.innerHTML = `
    <div class="page-head">
      <div><h1>Automation control</h1>
        <div class="subtitle">Discovery, enrichment and scoring run themselves.
          Anything that touches a human stops for your approval.</div></div>
      <button class="danger" onclick="killSwitch()">Kill switch — stop everything</button>
    </div>

    ${!s.automation_master ? '<div class="banner warn">Automation is OFF. Nothing runs on a schedule.</div>' : ''}
    ${status.dry_run ? '<div class="banner info">Dry-run sending is on — messages are logged, not sent.</div>' : ''}

    <div class="grid cols-5" style="margin-bottom:16px">
      ${stat('Searches today', status.today.searches)}
      ${stat('Leads found', status.today.leads_found)}
      ${stat('Enriched', status.today.enriched)}
      ${stat('Awaiting approval', status.today.awaiting_approval, '', 'accent')}
      ${stat('Sent today', status.today.sent)}
    </div>

    <div class="grid cols-2">
      <div class="card">
        <h3>Switches</h3>
        ${toggle('automation_master', 'Master switch', 'One flip stops every scheduled job')}
        ${toggle('job_discovery', 'Discovery', 'Runs due targets each morning')}
        ${toggle('job_enrichment', 'Enrichment & scoring', 'Website checks, validation, scoring')}
        ${toggle('job_followup_generation', 'Follow-up generation',
          'Writes messages into the approval queue — never sends')}
        ${toggle('job_sending', 'Sending', 'Sends messages you already approved, rate-limited')}
      </div>
      <div class="card">
        <h3>Scheduled jobs (UTC)</h3>
        ${table(['Job', 'Next run', ''], jobRows, 'Scheduler not running.')}
      </div>
    </div>

    ${failures.length ? `<h3 style="margin-top:22px;color:var(--hot)">
      Unresolved failures (${failures.length})
      <button class="sm" style="margin-left:8px" onclick="resolveAllFailures()">Mark all reviewed</button></h3>
      ${table(['Job', 'Error', 'When'], failures.map((f) => `<tr>
        <td class="mono">${esc(f.job)}</td>
        <td class="faint" style="max-width:520px">${esc(f.error)}</td>
        <td class="faint nowrap">${date(f.created_at, true)}</td></tr>`))}` : ''}

    <h3 style="margin-top:22px">Recent activity</h3>
    <div class="card">
      ${log.map((entry) => `<div style="padding:6px 0;border-bottom:1px solid var(--line-soft)">
        <span class="faint mono">${date(entry.created_at, true)}</span>
        <span class="faint">[${esc(entry.job)}]</span>
        <span style="${entry.level === 'error' ? 'color:var(--hot)'
          : entry.level === 'warn' ? 'color:var(--warm)' : ''};white-space:pre-wrap">
          ${esc(entry.message)}</span></div>`).join('')
        || '<div class="empty">No automation activity yet.</div>'}
    </div>`;
};

async function flipSwitch(name, enabled) {
  await post('/api/automation/switch', { name, enabled });
  navigate();
}

async function killSwitch() {
  await post('/api/automation/kill');
  toast('Automation stopped', 'ok');
  navigate();
}

async function runJob(name) {
  toast(`Running ${name}...`);
  try {
    const result = await post(`/api/automation/run/${name}`);
    toast(`${name}: ${JSON.stringify(result.result).slice(0, 120)}`, 'ok');
    navigate();
  } catch (error) { toast(error.message, 'err'); }
}

async function resolveAllFailures() {
  await post('/api/automation/failures/resolve-all');
  navigate();
}

// ------------------------------------------------------------- reports ---
routes['/reports'] = async () => {
  const [weekly, conversions] = await Promise.all([
    api('/api/stats/weekly'), api('/api/stats/conversions'),
  ]);
  const cost = weekly.cost_per_hot_lead;

  view.innerHTML = `
    <div class="page-head"><div><h1>Reports</h1>
      <div class="subtitle">${weekly.period_start} → ${weekly.period_end}</div></div></div>

    <div class="grid cols-5" style="margin-bottom:16px">
      ${stat('Leads found', weekly.leads_found)}
      ${stat('Hot leads', weekly.hot_found, '', 'hot')}
      ${stat('Searches run', weekly.searches_run)}
      ${stat('Touchpoints', weekly.total_touchpoints)}
      ${stat('Deals won', weekly.deals_won, '', 'ok')}
    </div>

    <div class="grid cols-2">
      <div class="card"><h3>Conversion between stages</h3>
        ${table(['From', 'To', { label: 'Rate', num: true }],
          conversions.steps.map((s) => `<tr>
            <td>${esc(s.from.replace(/_/g, ' '))} <span class="faint">(${s.from_count})</span></td>
            <td>${esc(s.to.replace(/_/g, ' '))} <span class="faint">(${s.to_count})</span></td>
            <td class="num"><b>${s.rate}%</b></td></tr>`))}
        <div class="muted" style="margin-top:12px;font-size:12px">
          Search → Won: <b>${conversions.search_to_won}%</b> of everything you've looked at.</div>
      </div>

      <div class="card"><h3>Cost efficiency (Part 9.7)</h3>
        <dl class="kv">
          <dt>API calls this month</dt><dd>${cost.api_calls_this_month}</dd>
          <dt>Hot leads this month</dt><dd>${cost.hot_leads_this_month}</dd>
          <dt>Calls per hot lead</dt><dd><b>${cost.calls_per_hot_lead ?? '—'}</b></dd>
        </dl>
        <div class="faint" style="margin-top:10px;font-size:11.5px">${esc(cost.note)}</div>
      </div>

      <div class="card"><h3>By niche</h3>
        ${table(['Niche', { label: 'Leads', num: true }, { label: 'Contacted', num: true },
          { label: 'Won', num: true }],
          weekly.by_niche.map((r) => `<tr><td>${esc(r.niche)}</td>
            <td class="num">${r.leads}</td><td class="num">${r.contacted}</td>
            <td class="num" style="color:var(--ok)">${r.won}</td></tr>`))}
      </div>

      <div class="card"><h3>By city</h3>
        ${table(['City', { label: 'Leads', num: true }, { label: 'Won', num: true }],
          weekly.by_city.map((r) => `<tr><td>${esc(r.city)}</td>
            <td class="num">${r.leads}</td>
            <td class="num" style="color:var(--ok)">${r.won}</td></tr>`))}
      </div>
    </div>`;
};

// ------------------------------------------------------------- targets ---
routes['/targets'] = async () => {
  const targets = await api('/api/targets');
  const rows = targets.map((target) => `<tr>
    <td><b>${esc(target.niche)}</b></td>
    <td>${esc(target.area ? `${target.area}, ${target.city}` : target.city)}
      ${target.country ? `<span class="faint">${esc(target.country)}</span>` : ''}</td>
    <td>${esc(target.auto_search)}</td>
    <td>${pill(target.coverage.status)}</td>
    <td class="num">${target.coverage.total_leads_found || 0}</td>
    <td class="faint nowrap">${target.last_searched ? ago(target.last_searched) : 'never'}</td>
    <td>${target.is_active ? '<span class="pill FRESH">active</span>'
      : '<span class="pill COLD">paused</span>'}</td>
    <td class="right nowrap">
      <button class="sm" onclick="searchTargetNow(${target.id})">Search now</button>
      <button class="sm" onclick="toggleTarget(${target.id}, ${!target.is_active})">
        ${target.is_active ? 'Pause' : 'Resume'}</button>
      <button class="sm danger" onclick="deleteTarget(${target.id})">✕</button>
    </td></tr>`);

  view.innerHTML = `
    <div class="page-head"><div><h1>Targets</h1>
      <div class="subtitle">Saved niche + city combos the automator searches on schedule.</div></div></div>
    <div class="card" style="margin-bottom:16px">
      <div class="row">
        <div><label>Niche</label><input id="tNiche" placeholder="gyms"></div>
        <div><label>City</label><input id="tCity" placeholder="Manchester"></div>
        <div><label>Area (optional)</label><input id="tArea"></div>
        <div><label>Country</label><input id="tCountry" placeholder="UK"></div>
        <div class="shrink" style="width:110px"><label>Max</label>
          <input id="tMax" type="number" value="20"></div>
        <div class="shrink" style="width:130px"><label>Frequency</label>
          <select id="tFreq"><option>weekly</option><option>daily</option>
            <option>monthly</option><option>manual</option></select></div>
        <div class="shrink"><button class="primary" onclick="addTarget()">Add target</button></div>
      </div>
    </div>
    ${table(['Niche', 'Where', 'Frequency', 'Coverage', { label: 'Leads', num: true },
      'Last run', 'State', ''], rows, 'No targets yet — add one above.')}`;
};

async function addTarget() {
  const payload = {
    niche: document.getElementById('tNiche').value.trim(),
    city: document.getElementById('tCity').value.trim(),
    area: document.getElementById('tArea').value.trim(),
    country: document.getElementById('tCountry').value.trim(),
    max_results: Number(document.getElementById('tMax').value) || 20,
    auto_search: document.getElementById('tFreq').value,
  };
  if (!payload.niche || !payload.city) return toast('Niche and city are required', 'err');
  await post('/api/targets', payload);
  toast('Target added', 'ok');
  navigate();
}

async function toggleTarget(id, active) {
  await patch(`/api/targets/${id}?is_active=${active}`, {});
  navigate();
}

async function deleteTarget(id) {
  await del(`/api/targets/${id}`);
  navigate();
}

async function searchTargetNow(id) {
  const { search_id: searchId } = await post(`/api/targets/${id}/search-now`);
  toast(`Search #${searchId} started — watch it on the Search page`, 'ok');
}

// ----------------------------------------------------------- templates ---
routes['/templates'] = async () => {
  const templates = await api('/api/templates');
  view.innerHTML = `
    <div class="page-head"><div><h1>Templates</h1>
      <div class="subtitle">Merge fields: {business_name} {owner_name} {city} {niche}
        {observation} {website_issue} {portfolio_url} {sender_name} {sender_company}</div></div></div>
    <div class="grid cols-2">
      ${templates.map((template) => `
        <div class="card">
          <div class="flex between" style="margin-bottom:8px">
            <b>${esc(template.name)}</b>
            <span class="faint">${esc(template.type)} · ${esc(template.target_market)}</span>
          </div>
          ${template.subject ? `<div class="field"><label>Subject</label>
            <input value="${esc(template.subject)}" data-tsub="${template.id}"></div>` : ''}
          <textarea data-tbody="${template.id}" style="min-height:150px">${esc(template.body)}</textarea>
          <button class="sm" style="margin-top:8px"
            onclick="saveTemplate('${esc(template.name)}', '${esc(template.type)}',
              '${esc(template.stage || '')}', '${esc(template.target_market)}', ${template.id})">
            Save</button>
        </div>`).join('')}
    </div>`;
};

async function saveTemplate(name, type, stage, market, id) {
  const subject = document.querySelector(`[data-tsub="${id}"]`);
  const body = document.querySelector(`[data-tbody="${id}"]`);
  await post('/api/templates', {
    name, type, stage: stage || null, target_market: market,
    subject: subject ? subject.value : null, body: body.value,
  });
  window.__templates = await api('/api/templates');
  toast('Template saved', 'ok');
}

// ------------------------------------------------------------ settings ---
routes['/settings'] = async () => {
  const [identity, quota, providers, suppression, backups] = await Promise.all([
    api('/api/identity'), api('/api/quota'), api('/api/providers'),
    api('/api/suppression'), api('/api/automation/backups'),
  ]);

  view.innerHTML = `
    <div class="page-head"><div><h1>Settings</h1>
      <div class="subtitle">Keys, caps and schedules live in .env — these are the bits
        you change day to day.</div></div>
      <button onclick="logout()">Sign out</button></div>

    <div class="grid cols-2">
      <div class="card">
        <h3>Your identity in outreach</h3>
        <div class="field"><label>Company name</label>
          <input id="sCompany" value="${esc(identity.company_name)}"></div>
        <div class="field"><label>Portfolio URL</label>
          <input id="sPortfolio" value="${esc(identity.portfolio_url)}"></div>
        <dl class="kv" style="margin:12px 0">
          <dt>Sender name</dt><dd>${esc(identity.sender_name) || '<span class="faint">MAIL_FROM_NAME in .env</span>'}</dd>
          <dt>Sender email</dt><dd>${esc(identity.sender_email) || '<span class="faint">MAIL_FROM in .env</span>'}</dd>
          <dt>Dry run</dt><dd>${identity.dry_run ? 'ON — nothing is really sent' : 'OFF — sending live'}</dd>
        </dl>
        <button class="primary" onclick="saveIdentity()">Save</button>
      </div>

      <div class="card">
        <h3>Provider &amp; quota</h3>
        <dl class="kv">
          <dt>Active provider</dt><dd>${esc(providers.active)}</dd>
          <dt>Google calls today</dt><dd>${quota.google.used_today} / ${quota.google.cap}</dd>
          <dt>By stage</dt><dd class="mono">${esc(JSON.stringify(quota.google.by_tier))}</dd>
          <dt>Hunter.io</dt><dd>${quota.hunter.used_this_month} / ${quota.hunter.cap} this month</dd>
        </dl>
        <div class="banner warn" style="margin-top:12px;font-size:12px">
          This cap is your app-side second layer. Also set a <b>Requests-per-day
          Quota limit</b> on the API in Google Cloud Console — a billing budget alert
          only emails you, it does not stop spending.</div>
      </div>

      <div class="card">
        <h3>Suppression list (${suppression.length})</h3>
        <div class="subtitle" style="margin-bottom:10px">Anyone here is never contacted again,
          checked immediately before every send.</div>
        <div class="row" style="margin-bottom:10px">
          <div><input id="sSuppress" placeholder="email@example.com"></div>
          <div class="shrink"><button onclick="addSuppression()">Add</button></div>
        </div>
        ${table(['Value', 'Reason', 'Added', ''], suppression.map((entry) => `<tr>
          <td class="mono">${esc(entry.value)}</td>
          <td class="faint">${esc(entry.reason)}</td>
          <td class="faint nowrap">${date(entry.created_at)}</td>
          <td class="right"><button class="sm ghost"
            onclick="removeSuppression('${esc(entry.value)}','${esc(entry.kind)}')">✕</button></td>
        </tr>`), 'Empty — nobody has opted out yet.')}
      </div>

      <div class="card">
        <h3>Backups (keep ${backups.keep_days} days)</h3>
        <button style="margin-bottom:10px" onclick="makeBackup()">Back up now</button>
        ${table(['File', { label: 'Size', num: true }, 'When'],
          backups.backups.slice(0, 10).map((backup) => `<tr>
            <td class="mono">${esc(backup.name)}</td>
            <td class="num">${backup.size_kb} KB</td>
            <td class="faint nowrap">${date(backup.created, true)}</td></tr>`),
          'No backups yet — run one now.')}
      </div>
    </div>`;
};

async function saveIdentity() {
  await post('/api/identity', {
    company_name: document.getElementById('sCompany').value,
    portfolio_url: document.getElementById('sPortfolio').value,
  });
  toast('Saved', 'ok');
}

async function addSuppression() {
  const value = document.getElementById('sSuppress').value.trim();
  if (!value) return;
  await post('/api/suppression', { value, reason: 'manual' });
  toast('Added to suppression list', 'ok');
  navigate();
}

async function removeSuppression(value, kind) {
  await del(`/api/suppression?value=${encodeURIComponent(value)}&kind=${kind}`);
  navigate();
}

async function makeBackup() {
  const result = await post('/api/automation/backups');
  toast(`Backup written (${result.size_kb} KB)`, 'ok');
  navigate();
}

async function logout() {
  await post('/api/auth/logout');
  window.location.href = '/login';
}

// ---------------------------------------------------------------- boot ---
(async function boot() {
  try {
    const providers = await api('/api/providers');
    document.getElementById('providerLabel').textContent = `provider: ${providers.active}`;
  } catch (_) { /* not fatal */ }
  refreshBadges();
  navigate();
  setInterval(refreshBadges, 60000);
})();
