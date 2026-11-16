/* Single-page UI, no build step, no dependencies. Branding is server-rendered
   into the shell pages and overridable at /api/branding/theme.css. */

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
const put = (path, data) => api(path, { method: 'PUT', body: JSON.stringify(data) });
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

// -------------------------------------------------------- session & roles ---
const ROLE_LEVEL = { viewer: 0, user: 0, admin: 1, superadmin: 2 };

function me() {
  return window.__me || { user: null, subscription: null };
}

function roleLevel() {
  return ROLE_LEVEL[me().user && me().user.role] ?? 0;
}

function hasMinRole(role) {
  return roleLevel() >= ROLE_LEVEL[role];
}

function isAdmin() { return hasMinRole('admin'); }
function isFree() { return !!(window.__me && window.__me.free_mode); }

const progressBar = (used, cap) => {
  const pct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : 0;
  const cls = pct >= 90 ? 'danger' : pct >= 70 ? '' : 'ok';
  return `<div class="bar ${cls}" style="margin:6px 0">
    <span style="width:${pct}%"></span></div>`;
};

function configureNav() {
  const admin = isAdmin();
  document.getElementById('admingroup').style.display = admin ? '' : 'none';
  document.getElementById('navUsers').style.display = admin ? '' : 'none';
  document.getElementById('navPlans').style.display = admin && !isFree() ? '' : 'none';
  document.querySelectorAll('#nav a[href="#/myplan"]').forEach((a) => { a.style.display = isFree() ? 'none' : ''; });
  document.getElementById('navSettings').style.display = admin ? '' : 'none';
  ['navIntegrations', 'navAudit'].forEach((id) => { const n = document.getElementById(id); if (n) n.style.display = admin ? '' : 'none'; });
  const user = me().user;
  if (user) {
    const name = [user.full_name, user.username].filter(Boolean).join(' · ');
    document.getElementById('meName').textContent =
      `${name} (${(user.role || 'user').replace(/_/g, ' ')})`;
    const avatar = document.getElementById('meAvatar');
    if (avatar) {
      avatar.textContent = (user.full_name || user.username || '?').trim().charAt(0).toUpperCase();
    }
    document.getElementById('sidebarFoot').style.display = '';
  }
}

// -------------------------------------------------------------- router ---
const routes = {};
let currentPath = '';

const ADMIN_ONLY = { '/users': 1, '/plans': 1, '/settings': 1 };

function navigate() {
  const hash = window.location.hash.slice(1) || '/dashboard';
  const [rawPath, queryString] = hash.split('?');
  let path = rawPath;
  if (!hasMinRole('admin') && ADMIN_ONLY[path]) path = '/dashboard';
  if (isFree() && (path === '/myplan' || path === '/plans')) path = '/dashboard';
  currentPath = path;
  const params = Object.fromEntries(new URLSearchParams(queryString || ''));

  document.querySelectorAll('#nav a').forEach((link) => {
    link.classList.toggle('active', link.getAttribute('href') === `#${path}`);
  });

  const render = routes[path] || routes['/dashboard'];
  // The map holds a live canvas and listeners against a DOM node that the
  // innerHTML swap below is about to throw away, so it goes first.
  disposeMap();
  view.innerHTML = `
    <div class="page-head"><div>
      <div class="sk sk-title"></div><div class="sk sk-sub"></div>
    </div></div>
    <div class="grid cols-5">${Array(5).fill(`
      <div class="card sk-card"><div class="sk sk-line w40"></div><div class="sk sk-big"></div></div>`).join('')}
    </div>
    <div class="grid cols-2" style="margin-top:16px">${Array(2).fill(`
      <div class="card sk-card"><div class="sk sk-line w60"></div><div class="sk sk-block"></div></div>`).join('')}
    </div>`;
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

  const warnings = [];
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
      <div class="card"><h3>Data sources</h3>
        <p class="muted" style="margin:0 0 10px">Free and open source - no keys, no quotas.</p>
        <dl class="kv">
          <dt>Active provider</dt><dd>${esc(stats.quota.provider)}</dd>
          <dt>Pending enrichment</dt><dd>${stats.pending_enrichment}</dd>
        </dl>
        <a class="btn sm" href="#/discover" style="margin-top:10px">See all sources</a>
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
  const [providers, history, plan] = await Promise.all([
    api('/api/providers'), api('/api/search/history?limit=15'),
    me().user && me().user.role !== 'superadmin' && !isFree()
      ? api('/api/me/subscription') : Promise.resolve({ subscription: null }),
  ]);

  const sub = plan ? plan.subscription : null;
  let quotaBanner = '';
  let locked = false;
  if (isFree()) {
    quotaBanner = `<div class="banner info">Free and open source. Every feature included.</div>`;
  } else if (me().user && me().user.role === 'superadmin') {
    quotaBanner = '';
  } else if (!sub) {
    quotaBanner = `<div class="banner warn">No active subscription. Searches are paused until
      you have an active plan. <a href="#/myplan" style="color:inherit">Choose a plan</a>.</div>`;
    locked = true;
  } else if (sub.searches_used >= sub.search_limit) {
    quotaBanner = `<div class="banner err">Plan search limit reached (${sub.searches_used}/${sub.search_limit}).
      Upgrade or wait for renewal on <a href="#/myplan" style="color:inherit">My Plan</a>.</div>`;
    locked = true;
  } else {
    quotaBanner = `<div class="banner info">Plan ${esc(sub.plan_name)} ·
      <b>${sub.search_limit - sub.searches_used}</b> of ${sub.search_limit} searches left
      · up to ${sub.max_results_per_search} results per search ·
      renews ${date(sub.period_end)}.</div>`;
  }

  const rows = history.map((run) => `<tr onclick="location.hash='#/leads?search=${run.id}'">
      <td class="faint">#${run.id}</td>
      <td>${esc(run.niche)}</td>
      <td>${esc(run.area || run.city)}</td>
      <td class="num">${run.results_found}</td>
      <td class="num" style="color:var(--ok)">${run.new_leads}</td>
      <td class="num faint">${run.duplicates_skipped}</td>
      <td>${pill(run.status)}${run.from_cache ? '<span style="color:var(--info);font-size:11px"> cached</span>' : ''}</td>
      <td class="faint nowrap">${date(run.started_at, true)}</td></tr>`);

  view.innerHTML = `
    <div class="page-head"><div><h1>Search</h1>
      <div class="subtitle">Every search is logged, so you never search the same place twice.</div>
    </div></div>
    ${quotaBanner}

    <div class="card" style="margin-bottom:16px">
      <div class="row">
        <div><label>Niche</label><input id="niche" placeholder="restaurants" value="restaurants"></div>
        <div><label>City</label><input id="city" placeholder="Faridabad"></div>
        <div><label>Area / sector (optional)</label><input id="area" placeholder="Sector 15"></div>
        <div><label>Country (optional)</label><input id="country" placeholder="India"></div>
        <div class="shrink" style="width:110px"><label>Max results</label>
          <input id="max" type="number" value="20" min="1"></div>
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

  if (locked) {
    document.getElementById('go').disabled = true;
    document.getElementById('useLocation').disabled = true;
  }

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

  box.innerHTML = (run.from_cache
    ? `<div class="banner info">Search #${searchId} served from cache
        ${run.cache_source_search_id ? `(search #${run.cache_source_search_id})` : ''} —
        <b>${(run.leads || []).length} results</b> reused, <b>0 API calls</b> made.</div>`
    : `<div class="banner info">Search #${searchId} done —
        <b>${run.new_leads} new leads</b>, ${run.duplicates_skipped} duplicates skipped,
        ${run.results_found} results seen.
        ${run.hot_count ? `<b style="color:var(--hot)">${run.hot_count} HOT</b>.` : ''}</div>`)
    + table(['Score', 'Business', 'Priority', 'Phone', 'Website'], leads,
      run.from_cache ? 'No cached results.' : 'No new leads - everything found was already in your database.');
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
        <button id="exportJsonBtn">Export JSONL</button>
        <button id="exportVcfBtn">Export vCard</button>
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
  document.getElementById('exportVcfBtn').onclick = () => {
    const query = new URLSearchParams(
      Object.entries(leadFilters).filter(([k, v]) =>
        v !== undefined && !['limit', 'offset', 'sort', 'direction'].includes(k)));
    query.set('format', 'vcf');
    window.location.href = `/api/leads/export?${query}`;
  };
  document.getElementById('exportJsonBtn').onclick = () => {
    const query = new URLSearchParams(
      Object.entries(leadFilters).filter(([k, v]) =>
        v !== undefined && !['limit', 'offset', 'sort', 'direction'].includes(k)));
    query.set('format', 'jsonl');
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
  if (tab === 'analysis') { loadEvidence(lead.id); loadFit(lead.id); loadVelocity(lead.id); }
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
    <ul class="issues">${issues || '<li class="faint">No issues recorded.</li>'}</ul>
    <div id="evidenceSlot"></div>
    <div id="fitSlot"></div>
    <div id="velocitySlot"></div>`;
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
    <div class="card" style="margin-bottom:14px">
      <h3>Draft with local AI <span class="pill COLD" style="margin-left:6px">optional</span></h3>
      <p class="muted" style="font-size:12px;margin:0 0 8px">Uses an open-source model on your own computer (Ollama).
        Nothing is sent anywhere; you review every draft.</p>
      <div class="row"><div><label>What you offer</label><input id="aiOffer" placeholder="Modern websites for local clinics"></div>
        <div class="shrink"><button class="primary" onclick="aiDraft(${lead.id})">Write draft</button></div></div>
      <div id="aiOut" style="margin-top:10px"></div>
    </div>
    <div class="card" style="margin-bottom:14px">
      <h3>Log a reply</h3>
      <p class="muted" style="font-size:12px;margin:0 0 8px">Paste what they wrote. The app works out
        what they want, stops pending follow-ups, honours STOP requests and moves the lead.</p>
      <textarea id="replyText" rows="4" placeholder="Paste the prospect's reply..."></textarea>
      <div class="flex" style="margin-top:8px">
        <button onclick="previewReply()">Preview</button>
        <button class="primary" onclick="applyReply(${lead.id})">Log reply & update lead</button>
        <button class="danger sm" onclick="markLost(${lead.id})">Mark lost...</button>
      </div>
      <div id="replyOut" style="margin-top:10px"></div>
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
    <div id="healthSlot"></div>
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
  loadPipelineHealth();
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

let googleMapsReady = null;

// Google Maps is loaded on first visit to this page rather than on every page
// load: the rest of the app doesn't need it and it is ~150KB. Unlike the
// Leaflet build that lived here before, this one is fetched from Google's CDN -
// the Maps JS API is only served there - so the map page now needs a network
// round trip to googleapis.com and a correctly configured key. Everything else
// in the app still runs with no internet and no key.
function loadGoogleMaps() {
  if (window.google && window.google.maps) return Promise.resolve(window.google.maps);
  if (googleMapsReady) return googleMapsReady;

  const key = (document.querySelector('meta[name="google-maps-key"]')?.content || '').trim();
  if (!key) {
    return Promise.reject(new Error(
      'No Google Maps key configured - set GOOGLE_MAPS_JS_KEY in .env and restart'));
  }

  const load = new Promise((resolve, reject) => {
    let timer;
    const done = (fn, value) => {
      clearTimeout(timer);
      delete window.__googleMapsReady;
      fn(value);
    };
    // loading=async keeps the library off the critical path, which means
    // onload only says the bootstrap file arrived, not that google.maps is
    // usable yet - the callback is the signal we actually want.
    window.__googleMapsReady = () => done(resolve, window.google.maps);
    // A silently hung script leaves a blank box and a spinner that never
    // resolves; fail loudly instead so the page can say what went wrong.
    timer = setTimeout(() => done(reject, new Error('Google Maps did not load in time')),
      15000);
    const s = document.createElement('script');
    s.async = true;
    s.onerror = () => done(reject, new Error('Could not reach Google Maps'));
    s.src = 'https://maps.googleapis.com/maps/api/js?key=' + encodeURIComponent(key)
      + '&v=weekly&loading=async&callback=__googleMapsReady';
    document.head.appendChild(s);
  });

  // A failed load is deliberately not cached: coming back to the page should
  // retry after a network hiccup, not replay a rejection from ten minutes ago.
  googleMapsReady = load.catch((err) => { googleMapsReady = null; throw err; });
  return googleMapsReady;
}

const PRIORITY_COLOUR = { HOT: '#f85149', WARM: '#d29922', COLD: '#6e7681' };

// Google's own dark styling needs a cloud-based map ID to apply. With one
// configured we ask for colorScheme instead; without one the map falls back to
// raster tiles and these legacy styles are the only way to keep it dark. The two
// are mutually exclusive - passing both throws - so exactly one is ever set.
const GOOGLE_DARK_STYLES = [
  { elementType: 'geometry', stylers: [{ color: '#212121' }] },
  { elementType: 'labels.text.stroke', stylers: [{ color: '#212121' }] },
  { elementType: 'labels.text.fill', stylers: [{ color: '#9e9e9e' }] },
  { elementType: 'labels.icon', stylers: [{ visibility: 'off' }] },
  { featureType: 'administrative', elementType: 'geometry.stroke', stylers: [{ color: '#4b4b4b' }] },
  { featureType: 'administrative.land_parcel', stylers: [{ visibility: 'off' }] },
  { featureType: 'poi', stylers: [{ visibility: 'off' }] },
  { featureType: 'road', elementType: 'geometry', stylers: [{ color: '#3a3a3a' }] },
  { featureType: 'road', elementType: 'geometry.stroke', stylers: [{ color: '#2b2b2b' }] },
  { featureType: 'road', elementType: 'labels.text.fill', stylers: [{ color: '#8a8a8a' }] },
  { featureType: 'road.highway', elementType: 'geometry', stylers: [{ color: '#4a4a4a' }] },
  { featureType: 'transit', stylers: [{ visibility: 'off' }] },
  { featureType: 'water', elementType: 'geometry', stylers: [{ color: '#111318' }] },
  { featureType: 'water', elementType: 'labels.text.fill', stylers: [{ color: '#5f6b7a' }] },
];

let mapInstance = null;
let infoWindow = null;
let mapMarkers = [];
let youAreHere = null;

function clearMapMarkers() {
  mapMarkers.forEach((marker) => marker.setMap(null));
  mapMarkers = [];
  if (youAreHere) { youAreHere.setMap(null); youAreHere = null; }
}

// Navigating away replaces the container div underneath a live map. Tearing
// the map down first stops the canvas, its listeners and its markers outliving
// the page that owned them.
function disposeMap() {
  if (!mapInstance) return;
  if (infoWindow) infoWindow.close();
  clearMapMarkers();
  window.google.maps.event.clearInstanceListeners(mapInstance);
  mapInstance.unbindAll();
  mapInstance = null;
}

function popupHtml(lead, colour) {
  return `
    <div class="map-popup">
      <b>${esc(lead.business_name)}</b><br>
      <span style="color:${colour}">${esc(lead.lead_priority)} &middot;
        ${lead.lead_score}/10</span>
      <span style="color:var(--text-dim)"> &middot; ${esc(lead.status || '')}</span><br>
      <span style="color:var(--text-dim)">${esc([lead.area, lead.city].filter(Boolean).join(', '))}</span><br>
      ${lead.phone ? `<span style="color:var(--text-dim)">${esc(lead.phone)}</span><br>` : ''}
      ${lead.has_website ? '' : '<b style="color:#f85149">No website</b><br>'}
      <a href="#" onclick="closeMapPopupAndOpen(${lead.id});return false">Open lead</a>
      &nbsp;|&nbsp;
      <a target="_blank" rel="noopener"
         href="https://www.google.com/maps/search/?api=1&query=${lead.lat},${lead.lng}">
         Google Maps</a>
    </div>`;
}

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

  let maps;
  try {
    maps = await loadGoogleMaps();
  } catch (err) {
    document.getElementById('mapCount').textContent = 'Map unavailable';
    document.getElementById('mapCanvas').innerHTML =
      `<div class="empty">${esc(err.message)}.</div>`;
    return;
  }

  const mapId = (document.querySelector('meta[name="google-maps-map-id"]')?.content || '').trim();
  mapInstance = new maps.Map(document.getElementById('mapCanvas'), {
    center: { lat: 22.5, lng: 78.9 },
    zoom: 4,
    // Matches the scroll-zoom the Leaflet build had, so a wheel over the map
    // still zooms instead of scrolling the page behind it.
    gestureHandling: 'greedy',
    ...(mapId
      ? { mapId, colorScheme: maps.ColorScheme.DARK }
      : { styles: GOOGLE_DARK_STYLES }),
  });
  // One InfoWindow reused for every pin: fewer objects than one each, and it
  // makes close-on-navigate a single call.
  infoWindow = new maps.InfoWindow({ maxWidth: 260, minWidth: 200 });

  async function draw() {
    const priority = document.getElementById('mapPriority').value;
    const data = await api(`/api/geo/map${priority ? `?priority=${priority}` : ''}`);
    clearMapMarkers();

    const bounds = new maps.LatLngBounds();
    data.leads.forEach((lead) => {
      const colour = PRIORITY_COLOUR[lead.lead_priority] || PRIORITY_COLOUR.COLD;
      const position = { lat: lead.lat, lng: lead.lng };
      const marker = new maps.Marker({
        map: mapInstance,
        position,
        title: lead.business_name,
        // A scaled circle, so dot size can carry the score exactly the way the
        // old circleMarker radius did.
        icon: {
          path: maps.SymbolPath.CIRCLE,
          scale: 5 + (lead.lead_score || 0) * 0.6,
          fillColor: colour, fillOpacity: 0.65,
          strokeColor: colour, strokeWeight: 1.5,
        },
      });
      marker.addListener('click', () => {
        infoWindow.setContent(popupHtml(lead, colour));
        infoWindow.open({ map: mapInstance, anchor: marker });
      });
      mapMarkers.push(marker);
      bounds.extend(position);
    });

    document.getElementById('mapCount').textContent =
      `${data.count} lead${data.count === 1 ? '' : 's'} with coordinates`;
    // fitBounds chooses whatever zoom fits the points; clamp to the same
    // ceiling the Leaflet build used so one lead doesn't zoom to street level.
    if (data.leads.length) {
      mapInstance.fitBounds(bounds, 40);
      if (mapInstance.getZoom() > 15) mapInstance.setZoom(15);
    }
  }

  document.getElementById('mapPriority').onchange = draw;
  document.getElementById('mapLocate').onclick = async () => {
    try {
      const { lat, lng } = await currentPosition();
      mapInstance.setCenter({ lat, lng });
      mapInstance.setZoom(14);
      youAreHere = new maps.Marker({
        map: mapInstance,
        position: { lat, lng },
        title: 'You are here',
        zIndex: 7,
        icon: {
          path: maps.SymbolPath.CIRCLE,
          scale: 9,
          fillColor: '#58a6ff', fillOpacity: 0.35,
          strokeColor: '#58a6ff', strokeWeight: 2,
        },
      });
      infoWindow.setContent('You are here');
      infoWindow.open({ map: mapInstance, anchor: youAreHere });
    } catch (err) {
      toast(err.message, 'err');
    }
  };

  await draw();
};

function closeMapPopupAndOpen(leadId) {
  if (infoWindow) infoWindow.close();
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

// ------------------------------------------------------- new startups ---
// Newly registered companies (UK Companies House / India MCA) and their owners.
routes['/startups'] = async () => {
  view.innerHTML = `
    <div class="page-head"><div><h1>New startups</h1>
      <div class="subtitle">New startups found on the open web (AI search) - find, review and update</div></div></div>
    <div class="card" style="margin-bottom:12px">
      <div class="flex" style="gap:8px;flex-wrap:wrap">
        <input id="su-niche" value="tech" placeholder="niche words (tech, software, cafe)">
        <input id="su-city" placeholder="city (optional)">
        <select id="su-country"><option value="both">UK + India</option><option value="uk">UK</option><option value="india">India</option></select>
        <button id="su-find">Find new startups</button>
      </div>
    </div>
    <div class="card">
      <div class="flex" style="gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <input id="su-q" placeholder="filter: name, address, owner">
        <select id="su-lc"><option value="">All countries</option><option>United Kingdom</option><option>India</option></select>
        <label class="flex" style="gap:4px"><input type="checkbox" id="su-closed"> show closed</label>
        <button id="su-reload" class="sm">Reload</button>
        <button id="su-dry" class="sm">Check (preview)</button>
        <button id="su-apply" class="sm">Check &amp; apply</button>
      </div>
      <div id="su-msg" class="faint" style="margin-bottom:8px"></div>
      <div style="overflow-x:auto"><table><thead><tr><th>Company</th><th>Owner</th><th>Registered</th>
        <th>Reg. no.</th><th>Location</th><th>Contact</th><th>Status</th><th></th></tr></thead>
        <tbody id="su-rows"></tbody></table></div>
    </div>`;
  const $ = (id) => document.getElementById(id);
  const say = (t) => { $('su-msg').textContent = t; };
  async function load(keep) {
    const p = new URLSearchParams({ q: $('su-q').value, country: $('su-lc').value, closed: $('su-closed').checked });
    try {
      const d = await api('/api/startups?' + p);
      $('su-rows').innerHTML = d.startups.map((s) => `
        <tr style="${s.business_status === 'CLOSED_PERMANENTLY' ? 'opacity:.55' : ''}">
          <td><a href="javascript:void(0)" onclick="openLead(${s.id})">${esc(s.business_name)}</a></td>
          <td>${esc(s.owner_name || '-')}</td><td>${esc(s.registered_on || '-')}</td>
          <td>${esc(s.registration_number)}</td>
          <td>${esc([s.city, s.state, s.pincode].filter(Boolean).join(', ') || '-')}</td>
          <td>${esc([s.email, s.phone, s.website_url].filter(Boolean).join(' | ') || '-')}</td>
          <td>${esc(s.business_status === 'CLOSED_PERMANENTLY' ? 'closed' : s.status)}</td>
          <td>${s.registry === 'uk' ? `<button class="sm" data-owners="${s.id}">Get owners</button>` : ''}</td>
        </tr>`).join('');
      if (!keep) say(`${d.count} compan${d.count === 1 ? 'y' : 'ies'}`);
      $('su-rows').querySelectorAll('[data-owners]').forEach((b) => b.onclick = async () => {
        try {
          const r = await post(`/api/startups/${b.dataset.owners}/owners`);
          say(r.officers.map((o) => `${o.name} (${o.role})`).join(', ') || 'No current officers');
          load();
        } catch (e) { say(e.message); }
      });
    } catch (e) { say(e.message); }
  }
  async function check(dry) {
    if (!dry && !confirm('Apply: update addresses, mark dissolved companies closed, and DELETE dead leads you never contacted?')) return;
    try {
      const r = await post(`/api/startups/check?dry_run=${dry}`);
      say(`${dry ? 'Preview' : 'Applied'}: ${r.checked} checked, ${r.updated} to update, ${r.retired} closed, ${r.removed} removed, ${r.unknown} not found in registry`);
      if (!dry) load();
    } catch (e) { say(e.message); }
  }
  $('su-reload').onclick = load;
  $('su-q').onkeydown = (e) => { if (e.key === 'Enter') load(); };
  $('su-dry').onclick = () => check(true);
  $('su-apply').onclick = () => check(false);
  $('su-find').onclick = async () => {
    try {
      const r = await post('/api/startups/find', { niche: $('su-niche').value, city: $('su-city').value, country: $('su-country').value });
      const ids = r.search_ids || [];
      const names = { uk_all: 'UK', india_all: 'India' };
      const FIN = ['COMPLETED', 'FAILED', 'STOPPED_QUOTA', 'CANCELLED'];
      const started = Date.now();
      let sts = [];
      while (Date.now() - started < 10 * 60 * 1000) {
        sts = await Promise.all(ids.map((id) => api(`/api/search/${id}/status`).catch(() => null)));
        if (!document.getElementById('su-find')) return;   // navigated away
        say('Searching... ' + sts.map((x, i) => x ? `[${x.country || i + 1}] ${x.status}: ${x.progress || ''}` : '[?] no status').join('  '));
        await load(true);                                  // show leads as they arrive
        if (sts.every((x) => x && FIN.includes(x.status))) break;
        await new Promise((res) => setTimeout(res, 3000));
      }
      await load(true);
      const errs = sts.filter((x) => x && x.error_message).map((x) => `${x.country || ''}: ${x.error_message}`);
      const found = sts.reduce((n, x) => n + ((x && x.new_leads) || 0), 0);
      say(errs.length ? 'Search problem: ' + errs.join(' | ')
          : (sts.every((x) => x && FIN.includes(x.status)) ? `Done - ${found} new companies added.` : 'Still running in the background - click Reload later.'));
    } catch (e) { say(e.message); }
  };
  load();
};

// ----------------------------------------------------------- demo sites ---
const DEMO_SITES = [
  { name: 'Chartered Accountant', url: 'https://ca.demo.arthvex.co.in/' },
  { name: 'Dry Cleaner', url: 'https://cleanthut.arthvex.co.in/' },
  { name: 'Computer Service', url: 'https://websites.demo.arthvex.co.in/' },
  { name: 'Tiffin Service', url: 'https://tiffin.arthvex.co.in/' },
];

routes['/demos'] = async () => {
  view.innerHTML = `
    <div class="page-head"><div><h1>Demo websites</h1>
      <div class="subtitle">Finished sample sites to show prospects in the same niche</div></div></div>
    <div class="grid cols-2">
      ${DEMO_SITES.map((site) => `
        <div class="card">
          <div class="flex between" style="margin-bottom:8px">
            <b>${esc(site.name)}</b>
            <a href="${esc(site.url)}" target="_blank" rel="noopener">
              <button class="sm">Open</button></a>
          </div>
          <div class="faint">${esc(site.url)}</div>
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

// -------------------------------------------------------------- my plan ---
routes['/myplan'] = async () => {
  const [plan, plans, payment, history] = await Promise.all([
    api('/api/me/subscription'),
    api('/api/plans'),
    api('/api/settings/payment'),
    api('/api/me/subscriptions'),
  ]);
  const sub = plan.subscription;
  const user = me().user;

  const planCard = sub
    ? `<div class="card">
        <div class="flex between" style="margin-bottom:6px">
          <h3 style="margin:0">${esc(sub.plan_name)}</h3>
          <span class="pill active">Active</span></div>
        <dl class="kv">
          <dt>Price</dt><dd>${esc(sub.currency)} ${sub.price} / ${sub.period_days}d</dd>
          <dt>Renews</dt><dd>${date(sub.period_end)}</dd>
          <dt>Searches used</dt><dd>${sub.searches_used} / ${sub.search_limit}</dd>
          <dt>Results per search</dt><dd>up to ${sub.max_results_per_search}</dd>
          <dt>Source</dt><dd>${esc(sub.channel)} · ${esc(sub.payment_status)}</dd>
        </dl>
        ${progressBar(sub.searches_used, sub.search_limit)}
        <div class="faint" style="font-size:12px">${sub.searches_used >= sub.search_limit
          ? 'Limit reached — manual searches are paused until renewal or an upgrade.'
          : 'Each completed manual search counts one against your limit.'}</div>
      </div>`
    : `<div class="card"><div class="empty">No active subscription.
        ${user && user.role === 'superadmin'
          ? 'Superadmins are never gated — you can still search freely.'
          : 'Manual searches are blocked until you pick a plan.'}
      </div></div>`;

  const buyVisible = !sub;
  const buyCard = `<div class="card">
      <h3>${payment.enabled ? 'Buy a plan' : 'Plans'}</h3>
      ${plans.rows.length ? plans.rows.map((p) => `
        <div class="flex between" style="padding:9px 0;border-bottom:1px solid var(--line)">
          <div>
            <b>${esc(p.name)}</b>
            <div class="faint" style="font-size:12px">${esc(p.currency)} ${p.price} / ${p.period_days}d
              · ${p.search_limit} searches · up to ${p.max_results_per_search} results</div>
          </div>
          ${buyVisible && payment.enabled
            ? `<button class="sm primary" onclick="orderPlan(${p.id}, '${esc(p.name)}')">Buy</button>`
            : ''}
        </div>`).join('')
        : '<div class="empty">No active plans right now.</div>'}
      ${buyVisible && !payment.enabled
        ? `<div class="banner warn" style="margin-top:10px;font-size:12px">Self-serve
            ordering is switched off. Contact an admin to assign a plan.</div>` : ''}
    </div>`;

  const historyRows = history.map((s) => `<tr>
      <td>${esc(s.plan_name)}</td>
      <td>${pill(s.status)}</td>
      <td class="faint nowrap">${date(s.created_at, true)}</td>
      <td class="faint">${s.channel} · ${s.payment_status}</td></tr>`);

  view.innerHTML = `
    <div class="page-head"><div><h1>My Plan</h1>
      <div class="subtitle">${user ? esc(user.full_name || user.username) : ''}</div></div></div>
    ${sub ? '' : buyVisible ? `<div class="banner warn">You need an active subscription
      to search leads. Pick a plan below to place an order — an admin approves it.</div>` : ''}
    <div class="grid cols-2">
      ${planCard}
      ${buyCard}
    </div>
    <h3 style="margin-top:22px">Subscription history</h3>
    ${table(['Plan', 'Status', 'Ordered', 'Source'], historyRows,
      'No orders yet.')}`;
};

async function orderPlan(planId, planName) {
  await post('/api/me/subscriptions', { plan_id: planId });
  toast(`Order for ${planName} placed — waiting on approval`, 'ok');
  navigate();
}

// ---------------------------------------------------------------- users ---
routes['/users'] = async () => {
  const data = await api('/api/users?per_page=500');
  const rows = data.rows.map((user) => `<tr>
    <td class="mono">${esc(user.username)}</td>
    <td>${esc(user.full_name || '—')}</td>
    <td>${esc(user.email || '—')}</td>
    <td>${pill(user.role)}</td>
    <td>${user.is_active ? '<span class="pill ok">active</span>' : '<span class="pill faint">off</span>'}</td>
    <td class="faint nowrap">${date(user.created_at)}</td>
    <td class="right">
      <button class="sm" onclick="openAssignDrawer(${user.id}, '${esc(user.username)}')">Assign plan</button>
      <button class="sm" onclick="openUserDrawer(${user.id})">Edit</button>
      <button class="sm ghost" onclick="deleteUser(${user.id}, '${esc(user.username)}')">✕</button>
    </td></tr>`);

  view.innerHTML = `
    <div class="page-head"><div><h1>Users</h1>
      <div class="subtitle">${data.total} account(s)</div></div>
      <button class="primary" onclick="openUserDrawer()">New user</button></div>
    ${table(['Username', 'Name', 'Email', 'Role', 'Status', 'Joined', ''], rows,
      'No users yet — create one.')}`;
};

async function openUserDrawer(userId) {
  const user = userId ? (await api(`/api/users?q=`)).rows.find((u) => u.id === userId) : null;
  const features = (await api('/api/permissions/features').catch(() => ({ features: [] }))).features;
  const denied = new Set((user && user.denied_features) || []);
  const myRole = me().user.role;
  const roleOptions = ['user', 'viewer', 'admin', 'superadmin'].map((r) =>
    `<option value="${r}" ${user && user.role === r ? 'selected' : ''}
      ${r !== 'user' && r !== 'viewer' && myRole !== 'superadmin' ? 'disabled' : ''}
      ${r === 'superadmin' && myRole !== 'superadmin' ? 'disabled' : ''}>${r}</option>`).join('');

  drawerRoot.innerHTML = `
    <div class="drawer-backdrop" onclick="closeDrawer()"></div>
    <div class="drawer">
      <div class="drawer-head"><div><h2>${user ? 'Edit user' : 'New user'}</h2>
        <div class="faint" style="font-size:12px">Only the superadmin can set privileged roles.</div></div>
        <button class="sm ghost" onclick="closeDrawer()">✕</button></div>
      <div class="field"><label>Username</label>
        <input id="uName" value="${user ? esc(user.username) : ''}"></div>
      <div class="field"><label>Full name</label>
        <input id="uFull" value="${user ? esc(user.full_name) : ''}"></div>
      <div class="field"><label>Email</label>
        <input id="uEmail" value="${user ? esc(user.email) : ''}"></div>
      <div class="field"><label>Password ${user ? '(leave blank to keep current)' : ''}</label>
        <input id="uPass" type="password" autocomplete="new-password"></div>
      <div class="field"><label>Role</label>
        <select id="uRole">${roleOptions}</select></div>
      <div class="field"><label>Feature access <span class="faint">(untick to switch a feature off for this user)</span></label>
        ${features.map((f) => `<label style="display:flex;align-items:center;gap:8px;margin:4px 0">
          <input type="checkbox" class="uFeat" value="${esc(f.key)}" ${denied.has(f.key) ? '' : 'checked'}> ${esc(f.label)}</label>`).join('')}
        <div class="faint" style="font-size:12px;margin-top:4px">Admins always have full access. Read-only “viewer” accounts can't change anything regardless.</div></div>
      <label class="row" style="display:flex;align-items:center;gap:8px;margin-bottom:14px">
        <input id="uActive" type="checkbox" ${!user || user.is_active ? 'checked' : ''}>
        Account active</label>
      <button class="primary" style="width:100%;justify-content:center"
        onclick="saveUser(${user ? user.id : 'null'})">${user ? 'Save changes' : 'Create user'}</button>
    </div>`;
}

async function saveUser(userId) {
  const payload = {
    username: document.getElementById('uName').value.trim(),
    full_name: document.getElementById('uFull').value.trim(),
    email: document.getElementById('uEmail').value.trim(),
    password: document.getElementById('uPass').value,
    role: document.getElementById('uRole').value,
    is_active: document.getElementById('uActive').checked,
  };
  if (!payload.username) return toast('Username is required', 'err');
  if (!userId && !payload.password) return toast('A password is required for new users', 'err');
  const saved = await (userId ? patch(`/api/users/${userId}`, payload) : post('/api/users', payload));
  if (!['admin', 'superadmin'].includes(payload.role)) {
    const denied = [...document.querySelectorAll('.uFeat')].filter((c) => !c.checked).map((c) => c.value);
    await patch(`/api/users/${(saved && saved.id) || userId}/permissions`, { denied });
  }
  closeDrawer();
  toast('User saved', 'ok');
  navigate();
}

async function deleteUser(userId, username) {
  if (!confirm(`Delete ${username}? Their subscriptions stay on record but they lose access.`)) return;
  await del(`/api/users/${userId}`);
  toast('User deleted', 'ok');
  navigate();
}

async function openAssignDrawer(userId, username) {
  const plans = await api('/api/plans');
  drawerRoot.innerHTML = `
    <div class="drawer-backdrop" onclick="closeDrawer()"></div>
    <div class="drawer">
      <div class="drawer-head"><div><h2>Assign plan to ${esc(username)}</h2>
        <div class="faint" style="font-size:12px">This starts a fresh period and rolls any
          existing active plan off.</div></div>
        <button class="sm ghost" onclick="closeDrawer()">✕</button></div>
      <div class="field"><label>Plan</label>
        <select id="assignPlan">${plans.rows.map((p) =>
          `<option value="${p.id}">${esc(p.name)} — ${p.search_limit} searches /
            ${p.period_days}d</option>`).join('')}</select></div>
      <div class="field"><label>Period (days, optional)</label>
        <input id="assignDays" type="number" min="1" placeholder="defaults to plan period"></div>
      <button class="primary" style="width:100%;justify-content:center"
        onclick="assignPlan(${userId})">Assign</button>
    </div>`;
}

async function assignPlan(userId) {
  const planId = Number(document.getElementById('assignPlan').value);
  const days = Number(document.getElementById('assignDays').value) || null;
  await post(`/api/users/${userId}/subscriptions`, { plan_id: planId, period_days: days });
  closeDrawer();
  toast('Plan assigned', 'ok');
  navigate();
}

// ----------------------------------------------------------------- plans --
routes['/plans'] = async () => {
  const [plans, subs, payment] = await Promise.all([
    api('/api/plans'), api('/api/subscriptions'), api('/api/settings/payment'),
  ]);

  const planRows = plans.rows.map((p) => `<tr>
    <td><b>${esc(p.name)}</b></td>
    <td class="faint">${esc(p.description || '—')}</td>
    <td class="num">${esc(p.currency)} ${p.price}</td>
    <td class="num">${p.period_days}d</td>
    <td class="num">${p.search_limit}</td>
    <td class="num">${p.max_results_per_search}</td>
    <td>${p.is_active ? '<span class="pill ok">active</span>' : '<span class="pill faint">off</span>'}</td>
    <td class="right">
      <button class="sm" onclick="openPlanDrawer(${p.id})">Edit</button>
      <button class="sm ghost" onclick="deletePlan(${p.id}, '${esc(p.name)}')">✕</button>
    </td></tr>`);

  const subRows = subs.map((s) => `<tr>
    <td>${esc(s.full_name || s.username)}</td>
    <td>${esc(s.plan_name)}</td>
    <td>${pill(s.status)}</td>
    <td class="faint">${s.channel} · ${s.payment_status}</td>
    <td class="num">${s.searches_used}<span class="faint">/${s.search_limit}</span></td>
    <td class="faint nowrap">${date(s.period_start)} → ${date(s.period_end)}</td>
    <td class="faint nowrap">${date(s.created_at, true)}</td>
    <td class="right">
      ${s.status === 'pending' ? `<button class="sm" onclick="approveSub(${s.id})">Approve</button>
        <button class="sm ghost" onclick="rejectSub(${s.id})">Reject</button>` : ''}
      ${s.status === 'active' ? `<button class="sm ghost" onclick="cancelSub(${s.id})">Cancel</button>` : ''}
    </td></tr>`);

  view.innerHTML = `
    <div class="page-head"><div><h1>Plans</h1>
      <div class="subtitle">One active plan per user. Assign or approve to activate.</div></div>
      <button class="primary" onclick="openPlanDrawer()">New plan</button></div>

    <label class="flex" style="justify-content:space-between;align-items:center;
      background:var(--bg-raise);border:1px solid var(--line);border-radius:var(--radius);
      padding:12px 16px;margin-bottom:20px;cursor:pointer">
      <div><b>Self-serve ordering</b>
        <div class="faint" style="font-size:12px">${payment.enabled
          ? 'Users can order plans from “My Plan”.'
          : 'Buy buttons are hidden and ordering is rejected.'}
        ${payment.pending_orders ? ` · <b>${payment.pending_orders} pending order(s)</b>` : ''}</div></div>
      <input id="payToggle" type="checkbox" style="width:20px;height:20px"
        ${payment.enabled ? 'checked' : ''} onchange="togglePayment(this.checked)">
    </label>

    <h3>Plans</h3>
    ${table(['Name', 'Description', 'Price', 'Period', { label: 'Searches', num: true },
      { label: 'Max/result', num: true }, 'Status', ''], planRows, 'No plans yet.')}
    <h3 style="margin-top:22px">Subscriptions</h3>
    ${table(['User', 'Plan', 'Status', 'Source', { label: 'Used', num: true }, 'Period', 'When', ''],
      subRows, 'No subscriptions yet — assign a plan or wait for a user order.')}`;
};

async function openPlanDrawer(planId) {
  let plan = null;
  if (planId) {
    const plans = await api('/api/plans');
    plan = plans.rows.find((p) => p.id === planId) || null;
  }
  const val = (key, dflt) => plan ? (plan[key] ?? dflt) : dflt;
  drawerRoot.innerHTML = `
    <div class="drawer-backdrop" onclick="closeDrawer()"></div>
    <div class="drawer">
      <div class="drawer-head"><div><h2>${plan ? 'Edit plan' : 'New plan'}</h2></div>
        <button class="sm ghost" onclick="closeDrawer()">✕</button></div>
      <div class="field"><label>Name</label><input id="pName" value="${esc(val('name', ''))}"></div>
      <div class="field"><label>Description</label><input id="pDesc" value="${esc(val('description', ''))}"></div>
      <div class="row">
        <div><label>Price</label><input id="pPrice" type="number" step="0.01" min="0" value="${val('price', 0)}"></div>
        <div><label>Currency</label><input id="pCur" value="${esc(val('currency', 'INR'))}"></div>
        <div><label>Period (days)</label><input id="pDays" type="number" min="1" value="${val('period_days', 30)}"></div>
      </div>
      <div class="row">
        <div><label>Search limit</label><input id="pLimit" type="number" min="0" value="${val('search_limit', 50)}"></div>
        <div><label>Max results / search</label><input id="pMax" type="number" min="1" value="${val('max_results_per_search', 20)}"></div>
      </div>
      <label class="flex" style="align-items:center;gap:8px;margin-bottom:14px">
        <input id="pActive" type="checkbox" ${val('is_active', true) ? 'checked' : ''}> Plan is purchasable</label>
      <button class="primary" style="width:100%;justify-content:center"
        onclick="savePlan(${plan ? plan.id : 'null'})">${plan ? 'Save changes' : 'Create plan'}</button>
    </div>`;
}

async function savePlan(planId) {
  const payload = {
    name: document.getElementById('pName').value.trim(),
    description: document.getElementById('pDesc').value.trim(),
    price: Number(document.getElementById('pPrice').value) || 0,
    currency: document.getElementById('pCur').value.trim() || 'INR',
    period_days: Number(document.getElementById('pDays').value) || 30,
    search_limit: Number(document.getElementById('pLimit').value) || 0,
    max_results_per_search: Number(document.getElementById('pMax').value) || 20,
    is_active: document.getElementById('pActive').checked,
  };
  if (!payload.name) return toast('Plan name is required', 'err');
  await (planId ? patch(`/api/plans/${planId}`, payload) : post('/api/plans', payload));
  closeDrawer();
  toast('Plan saved', 'ok');
  navigate();
}

async function deletePlan(planId, name) {
  if (!confirm(`Delete plan "${name}"? Existing subscriptions keep their current terms.`)) return;
  await del(`/api/plans/${planId}`);
  toast('Plan deleted', 'ok');
  navigate();
}

async function approveSub(subId) {
  await post(`/api/subscriptions/${subId}/approve`);
  toast('Approved — plan is active', 'ok');
  navigate();
}

async function rejectSub(subId) {
  await post(`/api/subscriptions/${subId}/reject`);
  toast('Order rejected', 'ok');
  navigate();
}

async function cancelSub(subId) {
  await post(`/api/subscriptions/${subId}/cancel`);
  toast('Subscription cancelled', 'ok');
  navigate();
}

async function togglePayment(enabled) {
  await post('/api/settings/payment', { enabled });
  toast(enabled ? 'Self-serve ordering is ON' : 'Self-serve ordering is OFF', 'ok');
  navigate();
}

// ------------------------------------------------------------ settings ---
routes['/settings'] = async () => {
  const [identity, quota, providers, suppression, backups, payment, brandingData] = await Promise.all([
    api('/api/identity'), api('/api/quota'), api('/api/providers'),
    api('/api/suppression'), api('/api/automation/backups'), api('/api/settings/payment'),
    api('/api/branding'),
  ]);
  window.__branding = brandingData.brand;

  view.innerHTML = `
    <div class="page-head"><div><h1>Settings</h1>
      <div class="subtitle">Keys, caps and schedules live in .env — these are the bits
        you change day to day.</div></div>
      <button onclick="logout()">Sign out</button></div>

    <div class="grid cols-2">

      <div class="card">
        <h3>White-label branding</h3>
        <div class="subtitle" style="margin-bottom:10px">Rename the product, recolour it and
          swap the logo — applied everywhere instantly, no redeploy.</div>
        <div class="field"><label>Product name</label>
          <input id="bName" value="${esc(brandingData.brand.brand_name)}"></div>
        <div class="row">
          <div class="field"><label>Wordmark suffix (the dimmed part)</label>
            <input id="bShort" value="${esc(brandingData.brand.brand_short_name)}" placeholder="auto"></div>
          <div class="field"><label>Tagline</label>
            <input id="bTagline" value="${esc(brandingData.brand.brand_tagline)}"></div>
        </div>
        <div class="row">
          <div class="field"><label>Accent colour</label>
            <input id="bAccent" type="color" value="${esc(brandingData.brand.brand_accent)}"></div>
          <div class="field"><label>Accent highlight</label>
            <input id="bAccent2" type="color" value="${esc(brandingData.brand.brand_accent_2)}"></div>
          <div class="field"><label>Background</label>
            <input id="bBackground" type="color" value="${esc(brandingData.brand.brand_background)}"></div>
        </div>
        <div class="row">
          <div class="field"><label>Logo (png/jpg/webp/svg, ≤1 MB)</label>
            <input id="bLogoFile" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml"></div>
          <div class="field"><label>Favicon</label>
            <input id="bFaviconFile" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml"></div>
        </div>
        <div class="flex wrap" style="margin-top:4px">
          <button class="primary" onclick="saveBranding()">Save branding</button>
          <button onclick="uploadLogo(false)">Upload logo</button>
          <button onclick="uploadLogo(true)">Upload favicon</button>
          <button class="danger" onclick="resetBranding()">Reset to default</button>
        </div>
        ${brandingData.brand.brand_logo ? `<div class="faint" style="margin-top:8px">Custom logo active: <span class="mono">${esc(brandingData.brand.brand_logo)}</span></div>` : ''}
      </div>

      <div class="card">
        <h3>Landing page &amp; contact copy</h3>
        <div class="field"><label>Landing headline</label>
          <input id="bLandingTitle" value="${esc(brandingData.brand.brand_landing_title)}" placeholder="Find businesses that need a website"></div>
        <div class="field"><label>Landing sub-headline</label>
          <textarea id="bLandingSub" style="min-height:64px" placeholder="Auto-generated from the product name if left blank">${esc(brandingData.brand.brand_landing_subtitle)}</textarea></div>
        <div class="row">
          <div class="field"><label>Company name (email footer)</label>
            <input id="bCompany" value="${esc(brandingData.brand.brand_company_name)}" placeholder="= product name"></div>
          <div class="field"><label>Support email (sign-in page)</label>
            <input id="bSupport" value="${esc(brandingData.brand.brand_support_email)}" placeholder="you@yourdomain.com"></div>
        </div>
        <div class="field"><label>Footer line (landing + sign-in)</label>
          <input id="bFooter" value="${esc(brandingData.brand.brand_footer)}" placeholder="Blank = © + company name"></div>
        <button class="primary" onclick="saveBrandingCopy()">Save copy</button>
      </div>
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
        <h3>Provider</h3>
        <dl class="kv">
          <dt>Active provider</dt><dd>${esc(providers.active)}</dd>
          ${providers.active === 'google' ? `<dt>Google calls today</dt><dd>${quota.google.used_today} / ${quota.google.cap}</dd>` : ''}
        </dl>
        ${providers.active === 'google' ? `<div class="banner warn" style="margin-top:12px;font-size:12px">
          Google bills per call, so this spend guard stays on. Also set a budget alert in Google Cloud Console.</div>`
          : '<div class="muted" style="font-size:12px">Free and open-source data - no quota.</div>'}
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

      <div class="card">
        <h3>Passwords &amp; self-serve orders</h3>
        <dl class="kv">
          <dt>Users and plans</dt><dd><a href="#/users">Manage users</a> ·
            <a href="#/plans">Manage plans</a></dd>
          <dt>Self-serve ordering</dt><dd>${payment.enabled ? 'ON' : 'OFF'}</dd>
          <dt>Pending orders</dt><dd>${payment.pending_orders}</dd>
          <dt>Active subscriptions</dt><dd>${payment.active_subscriptions}</dd>
        </dl>
        <label class="flex" style="align-items:center;gap:8px;margin-top:12px;cursor:pointer">
          <input id="payToggle" type="checkbox" ${payment.enabled ? 'checked' : ''}
            onchange="togglePayment(this.checked)"> Allow users to order plans
            themselves</label>
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

// ---------------------------------------------------------- white-label ---
async function saveBranding() {
  await put('/api/branding', {
    brand_name: document.getElementById('bName').value,
    brand_short_name: document.getElementById('bShort').value,
    brand_tagline: document.getElementById('bTagline').value,
    brand_accent: document.getElementById('bAccent').value,
    brand_accent_2: document.getElementById('bAccent2').value,
    brand_background: document.getElementById('bBackground').value,
  });
  toast('Branding saved — reloading', 'ok');
  setTimeout(() => window.location.reload(), 600);
}

async function saveBrandingCopy() {
  await put('/api/branding', {
    brand_landing_title: document.getElementById('bLandingTitle').value,
    brand_landing_subtitle: document.getElementById('bLandingSub').value,
    brand_company_name: document.getElementById('bCompany').value,
    brand_support_email: document.getElementById('bSupport').value,
    brand_footer: document.getElementById('bFooter').value,
  });
  toast('Landing copy saved — reloading', 'ok');
  setTimeout(() => window.location.reload(), 600);
}

async function uploadLogo(favicon) {
  const input = document.getElementById(favicon ? 'bFaviconFile' : 'bLogoFile');
  const file = input.files && input.files[0];
  if (!file) { toast('Choose an image first', 'err'); return; }
  const body = new FormData();
  body.append('file', file);
  const response = await fetch(`/api/branding/logo?favicon=${favicon}`, { method: 'POST', body });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { toast(data.detail || 'Upload failed', 'err'); return; }
  toast('Uploaded — reloading', 'ok');
  setTimeout(() => window.location.reload(), 600);
}

async function resetBranding() {
  if (!confirm('Reset every brand setting, colour and uploaded logo to the default?')) return;
  await post('/api/branding/reset');
  toast('Branding reset — reloading', 'ok');
  setTimeout(() => window.location.reload(), 600);
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

// ------------------------------------------------------------ discover ---
// Lead-discovery toolkit: every way the app can find leads, in one place.
let discoverTab = 'sources';

const DISCOVER_TABS = [
  ['sources', 'Sources'], ['icp', 'Ideal client'], ['intent', 'Intent feed'], ['import', 'CSV import'], ['grid', 'Grid sweep'],
  ['queries', 'Query generator'], ['signals', 'Signals'], ['reviews', 'Review miner'],
  ['lookalikes', 'Lookalikes'], ['api', 'API access'],
];

routes['/discover'] = async (params) => {
  if (params && params.tab) discoverTab = params.tab;
  view.innerHTML = `
    <div class="page-head"><div><h1>Discover</h1>
      <div class="subtitle">Every way to find leads beyond a plain map search.</div></div></div>
    <div class="tabs" style="margin-bottom:16px">${DISCOVER_TABS.map(([key, label]) =>
      `<button class="${key === discoverTab ? 'active' : ''}"
        onclick="discoverTab='${key}';routes['/discover']()">${label}</button>`).join('')}</div>
    <div id="discoverBody"><div class="loading">Loading...</div></div>`;
  const body = document.getElementById('discoverBody');
  try {
    await ({ sources: discoverSources, icp: discoverIcp, intent: discoverIntent, api: discoverApi, import: discoverImport, grid: discoverGrid,
      queries: discoverQueries, signals: discoverSignals, reviews: discoverReviews,
      lookalikes: discoverLookalikes }[discoverTab])(body);
  } catch (error) {
    body.innerHTML = `<div class="banner err">${esc(error.message)}</div>`;
  }
};

async function discoverSources(body) {
  const { sources } = await api('/api/discovery/sources');
  const kinds = { map: 'Map search', registry: 'New-company registry', import: 'Import',
    manual: 'Manual', reverse: 'Reverse prospecting', signal: 'Buying signal' };
  const tabFor = { csv: 'import', osm: 'sources', intent: 'intent', 'osm+grid': 'grid', 'google+grid': 'grid', dorks: 'queries',
    lookalike: 'lookalikes', hiring: 'signals', ads: 'signals', techstack: 'signals',
    domain: 'signals', reviews: 'reviews' };
  const ready = sources.filter((s) => s.ready).length;
  body.innerHTML = `
    <div class="banner info">${ready} of ${sources.length} sources are ready, and every core one is
      <b>free with no key</b>. Sources marked <b>optional</b> add extra coverage when you put their key in <code>.env</code>.</div>
    <div class="grid cols-3">${sources.map((s) => `<div class="card">
      <div class="flex between"><h3 style="margin:0">${esc(s.name)}</h3>
        ${s.ready ? `<span class="pill WARM" style="background:rgba(61,214,140,.12);color:var(--ok);white-space:nowrap;flex-shrink:0">${s.free ? 'free · ready' : 'ready'}</span>`
          : '<span class="pill COLD" style="white-space:nowrap;flex-shrink:0">optional · needs key</span>'}</div>
      <div class="muted" style="font-size:12px;margin:6px 0 10px">${esc(kinds[s.kind] || s.kind)}
        ${s.needs ? `· <code>${esc(s.needs)}</code>` : ''}</div>
      ${['map', 'registry'].includes(s.kind)
        ? `<a class="btn sm" href="#/search">Use in Search</a>`
        : `<button class="sm" onclick="discoverTab='${tabFor[s.id] || 'sources'}';routes['/discover']()">Open</button>`}
    </div>`).join('')}</div>`;
}

async function discoverImport(body) {
  body.innerHTML = `<div class="card" style="max-width:640px">
    <h3>Import a lead list</h3>
    <p class="muted">Upload a CSV from a directory export, CRM, or association roster. Columns are
      matched by name (company, phone, email, website, city...). Rows are de-duplicated against
      your existing leads, then enriched and scored.</p>
    <div class="field"><label>CSV file</label><input type="file" id="impFile" accept=".csv,text/csv"></div>
    <div class="field"><label>Default niche (used when the file has no category column)</label>
      <input id="impNiche" placeholder="e.g. dentist"></div>
    <div class="field"><label><input type="checkbox" id="impEnrich" checked style="width:auto">
      Enrich new leads (website check, email discovery, scoring)</label></div>
    <button class="primary" id="impBtn">Import</button>
    <div id="impResult" style="margin-top:14px"></div></div>`;
  document.getElementById('impBtn').onclick = async () => {
    const file = document.getElementById('impFile').files[0];
    const out = document.getElementById('impResult');
    if (!file) return toast('Choose a CSV file first', 'err');
    const form = new FormData();
    form.append('file', file);
    form.append('niche', document.getElementById('impNiche').value);
    form.append('enrich', document.getElementById('impEnrich').checked);
    out.innerHTML = '<div class="loading">Importing...</div>';
    try {
      const res = await fetch('/api/discovery/import', { method: 'POST', body: form });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) { window.location.href = '/login'; return; }
      if (!res.ok) throw new Error(data.detail || `Import failed (${res.status})`);
      out.innerHTML = `<div class="banner ok">${data.created} new, ${data.merged} merged into
        existing, ${data.failed} failed, ${(data.skipped || []).length} skipped.
        <a href="#/leads" style="color:inherit">View leads</a></div>
        <dl class="kv"><dt>Matched columns</dt><dd>${esc(Object.entries(data.mapped_columns)
          .map(([h, f]) => `${h} → ${f}`).join(', '))}</dd>
        <dt>Ignored columns</dt><dd>${esc((data.ignored_columns || []).join(', ')) || '—'}</dd></dl>
        ${(data.skipped || []).length ? `<ul class="issues">${data.skipped.map((s) =>
          `<li>Row ${s.row}: ${esc(s.reason)}</li>`).join('')}</ul>` : ''}`;
      refreshBadges();
    } catch (error) { out.innerHTML = `<div class="banner err">${esc(error.message)}</div>`; }
  };
}

async function discoverGrid(body) {
  body.innerHTML = `<div class="card" style="max-width:720px">
    <h3>Grid sweep preview</h3>
    <p class="muted">A single map query returns only the top ~20 places. A grid sweep splits the area
      into overlapping cells and searches each one. Preview the cells here, then pick the
      <b>grid sweep</b> provider on the <a href="#/search">Search</a> page.</p>
    <div class="row">
      <div><label>Latitude</label><input id="gLat" type="number" step="any" placeholder="28.6139"></div>
      <div><label>Longitude</label><input id="gLng" type="number" step="any" placeholder="77.2090"></div>
      <div><label>Area radius (m)</label><input id="gRad" type="number" value="8000"></div>
      <div><label>Cell radius (m)</label><input id="gCell" type="number" value="1500"></div>
      <div class="shrink"><button class="primary" id="gBtn">Preview</button></div>
      <div class="shrink"><button id="gHere">Use my location</button></div>
    </div><div id="gOut" style="margin-top:16px"></div></div>`;
  document.getElementById('gHere').onclick = () => navigator.geolocation
    ? navigator.geolocation.getCurrentPosition((pos) => {
      document.getElementById('gLat').value = pos.coords.latitude.toFixed(5);
      document.getElementById('gLng').value = pos.coords.longitude.toFixed(5);
    }, () => toast('Location permission denied', 'err'))
    : toast('Geolocation is not available', 'err');
  document.getElementById('gBtn').onclick = async () => {
    const out = document.getElementById('gOut');
    try {
      const data = await post('/api/discovery/grid-preview', {
        lat: parseFloat(document.getElementById('gLat').value),
        lng: parseFloat(document.getElementById('gLng').value),
        radius_m: parseInt(document.getElementById('gRad').value, 10),
        cell_radius_m: parseInt(document.getElementById('gCell').value, 10),
      });
      const lats = data.cells.map((c) => c.lat), lngs = data.cells.map((c) => c.lng);
      const [minLa, maxLa, minLo, maxLo] = [Math.min(...lats), Math.max(...lats), Math.min(...lngs), Math.max(...lngs)];
      const sx = (v) => 20 + ((v - minLo) / ((maxLo - minLo) || 1)) * 460;
      const sy = (v) => 280 - ((v - minLa) / ((maxLa - minLa) || 1)) * 260;
      out.innerHTML = `<div class="banner info"><b>${data.count}</b> cells (cap ${data.max_cells}).
        Each cell is one provider query, so a sweep costs about ${data.count}× a single search.</div>
        <svg viewBox="0 0 500 300" style="width:100%;max-width:500px;background:var(--bg-elev,#111);border-radius:10px">
        ${data.cells.map((c, i) => `<circle cx="${sx(c.lng)}" cy="${sy(c.lat)}" r="${i ? 14 : 18}"
          fill="rgba(110,140,255,${i ? .16 : .35})" stroke="rgba(110,140,255,.7)"/>`).join('')}</svg>`;
    } catch (error) { out.innerHTML = `<div class="banner err">${esc(error.message)}</div>`; }
  };
}

async function discoverQueries(body) {
  body.innerHTML = `<div class="card"><h3>Prospecting query generator</h3>
    <p class="muted">Ready-made search queries that surface businesses with weak web presence.
      They open in your own browser - nothing is scraped.</p>
    <div class="row"><div><label>Niche</label><input id="qNiche" placeholder="dentist"></div>
      <div><label>City</label><input id="qCity" placeholder="Pune"></div>
      <div class="shrink"><button class="primary" id="qBtn">Generate</button></div></div>
    <div id="qOut" style="margin-top:16px"></div></div>`;
  document.getElementById('qBtn').onclick = async () => {
    const out = document.getElementById('qOut');
    try {
      const data = await api(`/api/discovery/dorks?niche=${encodeURIComponent(
        document.getElementById('qNiche').value)}&city=${encodeURIComponent(document.getElementById('qCity').value)}`);
      out.innerHTML = data.queries.map((q) => `<div class="card" style="margin-bottom:8px">
        <div class="flex between"><b>${esc(q.label)}</b>
          <a class="btn sm" href="${esc(q.url)}" target="_blank" rel="noopener noreferrer">Open</a></div>
        <code style="font-size:12px;word-break:break-all">${esc(q.query)}</code></div>`).join('');
    } catch (error) { out.innerHTML = `<div class="banner err">${esc(error.message)}</div>`; }
  };
}

async function discoverSignals(body) {
  const box = (id, title, desc, inputs, btn) => `<div class="card"><h3>${title}</h3>
    <p class="muted" style="font-size:12px">${desc}</p>
    <div class="row">${inputs}<div class="shrink"><button class="primary" id="${id}Btn">${btn}</button></div></div>
    <div id="${id}Out" style="margin-top:12px"></div></div>`;
  body.innerHTML = `<div class="grid cols-2">
    ${box('tech', 'Tech-stack fingerprint', 'Detect legacy CMS, Flash, old jQuery, missing analytics for a lead\'s site.',
      '<div><label>Lead ID</label><input id="techId" type="number" placeholder="123"></div>', 'Inspect')}
    ${box('dom', 'Domain age & expiry', 'New domains and domains about to expire are both buying moments.',
      '<div><label>Domain</label><input id="domName" placeholder="example.com"></div>', 'Look up')}
    ${box('hire', 'Hiring signal (free)', 'Reads the company\'s own careers page and open job feeds for web/marketing roles.',
      '<div><label>Company</label><input id="hireCo"></div><div><label>Website (recommended)</label><input id="hireWeb" placeholder="example.com"></div>', 'Check')}
    ${box('ads', 'Paid-ads footprint (free)', 'Detects Google/Meta/LinkedIn/TikTok ad pixels on the site, and wasted-spend gaps.',
      '<div><label>Website</label><input id="adsUrl" placeholder="example.com"></div><div><label>or Lead ID</label><input id="adsLead" type="number"></div>', 'Scan')}
  </div>`;
  const run = (id, fn, render) => {
    document.getElementById(`${id}Btn`).onclick = async () => {
      const out = document.getElementById(`${id}Out`);
      out.innerHTML = '<div class="loading">Checking...</div>';
      try { out.innerHTML = render(await fn()); }
      catch (error) { out.innerHTML = `<div class="banner err">${esc(error.message)}</div>`; }
    };
  };
  const list = (items, empty) => items.length ? `<ul class="issues">${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>`
    : `<div class="faint">${empty}</div>`;
  const val = (id) => document.getElementById(id).value.trim();
  run('tech', () => api(`/api/discovery/leads/${val('techId')}/techstack`), (d) => `
    <div class="muted">Modernisation need: <b>${d.modernisation_score}/100</b> ·
      Stack: ${esc(d.stack.map((s) => s.tech).join(', ')) || 'unknown'}</div>${list(d.issues, 'No issues found.')}`);
  run('dom', () => api(`/api/discovery/domain?name=${encodeURIComponent(val('domName'))}`), (d) => d.error
    ? `<div class="banner warn">${esc(d.error)}</div>` : `<dl class="kv">
      <dt>Registered</dt><dd>${date(d.registered)}</dd><dt>Age</dt><dd>${d.age_days ?? '—'} days</dd>
      <dt>Expires</dt><dd>${date(d.expires)} (${d.days_to_expiry ?? '—'} days)</dd></dl>
      ${d.flags.map((f) => `<span class="pill WARM">${esc(f.replace(/_/g, ' '))}</span>`).join(' ')}`);
  run('hire', () => api(`/api/discovery/hiring?company=${encodeURIComponent(val('hireCo'))}&website=${encodeURIComponent(val('hireWeb'))}`),
    (d) => (d.hiring_digital
      ? `<div class="banner ok">Hiring for digital roles</div>${list(d.evidence.map((e) =>
        `${e.source}: ${(e.roles || []).join(', ')}`), '')}`
      : `<div class="faint">No digital roles found (checked ${esc(d.checked.join(', '))}). ${esc(d.note)}</div>`));
  run('ads', () => api(val('adsLead') ? `/api/discovery/ads?lead_id=${encodeURIComponent(val('adsLead'))}`
      : `/api/discovery/ads?url=${encodeURIComponent(val('adsUrl'))}`),
    (d) => (d.advertising
      ? `<div class="banner ok">Advertising on ${esc(d.platforms.map((p) => p.platform).join(', '))}
          <span class="faint">(${esc(d.confidence)} confidence)</span></div>${list(d.wasted_spend_signals, '')}`
      : '<div class="faint">No ad pixels found on this site.</div>'));
}

async function discoverReviews(body) {
  body.innerHTML = `<div class="card" style="max-width:720px"><h3>Review pain miner</h3>
    <p class="muted">Paste customer reviews (one per line). Finds direct evidence like "never answers
      the phone" or "couldn't find their website" - quotable proof for your pitch.</p>
    <textarea id="rvText" rows="8" placeholder="Great food but they never answer the phone&#10;No website, couldn't find the menu"></textarea>
    <div style="margin-top:10px"><button class="primary" id="rvBtn">Analyse</button></div>
    <div id="rvOut" style="margin-top:14px"></div></div>`;
  document.getElementById('rvBtn').onclick = async () => {
    const out = document.getElementById('rvOut');
    const reviews = document.getElementById('rvText').value.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!reviews.length) return toast('Paste at least one review', 'err');
    try {
      const d = await post('/api/discovery/reviews/analyse', { reviews: reviews.slice(0, 500) });
      out.innerHTML = `<div class="banner ${d.pain_score ? 'ok' : 'info'}">Pain score <b>${d.pain_score}/100</b>
        from ${d.reviews_scanned} reviews</div>
        ${Object.entries(d.hits).map(([k, n]) => `<span class="pill WARM">${esc(k.replace(/_/g, ' '))} ×${n}</span>`).join(' ')}
        <ul class="issues" style="margin-top:10px">${d.quotes.map((q) => `<li>“…${esc(q.quote)}…”</li>`).join('')}</ul>`;
    } catch (error) { out.innerHTML = `<div class="banner err">${esc(error.message)}</div>`; }
  };
}

async function discoverLookalikes(body) {
  const d = await api('/api/discovery/lookalikes?limit=25');
  if (!d.lookalikes.length) {
    body.innerHTML = `<div class="banner info">${esc(d.note || 'No lookalikes found yet.')}</div>`;
    return;
  }
  body.innerHTML = `<div class="banner info">Modelled on your ${d.based_on} won customer(s): same niche, city,
    size band and web-presence profile.</div>` + table(
    [{ label: 'Business' }, { label: 'Similarity', num: true }],
    d.lookalikes.map((l) => `<tr onclick="openLead(${l.id})" style="cursor:pointer">
      <td>${esc(l.business_name)}</td><td class="num"><b>${l.similarity}%</b></td></tr>`));
}

// Evidence grade, shown inside the lead drawer's Analysis tab.
async function loadEvidence(leadId) {
  const slot = document.getElementById('evidenceSlot');
  if (!slot) return;
  try {
    const ev = await api(`/api/leads/${leadId}/evidence`);
    if (!document.getElementById('evidenceSlot')) return;
    slot.innerHTML = `
      <h3 style="margin-top:20px">Evidence confidence
        <span class="pill ${ev.grade === 'A' || ev.grade === 'B' ? 'WARM' : 'COLD'}" style="margin-left:6px">
          Grade ${esc(ev.grade)} · ${ev.evidence_score}/100</span></h3>
      <dl class="kv">${Object.entries(ev.dimensions).map(([k, v]) =>
        `<dt>${esc(k)}</dt><dd>${v}/5 <span class="faint" style="font-size:11px">${esc((ev.notes[k] || []).join('; '))}</span></dd>`).join('')}</dl>
      ${ev.gaps.length ? `<div class="banner warn" style="margin-top:10px"><b>Close these gaps before outreach</b>
        <ul class="issues">${ev.gaps.map((g) => `<li>${esc(g)}</li>`).join('')}</ul></div>` : ''}`;
  } catch (_) { slot.innerHTML = ''; }
}


// ------------------------------------------------------- replies & health ---
const INTENT_LABEL = { STOP: 'Asked to stop', AUTO_REPLY: 'Auto-reply', REFERRAL: 'Referred us on',
  NOT_INTERESTED: 'Not interested', NOT_NOW: 'Not now', WANTS_MEETING: 'Wants a call',
  ASKS_PRICE: 'Asking price', INTERESTED: 'Interested', UNCLEAR: 'Unclear - read it yourself' };

function renderVerdict(v, applied) {
  const bad = ['STOP', 'NOT_INTERESTED'].includes(v.intent);
  const ref = v.referral && (v.referral.name || v.referral.emails.length || v.referral.phones.length)
    ? `<div class="muted" style="margin-top:6px">Referral: ${esc([v.referral.name, ...v.referral.emails,
        ...v.referral.phones].filter(Boolean).join(' · '))}</div>` : '';
  const ch = applied ? `<ul class="issues" style="margin-top:6px">
      ${v.changes.status ? `<li>Status → <b>${esc(v.changes.status)}</b></li>` : ''}
      ${v.changes.cancelled_messages ? `<li>${v.changes.cancelled_messages} pending message(s) cancelled</li>` : ''}
      ${v.changes.suppressed.length ? `<li>Suppressed: ${esc(v.changes.suppressed.join(', '))}</li>` : ''}
      ${v.changes.follow_up_date ? `<li>Follow-up set for ${esc(v.changes.follow_up_date)}</li>` : ''}</ul>` : '';
  return `<div class="banner ${bad ? 'warn' : 'info'}"><b>${esc(INTENT_LABEL[v.intent] || v.intent)}</b>
    <span class="faint"> · confidence ${Math.round(v.confidence * 100)}%</span><br>
    Next step: ${esc(v.action.next_step)}${(v.matched || []).length
      ? `<div class="faint" style="font-size:11.5px">matched: “${esc(v.matched.join('”, “'))}”</div>` : ''}
    ${ref}${ch}</div>`;
}

async function previewReply() {
  const text = document.getElementById('replyText').value.trim();
  if (!text) return toast('Paste the reply first', 'err');
  try { document.getElementById('replyOut').innerHTML = renderVerdict(await post('/api/replies/classify', { text }), false); }
  catch (error) { toast(error.message, 'err'); }
}

async function applyReply(leadId) {
  const text = document.getElementById('replyText').value.trim();
  if (!text) return toast('Paste the reply first', 'err');
  try {
    const verdict = await post(`/api/leads/${leadId}/reply`, { text });
    document.getElementById('replyOut').innerHTML = renderVerdict(verdict, true);
    toast('Reply logged');
    refreshBadges();
    drawerLead = await api(`/api/leads/${leadId}`);
  } catch (error) { toast(error.message, 'err'); }
}

async function markLost(leadId) {
  const reasons = ['NOT_INTERESTED', 'NO_BUDGET', 'HAS_VENDOR', 'BAD_TIMING', 'NO_RESPONSE',
    'WRONG_FIT', 'CLOSED_BUSINESS', 'OTHER'];
  const reason = (window.prompt(`Why was this lead lost?\n${reasons.join(', ')}`, 'NOT_INTERESTED') || '').trim().toUpperCase();
  if (!reason) return;
  try {
    await post(`/api/leads/${leadId}/loss`, { reason, note: '' });
    toast('Marked lost');
    drawerLead = await api(`/api/leads/${leadId}`);
    renderDrawer('details');
    refreshBadges();
  } catch (error) { toast(error.message, 'err'); }
}

async function loadPipelineHealth() {
  const slot = document.getElementById('healthSlot');
  if (!slot) return;
  try {
    const h = await api('/api/pipeline/health');
    if (!document.getElementById('healthSlot')) return;
    const s = h.sending;
    const list = (rows, key) => rows.length ? `<ul class="issues">${rows.slice(0, 6).map((r) =>
      `<li><a href="#" onclick="openLead(${r.id});return false">${esc(r.business_name)}</a>
        <span class="faint"> ${ago(r[key])}</span></li>`).join('')}</ul>` : '<div class="faint">None 🎉</div>';
    slot.innerHTML = `<div class="grid cols-3" style="margin-bottom:16px">
      <div class="card"><h3>Waiting on you <span class="pill WARM">${h.awaiting_reply_from_us.length}</span></h3>
        <div class="muted" style="font-size:12px">They replied; you haven't answered.</div>
        ${list(h.awaiting_reply_from_us, 'replied_at')}</div>
      <div class="card"><h3>Gone quiet <span class="pill COLD">${h.stale_contacted.length}</span></h3>
        <div class="muted" style="font-size:12px">Contacted ${h.stale_days}+ days ago, no reply.</div>
        ${list(h.stale_contacted, 'last_contacted_date')}</div>
      <div class="card"><h3>Sending</h3>
        <div class="banner ${s.allowed ? 'ok' : 'warn'}" style="margin:6px 0">${s.allowed
          ? `Open - up to ${s.budget} now` : `Paused: ${esc(s.reason)}`}</div>
        ${s.dry_run ? '<div class="faint" style="font-size:12px">Dry run - nothing is really sent.</div>' : ''}
        ${h.loss_reasons.length ? `<div class="muted" style="font-size:12px;margin-top:6px">Lost because: ${
          esc(h.loss_reasons.map((r) => `${r.reason.replace(/_/g, ' ').toLowerCase()} ×${r.count}`).join(', '))}</div>` : ''}
      </div></div>`;
  } catch (_) { slot.innerHTML = ''; }
}


// ------------------------------------------------- ideal client / intent / api ---
const listField = (id, label, hint) => `<div class="field"><label>${label}
  <span class="faint" style="font-weight:400"> ${hint}</span></label><input id="${id}"></div>`;

async function discoverIcp(body) {
  const { icp } = await api('/api/icp');
  body.innerHTML = `<div class="card" style="max-width:760px">
    <h3>Your ideal client</h3>
    <p class="muted">Tell the app what you sell and who buys it. Every lead is then scored for fit, and
      your "Not a fit" marks teach it. Paste your website and it proposes a profile you can edit.</p>
    <div class="row"><div><label>Your website</label><input id="icpUrl" placeholder="yourstudio.com"
      value="${esc(icp ? icp.url : '')}"></div>
      <div class="shrink"><button id="icpInfer">Read my website</button></div></div>
    <div id="icpNote" style="margin:10px 0"></div>
    ${listField('icpOffer', 'What you sell', '(comma separated, e.g. web design, seo)')}
    ${listField('icpNiche', 'Who buys it', '(business types, e.g. dentist, restaurant)')}
    ${listField('icpCities', 'Cities you serve', '(optional)')}
    ${listField('icpExNiche', 'Never target', '(business types to exclude, e.g. bar, casino)')}
    ${listField('icpExKw', 'Skip names containing', '(optional, e.g. franchise, outlet)')}
    ${listField('icpRoles', 'Decision-maker titles', '(optional, e.g. owner, founder, director)')}
    <div class="field"><label>Minimum Google reviews <span class="faint" style="font-weight:400">(0 = don't care)</span></label>
      <input id="icpRev" type="number" min="0" value="${icp ? icp.min_reviews || 0 : 0}"></div>
    <button class="primary" id="icpSave">Save profile</button></div>`;
  const fill = (d) => {
    document.getElementById('icpOffer').value = (d.offerings || []).join(', ');
    document.getElementById('icpNiche').value = (d.target_niches || []).join(', ');
    document.getElementById('icpCities').value = (d.target_cities || []).join(', ');
    document.getElementById('icpExNiche').value = (d.exclude_niches || []).join(', ');
    document.getElementById('icpExKw').value = (d.exclude_keywords || []).join(', ');
    document.getElementById('icpRoles').value = (d.target_roles || []).join(', ');
  };
  if (icp) fill(icp);
  const split = (id) => document.getElementById(id).value.split(',').map((x) => x.trim()).filter(Boolean);
  document.getElementById('icpInfer').onclick = async () => {
    const note = document.getElementById('icpNote');
    note.innerHTML = '<div class="loading">Reading your site...</div>';
    try {
      const d = await post('/api/icp/infer', { url: document.getElementById('icpUrl').value.trim() });
      fill(d);
      note.innerHTML = `<div class="banner info"><b>${esc(d.summary || 'Profile proposed')}</b>
        <span class="faint"> · confidence ${Math.round(d.confidence * 100)}%</span><br>${esc(d.note)}
        ${d.evidence.length ? `<ul class="issues">${d.evidence.map((e) => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}</div>`;
    } catch (error) { note.innerHTML = `<div class="banner err">${esc(error.message)}</div>`; }
  };
  document.getElementById('icpSave').onclick = async () => {
    try {
      await put('/api/icp', { url: document.getElementById('icpUrl').value.trim(), offerings: split('icpOffer'),
        target_niches: split('icpNiche'), target_cities: split('icpCities'),
        exclude_niches: split('icpExNiche'), exclude_keywords: split('icpExKw'), target_roles: split('icpRoles'),
        min_reviews: parseInt(document.getElementById('icpRev').value, 10) || 0 });
      toast('Ideal-client profile saved', 'ok');
    } catch (error) { toast(error.message, 'err'); }
  };
}

async function discoverIntent(body) {
  const { icp } = await api('/api/icp');
  body.innerHTML = `<div class="card"><h3>Intent feed</h3>
    <p class="muted">Public posts where someone is asking for what you sell (Hacker News and Mastodon, via their open
      APIs - no scraping, no login). Posts from other providers are filtered out. Reply helpfully;
      don't spam.</p>
    <div class="row"><div><label>What you sell (comma separated)</label>
      <input id="inOffer" value="${esc(icp ? (icp.offerings || []).join(', ') : '')}" placeholder="web design, seo"></div>
      <div class="shrink"><label>Days</label><input id="inDays" type="number" value="30" style="width:80px"></div>
      <div class="shrink"><button class="primary" id="inGo">Find requests</button></div></div>
    <div id="inOut" style="margin-top:14px"></div></div>`;
  document.getElementById('inGo').onclick = async () => {
    const out = document.getElementById('inOut');
    out.innerHTML = '<div class="loading">Searching public posts...</div>';
    try {
      const d = await api(`/api/discovery/intent-feed?offer=${encodeURIComponent(document.getElementById('inOffer').value)}&days=${parseInt(document.getElementById('inDays').value, 10) || 30}`);
      window.__intentItems = d.items;
      window.__intentOffer = (d.offers || [])[0] || '';
      out.innerHTML = (d.items.length ? d.items.map((it, i) => `<div class="card" style="margin-bottom:10px">
        <div class="flex between"><b>${esc(it.author || 'unknown')}</b> <span class="faint">· ${esc(it.source)}</span>
          <span><span class="pill WARM">intent ${it.score}</span> <span class="faint">${date(it.created_at)}</span></span></div>
        <p style="margin:8px 0">${esc(it.snippet)}</p>
        <div class="flex"><a class="btn sm" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">Open post</a>
          <button class="sm primary" onclick="intentToLead(${i})">Save as lead</button>
          <span class="faint" style="font-size:11.5px">matched: ${esc(it.signals.join(', '))}</span></div></div>`).join('')
        : `<div class="banner info">Scanned ${d.scanned} public posts - none were real requests for ${esc(d.offers.join(' / '))} in this window.
          Genuine requests are rare on open feeds; try a wider window or another offer.</div>`)
        + (d.errors.length ? `<div class="faint" style="font-size:12px">${esc(d.errors.join(' | '))}</div>` : '');
    } catch (error) { out.innerHTML = `<div class="banner err">${esc(error.message)}</div>`; }
  };
}

async function intentToLead(index) {
  const offer = window.__intentOffer || '';
  const it = (window.__intentItems || [])[index];
  if (!it) return;
  try {
    const r = await post('/api/discovery/intent-feed/lead',
      { author: it.author || 'unknown', url: it.url, snippet: it.snippet, offer, source: it.source });
    toast(r.created ? 'Saved as a lead' : 'Already in your leads', 'ok');
    refreshBadges();
  } catch (error) { toast(error.message, 'err'); }
}

async function discoverApi(body) {
  const d = await api('/api/tokens');
  body.innerHTML = `<div class="card" style="max-width:760px"><h3>Read-only API access</h3>
    <p class="muted">Create a token so Claude, a script or a dashboard can <b>read</b> your leads. A token can
      only make GET requests to lead data - it can't edit, delete, send or manage users. It is shown once.</p>
    <div class="row"><div><label>Token name</label><input id="tkName" placeholder="Claude"></div>
      <div class="shrink"><button class="primary" id="tkMake">Create token</button></div></div>
    <div id="tkNew" style="margin:12px 0"></div>
    ${table([{ label: 'Name' }, { label: 'Token' }, { label: 'Last used' }, { label: '' }],
      d.tokens.filter((t) => !t.revoked).map((t) => `<tr><td>${esc(t.name)}</td>
        <td class="mono">${esc(t.prefix)}…</td><td class="faint">${t.last_used_at ? ago(t.last_used_at) : 'never'}</td>
        <td><button class="sm danger" onclick="revokeToken(${t.id})">Revoke</button></td></tr>`), 'No active tokens.')}
    <p class="faint" style="font-size:12px;margin-top:12px">Example: <code>${esc(d.usage)}</code><br>
      Allowed paths: ${esc(d.allowed.join(', '))}</p></div>`;
  document.getElementById('tkMake').onclick = async () => {
    try {
      const t = await post('/api/tokens', { name: document.getElementById('tkName').value || 'API token' });
      document.getElementById('tkNew').innerHTML = `<div class="banner ok"><b>${esc(t.warning)}</b>
        <div class="mono" style="word-break:break-all;margin-top:6px" id="tkVal">${esc(t.token)}</div>
        <button class="sm" onclick="navigator.clipboard.writeText(document.getElementById('tkVal').textContent);toast('Copied','ok')">Copy</button></div>`;
    } catch (error) { toast(error.message, 'err'); }
  };
}

async function revokeToken(id) {
  try { await del(`/api/tokens/${id}`); toast('Token revoked'); routes['/discover'](); }
  catch (error) { toast(error.message, 'err'); }
}

// Fit + feedback, shown in the lead drawer's Analysis tab.
async function loadFit(leadId) {
  const slot = document.getElementById('fitSlot');
  if (!slot) return;
  try {
    const f = await api(`/api/leads/${leadId}/fit`);
    if (!document.getElementById('fitSlot')) return;
    const shown = f.adjusted_fit ?? f.fit;
    slot.innerHTML = `<h3 style="margin-top:20px">Fit for your ideal client
      ${shown === null ? '<span class="pill COLD" style="margin-left:6px">not set up</span>'
        : `<span class="pill ${shown >= 70 ? 'HOT' : shown >= 45 ? 'WARM' : 'COLD'}" style="margin-left:6px">${shown}/100</span>`}</h3>
      ${f.configured ? `<ul class="issues reasons">${f.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}
        ${f.learned.map((r) => `<li class="faint">learned: ${esc(r)}</li>`).join('')}</ul>`
        : '<div class="banner info">Set up your <a href="#/discover?tab=icp" style="color:inherit">ideal-client profile</a> to see fit.</div>'}
      <div class="flex" style="margin-top:10px">
        <button class="sm" onclick="sendFit(${leadId}, 'GOOD_FIT')">👍 Good fit</button>
        <select id="nfReason" style="max-width:190px"><option value="wrong_industry">Wrong industry</option>
          <option value="too_small">Too small</option><option value="too_big">Too big</option>
          <option value="already_has_solution">Already has a solution</option><option value="no_budget">No budget</option>
          <option value="wrong_location">Wrong location</option><option value="bad_data">Bad data</option>
          <option value="other">Other</option></select>
        <button class="sm danger" onclick="sendFit(${leadId}, 'NOT_A_FIT')">👎 Not a fit</button></div>
      <div class="faint" style="font-size:11.5px;margin-top:6px">Your marks teach the app. "Not a fit" also closes the lead.</div>`;
  } catch (_) { slot.innerHTML = ''; }
}

async function sendFit(leadId, verdict) {
  const reason = document.getElementById('nfReason')?.value || 'other';
  try {
    const r = await post(`/api/leads/${leadId}/feedback`, { verdict, reason, mark_lost: true });
    toast(verdict === 'GOOD_FIT' ? 'Thanks - noted as a good fit' : 'Noted - the app will learn from this', 'ok');
    drawerLead = await api(`/api/leads/${leadId}`);
    renderDrawer('analysis');
    refreshBadges();
  } catch (error) { toast(error.message, 'err'); }
}


async function loadVelocity(leadId) {
  const slot = document.getElementById('velocitySlot');
  if (!slot) return;
  try {
    const v = await api(`/api/leads/${leadId}/review-velocity`);
    if (!document.getElementById('velocitySlot')) return;
    slot.innerHTML = `<h3 style="margin-top:20px">Review growth</h3>` + (v.per_month === null
      ? `<div class="faint">${v.snapshots.length} snapshot(s) so far - growth shows once there are two, a week or more apart.</div>`
      : `<div>${v.rapid ? '<span class="pill HOT">rapid growth</span> ' : ''}<b>${v.per_month}</b> new reviews / month
          <span class="faint">(from ${v.snapshots.length} snapshots)</span></div>`);
  } catch (_) { slot.innerHTML = ''; }
}

async function aiDraft(leadId) {
  const out = document.getElementById('aiOut');
  const offer = document.getElementById('aiOffer').value.trim();
  if (offer.length < 3) return toast('Say what you offer first', 'err');
  out.innerHTML = '<div class="loading">Writing... (a local model can take a minute)</div>';
  try {
    const d = await post(`/api/leads/${leadId}/ai-draft`, { offer, channel: 'email' });
    out.innerHTML = `<div class="banner ${d.problems.length ? 'warn' : 'info'}">${esc(d.note)}
        <span class="faint"> model: ${esc(d.model)}</span>${d.problems.length ? `<br>⚠ ${esc(d.problems.join(', '))}` : ''}</div>
      <label>Subject</label><input id="aiSubj" value="${esc(d.subject || '')}">
      <label style="margin-top:8px">Body</label><textarea id="aiBody" rows="8">${esc(d.body)}</textarea>
      <div class="flex" style="margin-top:8px"><button onclick="navigator.clipboard.writeText(document.getElementById('aiBody').value);toast('Copied','ok')">Copy</button></div>`;
  } catch (error) { out.innerHTML = `<div class="banner warn">${esc(error.message)}</div>`; }
}

// ---------------------------------------------------------------- boot ---
(async function boot() {
  try {
    const [who, providers] = await Promise.all([
      api('/api/auth/me'), api('/api/providers'),
    ]);
    window.__me = who;
    document.getElementById('providerLabel').textContent = `provider: ${providers.active}`;
    configureNav();
  } catch (_) { /* api() already sent 401 users to /login */ }
  refreshBadges();
  navigate();
  setInterval(refreshBadges, 60000);
})();
