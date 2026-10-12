/* Family Board - Kiosk App */
const API = '';
// How often to pull fresh data (bot/email/calendar can add events any time).
const REFRESH_MS = 60000;
// How often to check whether a new frontend version was deployed.
const VERSION_CHECK_MS = 300000;
let state = { weekOffset:0, member:'all', events:[], cash:[], today:'' };
let currentVersion = null;

function api(path, opts={}) {
  return fetch(API+path, {
    ...opts,
    cache:'no-store',
    headers:{'Content-Type':'application/json',...opts.headers},
  }).then(r=>r.json());
}

/* Date helpers */
function getWeekStart(offset) {
  const d = new Date(); d.setDate(d.getDate() + offset*7);
  const dow = d.getDay(); const diff = d.getDate() - dow + (dow===0?-6:1);
  d.setDate(diff); d.setHours(0,0,0,0); return d;
}
function fmtDate(d) {
  // Build from local components; toISOString() would shift by a day for
  // timezones east of UTC at local midnight.
  const m = String(d.getMonth()+1).padStart(2,'0');
  const day = String(d.getDate()).padStart(2,'0');
  return `${d.getFullYear()}-${m}-${day}`;
}
function fmtDisplay(d) {
  const months='Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec'.split(',');
  return `${d.getDate()} ${months[d.getMonth()]}`;
}
function dayName(i) { return 'Sun,Mon,Tue,Wed,Thu,Fri,Sat'.split(',')[i]; }
function shortName(i) { return 'Su,Mo,Tu,We,Th,Fr,Sa'.split(',')[i]; }
/* Map an "HH:MM" time to its period. All-day (no time) events go to Morning. */
function periodOf(t) {
  if (!t) return 'Morning';
  const h = parseInt(t.slice(0,2), 10);
  if (h < 12) return 'Morning';
  if (h < 18) return 'Afternoon';
  return 'Evening';
}
const FALLBACK_MEMBERS = [
  {key:'all', label:'All family', emoji:'👨‍👩‍👧‍👦', color:'#6b7280'},
  {key:'alex', label:'Alex', emoji:'👩', color:'#ec4899'},
  {key:'sam', label:'Sam', emoji:'👨', color:'#3b82f6'},
  {key:'jamie', label:'Jamie', emoji:'👦', color:'#6366f1'},
  {key:'taylor', label:'Taylor', emoji:'👧', color:'#14b8a6'},
  {key:'riley', label:'Riley', emoji:'👦', color:'#f59e0b'},
  {key:'casey', label:'Casey', emoji:'🧑', color:'#a855f7'},
];
let MEMBER_LABELS = { all:'👨‍👩‍👧‍👦 All family' };
let APP_CONFIG = null;
let configSig = '';

function memText(m) { return ((m.emoji||'') + ' ' + (m.label||m.key)).trim(); }
function textOn(hex) {
  const c = (hex||'').replace('#','');
  if (c.length < 6) return '#f9fafb';
  const r=parseInt(c.slice(0,2),16), g=parseInt(c.slice(2,4),16), b=parseInt(c.slice(4,6),16);
  return (0.299*r+0.587*g+0.114*b)/255 > 0.6 ? '#111827' : '#f9fafb';
}
function applyConfig(cfg) {
  cfg = cfg || {};
  const members = Array.isArray(cfg.members) && cfg.members.length ? cfg.members : FALLBACK_MEMBERS;
  APP_CONFIG = Object.assign({}, cfg, { members });
  MEMBER_LABELS = {};
  const styleParts = members.map(m => {
    MEMBER_LABELS[m.key] = memText(m);
    return m.color ? `.fm-${m.key}{background:${m.color};color:${textOn(m.color)}}` : null;
  }).filter(Boolean);

  let tag = document.getElementById('member-styles');
  if (!tag) { tag = document.createElement('style'); tag.id = 'member-styles'; document.head.appendChild(tag); }
  tag.textContent = styleParts.join('\n');

  const nav = document.getElementById('member-tabs');
  if (nav) nav.innerHTML = members.map(m =>
    `<button class="tab${m.key===state.member?' active':''}" data-member="${esc(m.key)}">${esc(memText(m))}</button>`
  ).join('');

  ['event-member-select','cash-member-select'].forEach(id => {
    const sel = document.getElementById(id);
    if (sel) sel.innerHTML = members.map(m =>
      `<option value="${esc(m.key)}">${esc(memText(m))}</option>`).join('');
  });

  const title = cfg.app_title || 'Family Board';
  document.title = title;
  const h1 = document.querySelector('header h1');
  if (h1) h1.textContent = title;
}
async function loadConfig() {
  try {
    const cfg = await api('/api/config');
    const sig = JSON.stringify(cfg);
    if (sig === configSig) return;
    configSig = sig;
    applyConfig(cfg);
  } catch (e) { /* fall back to FALLBACK_MEMBERS */ }
}
function memberLabel(m) { return MEMBER_LABELS[m] || (m ? m : 'All family'); }
const CATEGORY_LABELS = {
  event:'Event', school:'🏫 School', sport:'⚽ Sport',
  medical:'🏥 Medical', field_trip:'🚌 Field trip', lunch:'🍕 Lunch', other:'Other',
};
function fullDate(s) {
  if (!s) return '—';
  const d = new Date(s + 'T00:00:00');
  const months='Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec'.split(',');
  return `${dayName(d.getDay())}, ${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
}
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function hhmm(t) { return t ? t.slice(0,5) : ''; }

/* Load data */
async function loadWeek() {
  loadConfig();
  const start = getWeekStart(state.weekOffset);
  const end = new Date(start); end.setDate(end.getDate()+6);
  const s = fmtDate(start), e = fmtDate(end);
  
  const data = await api(`/api/overview?days=14`);
  state.cash = data.cash_items || [];
  state.today = data.today;
  state.events = [];
  for (const [dt, evts] of Object.entries(data.by_date || {})) {
    state.events.push(...evts);
  }
  renderWeek(start, end);

  renderWeather(data.weather);
  renderShopping(data.shopping);
  renderMessage(data.message);
}

/* Sidebar: weather, shopping list, family message, clock */
function renderWeather(w) {
  const el = document.getElementById('weather-body');
  if (!el) return;
  if (!w || !w.days) {
    el.className = 'side-empty';
    el.textContent = (w && w.error) ? 'unavailable' : '—';
    return;
  }
  el.className = '';
  const days = w.days.slice(0, 4).map((d, i) => {
    const lab = i === 0 ? 'Today' : dayName(new Date(d.date + 'T00:00:00').getDay());
    return `<div class="wday"><span class="wday-name">${lab}</span>` +
           `<span class="wday-icon">${d.icon}</span>` +
           `<span class="wday-temp">${d.max}°<small>/${d.min}°</small></span></div>`;
  }).join('');
  el.innerHTML =
    `<div class="weather-now">
       <span class="weather-icon">${w.icon}</span>
       <div>
         <div class="weather-temp">${w.temp}°</div>
         <div class="weather-desc">${esc(w.label)} · feels ${w.feels}°</div>
         <div class="weather-place">${esc(w.place)}</div>
       </div>
     </div>
     <div class="weather-days">${days}</div>`;
}

function renderShopping(items) {
  const el = document.getElementById('shopping-list');
  if (!el) return;
  if (!items || !items.length) { el.className = 'side-empty'; el.textContent = 'No items'; return; }
  el.className = '';
  el.innerHTML = items.map(i =>
    `<div class="shop-item ${i.checked ? 'checked' : ''}">` +
    `<span class="shop-box">${i.checked ? '☑' : '☐'}</span>${esc(i.text)}</div>`
  ).join('');
}

function renderMessage(msg) {
  const el = document.getElementById('message-body');
  if (!el) return;
  if (!msg) { el.className = 'side-empty'; el.textContent = '—'; return; }
  el.className = '';
  el.textContent = msg;
}

function tickClock() {
  const el = document.getElementById('clock-time');
  const de = document.getElementById('clock-date');
  if (!el) return;
  const now = new Date();
  el.textContent = String(now.getHours()).padStart(2, '0') + ':' +
                   String(now.getMinutes()).padStart(2, '0');
  de.textContent = now.toLocaleDateString('en-US',
    { weekday: 'long', month: 'long', day: 'numeric' });
}

/* Render calendar */
function renderWeek(start, end) {
  const grid = document.getElementById('calendar-grid');
  const label = document.getElementById('week-label');
  label.textContent = `${fmtDisplay(start)} — ${fmtDisplay(end)}`;
  
  const today = state.today;
  const member = state.member;
  const events = state.events;

  let html = '<div class="day-header"><span></span>';
  for (let i=0; i<7; i++) {
    const d = new Date(start); d.setDate(d.getDate()+i);
    const isToday = fmtDate(d)===today;
    html += `<span class="${isToday?'today':''}">${shortName(d.getDay())} ${d.getDate()}</span>`;
  }
  html += '</div>';

  // Time slots: morning, afternoon, evening
  ['Morning','Afternoon','Evening'].forEach((period, pi) => {
    html += `<div class="day-row"><div class="day-label">${period}</div>`;
    for (let i=0; i<7; i++) {
      const d = new Date(start); d.setDate(d.getDate()+i);
      const dateStr = fmtDate(d);
      const isToday = dateStr===today;
      const dayEvts = events.filter(ev => ev.date===dateStr && periodOf(ev.time)===period && (member==='all'||ev.family_member===member||ev.family_member==='all'||!ev.family_member));
      
      html += `<div class="day-cell ${isToday?'today':''}">`;
      dayEvts.slice(0,5).forEach(ev => {
        const fm = ev.family_member||'all';
        const cls = `fm-${fm}`;
        const time = ev.time ? ev.time.slice(0,5) : '';
        html += `<span class="event-chip ${cls}" onclick="showEventDetail(${ev.id})" title="Tap for details">${time} ${esc(ev.title)}</span>`;
      });
      if (dayEvts.length>5) html += `<span style="color:var(--accent);font-size:13px">+${dayEvts.length-5} more</span>`;
      html += '</div>';
    }
    html += '</div>';
  });

  grid.innerHTML = html;
  renderCash();
}

/* Cash items */
function renderCash() {
  const list = document.getElementById('cash-list');
  const summary = document.getElementById('cash-summary');
  const today = new Date();
  const items = state.cash.filter(c=>!c.paid);
  
  const total = items.reduce((s,c)=>s+c.amount, 0);
  summary.textContent = `${items.length} items · $${total.toFixed(2)}`;

  if (!items.length) {
    list.innerHTML = '<div class="empty-state">✅ No pending payments!</div>';
    return;
  }

  let html = '';
  items.sort((a,b)=>(a.due_date||'9999').localeCompare(b.due_date||'9999'));
  items.slice(0,10).forEach(ci => {
    const isOverdue = ci.due_date && ci.due_date < fmtDate(today);
    html += `<div class="cash-item ${isOverdue?'overdue':''}">
      <div>
        <div>💰 <strong>${ci.title}</strong> <span class="amount">$${ci.amount.toFixed(2)}</span></div>
        <div class="due">${ci.family_member} · Due: ${ci.due_date||'—'}${isOverdue?' 🔴 OVERDUE':''}</div>
      </div>
      <div class="cash-actions">
        <button class="paid-btn" onclick="markPaid(${ci.id})">✔ Paid</button>
        <button class="ignore-btn" onclick="ignoreCash(${ci.id})" title="Ignore (never show this again)">✕</button>
      </div>
    </div>`;
  });
  list.innerHTML = html;
}

async function markPaid(id) {
  await api(`/api/cash/${id}/pay`, {method:'POST'});
  loadWeek();
}

async function ignoreCash(id) {
  await api(`/api/cash/${id}/ignore`, {method:'POST'});
  loadWeek();
}

/* Navigation */
function shiftWeek(n) { state.weekOffset += n; loadWeek(); }
function goToday() { state.weekOffset=0; loadWeek(); }

/* Member filter */
document.addEventListener('click', e => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  tab.classList.add('active');
  state.member = tab.dataset.member;
  loadWeek();
});

/* Add panel */
function showAddPanel() { document.getElementById('add-panel').style.display='flex'; }
function closeAddPanel() { document.getElementById('add-panel').style.display='none'; }

/* Event detail popup */
function showEventDetail(id) {
  const ev = state.events.find(e => e.id === id);
  if (!ev) return;
  const fm = ev.family_member || 'all';
  const time = ev.time
    ? hhmm(ev.time) + (ev.end_time ? '–' + hhmm(ev.end_time) : '')
    : 'All day';
  document.getElementById('event-detail-body').innerHTML = `
    <span class="event-chip fm-${fm} detail-tag">${esc(memberLabel(fm))}</span>
    <h2>${esc(ev.title)}</h2>
    <div class="detail-rows">
      <div><span>Date</span><b>${esc(fullDate(ev.date))}</b></div>
      <div><span>Time</span><b>${esc(time)}</b></div>
      <div><span>Type</span><b>${esc(CATEGORY_LABELS[ev.category] || ev.category || 'Event')}</b></div>
    </div>
    ${ev.description ? `<div class="detail-notes"><span>Details</span>${esc(ev.description)}</div>` : ''}
  `;
  document.getElementById('event-detail').style.display = 'flex';
}
function closeEventDetail() {
  document.getElementById('event-detail').style.display = 'none';
}
document.getElementById('event-detail').addEventListener('click', e => {
  if (e.target.id === 'event-detail') closeEventDetail();
});

document.addEventListener('click', e => {
  if (e.target.closest('.form-tab')) {
    const tab = e.target.closest('.form-tab');
    document.querySelectorAll('.form-tab').forEach(t=>t.classList.remove('active'));
    tab.classList.add('active');
    const form = tab.dataset.form;
    document.getElementById('event-form').style.display = form==='event'?'flex':'none';
    document.getElementById('cash-form').style.display = form==='cash'?'flex':'none';
  }
});

document.getElementById('event-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const data = Object.fromEntries(fd);
  await api('/api/events', {method:'POST', body:JSON.stringify(data)});
  closeAddPanel(); loadWeek();
});

document.getElementById('cash-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const data = Object.fromEntries(fd);
  data.amount = parseFloat(data.amount||0);
  await api('/api/cash', {method:'POST', body:JSON.stringify(data)});
  closeAddPanel(); loadWeek();
});

/* Cash toggle */
function toggleCash() {
  const list = document.getElementById('cash-list');
  const icon = document.getElementById('cash-toggle-icon');
  const shown = list.style.display!=='none';
  list.style.display = shown?'none':'block';
  icon.textContent = shown?'▲':'▼';
}

/* Scrape trigger */
async function triggerScrape() {
  const btn = event.target;
  btn.textContent = '⏳'; btn.disabled = true;
  await api('/api/scrape', {method:'POST'});
  loadWeek();
  btn.textContent = '📧'; btn.disabled = false;
}

/* Self-refresh: reload data frequently, and reload the whole page when a new
   frontend version is deployed. */
async function checkVersion() {
  try {
    const { version } = await api('/api/version');
    if (currentVersion === null) {
      currentVersion = version;
    } else if (version !== currentVersion) {
      location.reload();
    }
  } catch (e) { /* offline — try again next tick */ }
}

tickClock();
setInterval(tickClock, 1000);
applyConfig(null);            // render fallback roster immediately
loadConfig();                 // then replace with the real one from /api/config
loadWeek();
checkVersion();
setInterval(() => { if (!document.hidden) loadWeek(); }, REFRESH_MS);
setInterval(checkVersion, VERSION_CHECK_MS);
// Refresh immediately when the tablet screen comes back on.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) { loadWeek(); checkVersion(); }
});
window.addEventListener('focus', loadWeek);

/* Touch/swipe support for week nav */
let touchStart = null;
document.addEventListener('touchstart', e => {
  const t = e.touches[0]||e.target;
  touchStart = {x: t.x||0, time: Date.now()};
});
document.addEventListener('touchend', e => {
  if (!touchStart) return;
  const t = e.changedTouches?.[0]||e.target;
  const dx = (t.x||0) - touchStart.x;
  const dt = Date.now() - touchStart.time;
  if (Math.abs(dx)>50 && dt<600) {
    shiftWeek(dx>0 ? -1 : 1);
  }
  touchStart = null;
});