let currentSession = null;
let events = [];
let currentIndex = 0;
let labels = {};
let isPlaying = false;
let playTimer = null;
let totalEventCount = 0;
let debounceTimer = null;
let playbackSpeed = 1500;
let zoomLevel = 1;

const typeColors = { observation: '#58a6ff', action: '#d29922', system_event: '#8b949e' };

document.addEventListener('DOMContentLoaded', () => {
  loadSessionList();
  document.addEventListener('keydown', handleKeydown);
});

// ── Session Management ──────────────────────────────────

function handleKeydown(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') nextEvent();
  if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') prevEvent();
  if (e.key === 'Home') firstEvent();
  if (e.key === 'End') lastEvent();
  if (e.key === ' ') { e.preventDefault(); togglePlay(); }
}

async function loadSessionList() {
  try {
    const res = await fetch('/api/sessions');
    const sessions = await res.json();
    const sel = document.getElementById('session-select');
    const prevVal = sel.value;
    sel.innerHTML = '<option value="">-- Select a session --</option>';
    for (const s of sessions) {
      const opt = document.createElement('option');
      opt.value = s.id;
      const date = new Date(s.start_time * 1000).toLocaleString();
      const dur = s.duration_seconds ? `${s.duration_seconds.toFixed(0)}s` : '?';
      const lbl = s.labels ? ` [${s.labels} labels]` : '';
      opt.textContent = `${s.id.slice(0, 15)}… ${date} (${s.events} events, ${dur})${lbl}`;
      sel.appendChild(opt);
    }
    if (prevVal && [...sel.options].some(o => o.value === prevVal)) sel.value = prevVal;
  } catch (e) {
    console.error('Failed to load sessions', e);
  }
}

async function loadSession(sessionId) {
  if (!sessionId) return;
  currentSession = sessionId;
  currentIndex = 0;
  events = [];
  stopPlay();
  resetZoom();
  await Promise.all([loadEvents(), loadStats(), loadSessionDetail(), loadLabels()]);
  document.getElementById('export-btn').style.display = 'block';
  document.getElementById('export-stats-btn').style.display = 'block';
  document.getElementById('delete-btn').style.display = 'block';
  if (events.length > 0) showEvent(0);
}

async function loadSessionDetail() {
  try {
    const res = await fetch(`/api/sessions/${currentSession}`);
    const s = await res.json();
    const panel = document.getElementById('session-info');
    const date = new Date(s.start_time * 1000).toLocaleString();
    const sizeMB = (s.size_bytes / (1024*1024)).toFixed(1);
    panel.innerHTML = `
      <h3>Session Info</h3>
      <div class="stat"><span>Started</span><span class="val">${date}</span></div>
      <div class="stat"><span>Duration</span><span class="val">${s.duration_seconds.toFixed(1)}s</span></div>
      <div class="stat"><span>Events</span><span class="val">${s.events}</span></div>
      <div class="stat"><span>Screenshots</span><span class="val">${s.screenshots}</span></div>
      <div class="stat"><span>Size</span><span class="val">${sizeMB} MB</span></div>
      <div class="stat"><span>Labels</span><span class="val">${s.labels}</span></div>
    `;
  } catch (e) {
    console.error('Failed to load session detail', e);
  }
}

// ── Events ──────────────────────────────────────────────

async function loadEvents() {
  try {
    const filter = document.getElementById('filter-type').value;
    const search = document.getElementById('search-input').value;
    let url = `/api/sessions/${currentSession}/events?offset=0&limit=10000`;
    if (filter) url += `&filter=${filter}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    const res = await fetch(url);
    const data = await res.json();
    events = data.events;
    totalEventCount = data.total;
    renderTimeline();
    updateCounter();
    document.getElementById('screenshot-container').style.display = events.length > 0 ? 'flex' : 'none';
    document.getElementById('timeline-container').style.display = events.length > 0 ? 'block' : 'none';
    document.getElementById('zoom-controls').style.display = events.length > 0 ? 'flex' : 'none';
    document.getElementById('welcome').style.display = events.length > 0 ? 'none' : 'flex';
  } catch (e) {
    console.error('Failed to load events', e);
  }
}

async function loadStats() {
  try {
    const res = await fetch(`/api/sessions/${currentSession}/stats`);
    const stats = await res.json();
    const panel = document.getElementById('stats-panel');
    let html = '<h4>Event Stats</h4>';
    for (const [k, v] of Object.entries(stats.by_type || {})) {
      html += `<div class="stat-row"><span>${k}</span><span class="cnt">${v}</span></div>`;
    }
    if (stats.top_apps && Object.keys(stats.top_apps).length > 0) {
      html += '<h4 style="margin-top:8px">Top Apps</h4>';
      for (const [k, v] of Object.entries(stats.top_apps)) {
        html += `<div class="stat-row"><span>${k}</span><span class="cnt">${v}</span></div>`;
      }
    }
    if (stats.by_action && Object.keys(stats.by_action).length > 0) {
      html += '<h4 style="margin-top:8px">Actions</h4>';
      for (const [k, v] of Object.entries(stats.by_action)) {
        html += `<div class="stat-row"><span>${k}</span><span class="cnt">${v}</span></div>`;
      }
    }
    panel.innerHTML = html;
  } catch (e) {
    console.error('Failed to load stats', e);
  }
}

// ── Event Navigation ────────────────────────────────────

function showEvent(index) {
  if (index < 0 || index >= events.length) return;
  currentIndex = index;
  const ev = events[index];
  updateCounter();
  renderTimeline();
  showScreenshot(ev);
  showDetails(ev);
  showLabels(ev);
}

function updateCounter() {
  document.getElementById('event-counter').textContent =
    events.length > 0 ? `${currentIndex + 1} / ${events.length} (${totalEventCount} total)` : '0 / 0';
}

function nextEvent() { if (currentIndex < events.length - 1) showEvent(currentIndex + 1); }
function prevEvent() { if (currentIndex > 0) showEvent(currentIndex - 1); }
function firstEvent() { if (events.length > 0) showEvent(0); }
function lastEvent() { if (events.length > 0) showEvent(events.length - 1); }

function gotoEvent(e) {
  if (e.key !== 'Enter') return;
  const input = document.getElementById('goto-input');
  let num = parseInt(input.value);
  if (isNaN(num) || num < 1) num = 1;
  if (num > events.length) num = events.length;
  const idx = num - 1;
  input.value = '';
  showEvent(idx);
}

// ── Screenshot Viewer ───────────────────────────────────

function showScreenshot(ev) {
  const img = document.getElementById('screenshot-img');
  const ssPath = ev.data?.screenshot;
  if (ssPath) {
    img.src = `/api/sessions/${currentSession}/screenshot/${ssPath}`;
    img.style.display = 'block';
    img.onload = () => {
      renderA11yOverlay(ev);
      applyZoom();
    };
    document.getElementById('dl-btn').style.display = 'inline-block';
    document.getElementById('dl-btn').dataset.src = img.src;
  } else {
    img.style.display = 'none';
    document.getElementById('a11y-overlay').innerHTML = '';
    document.getElementById('dl-btn').style.display = 'none';
  }
  document.getElementById('goto-input').value = '';
}

function downloadScreenshot() {
  const src = document.getElementById('dl-btn').dataset.src;
  if (!src) return;
  const a = document.createElement('a');
  a.href = src;
  a.download = src.split('/').pop();
  a.click();
}

// ── Zoom ─────────────────────────────────────────────────

function zoomIn() { zoomLevel = Math.min(zoomLevel * 1.5, 5); applyZoom(); }
function zoomOut() { zoomLevel = Math.max(zoomLevel / 1.5, 0.5); applyZoom(); }
function zoomReset() { zoomLevel = 1; applyZoom(); }

function resetZoom() { zoomLevel = 1; }

function applyZoom() {
  const img = document.getElementById('screenshot-img');
  const wrapper = document.getElementById('screenshot-wrapper');
  if (zoomLevel === 1) {
    img.style.transform = 'none';
    wrapper.classList.remove('zoomed');
  } else {
    img.style.transform = `scale(${zoomLevel})`;
    wrapper.classList.add('zoomed');
  }
  document.getElementById('zoom-level').textContent = `${Math.round(zoomLevel * 100)}%`;
}

// ── A11y Overlay ────────────────────────────────────────

function renderA11yOverlay(ev) {
  const overlay = document.getElementById('a11y-overlay');
  const img = document.getElementById('screenshot-img');
  overlay.innerHTML = '';
  if (!document.getElementById('a11y-toggle').checked) return;
  const tree = ev.data?.accessibility_tree;
  if (!tree || !img.complete || !img.naturalWidth) return;
  const imgRect = img.getBoundingClientRect();
  const natW = img.naturalWidth;
  const natH = img.naturalHeight;
  const scaleX = imgRect.width / natW;
  const scaleY = imgRect.height / natH;
  overlay.style.width = imgRect.width + 'px';
  overlay.style.height = imgRect.height + 'px';
  overlay.style.left = (img.offsetLeft || 0) + 'px';
  overlay.style.top = (img.offsetTop || 0) + 'px';

  const bboxes = [];
  extractBBoxes(tree, bboxes);
  const showBBoxes = document.getElementById('bbox-toggle').checked;
  for (const b of bboxes) {
    if (!b.position || !b.size) continue;
    let { x, y } = b.position;
    let w = b.size.w !== undefined ? b.size.w : (b.size.width || 0);
    let h = b.size.h !== undefined ? b.size.h : (b.size.height || 0);
    x *= scaleX; y *= scaleY; w *= scaleX; h *= scaleY;
    if (w < 4 || h < 4) continue;
    if (x + w > overlay.clientWidth + 2 || y + h > overlay.clientHeight + 2) continue;
    if (!showBBoxes) continue;
    const div = document.createElement('div');
    div.className = 'a11y-bbox';
    div.style.left = x + 'px'; div.style.top = y + 'px';
    div.style.width = w + 'px'; div.style.height = h + 'px';
    const role = b.role?.replace('AX', '') || '';
    const title = b.title || '';
    div.textContent = role + (title ? ': ' + title.slice(0, 30) : '');
    overlay.appendChild(div);
  }
}

function extractBBoxes(node, result) {
  if (!node || typeof node !== 'object') return;
  if (node.position && node.size) result.push(node);
  if (node.children && Array.isArray(node.children)) {
    for (const child of node.children) extractBBoxes(child, result);
  }
}

function toggleOverlay() { if (events[currentIndex]) showScreenshot(events[currentIndex]); }
function toggleBBoxes() { if (events[currentIndex]) showScreenshot(events[currentIndex]); }

// ── Accessibility Tree Panel ────────────────────────────

function renderA11yTree(tree) {
  const container = document.getElementById('a11y-tree-view');
  if (!tree || Object.keys(tree).length === 0) {
    document.getElementById('detail-a11y-tree').style.display = 'none';
    return;
  }
  document.getElementById('detail-a11y-tree').style.display = 'block';
  container.innerHTML = '';
  container.appendChild(buildTreeNode(tree, 0));
}

function buildTreeNode(node, depth) {
  const div = document.createElement('div');
  div.className = 'tree-node';

  const row = document.createElement('div');
  row.className = 'tree-node-row';

  const hasChildren = node.children && Array.isArray(node.children) && node.children.length > 0;
  const toggle = document.createElement('span');
  toggle.className = 'tree-toggle';
  if (hasChildren) {
    toggle.textContent = '▼';
    toggle.onclick = () => {
      const childContainer = div.querySelector('.tree-children');
      if (childContainer) {
        const expanded = childContainer.style.display !== 'none';
        childContainer.style.display = expanded ? 'none' : 'block';
        toggle.textContent = expanded ? '▶' : '▼';
      }
    };
  } else {
    toggle.textContent = '  ';
  }
  row.appendChild(toggle);

  const role = document.createElement('span');
  role.className = 'tree-role';
  role.textContent = (node.role || '?').replace('AX', '');
  row.appendChild(role);

  if (node.title) {
    const title = document.createElement('span');
    title.className = 'tree-title';
    title.textContent = node.title.slice(0, 60);
    row.appendChild(title);
  }

  div.appendChild(row);

  if (hasChildren) {
    const childContainer = document.createElement('div');
    childContainer.className = 'tree-children';
    childContainer.style.display = depth < 2 ? 'block' : 'none';
    for (const child of node.children.slice(0, 50)) {
      childContainer.appendChild(buildTreeNode(child, depth + 1));
    }
    if (node.children.length > 50) {
      const more = document.createElement('div');
      more.className = 'tree-node-row';
      more.style.color = 'var(--text-dim)';
      more.style.fontStyle = 'italic';
      more.textContent = `... ${node.children.length - 50} more`;
      childContainer.appendChild(more);
    }
    div.appendChild(childContainer);
  }

  return div;
}

// ── Event Details ───────────────────────────────────────

function showDetails(ev) {
  document.getElementById('detail-content').style.display = 'block';
  document.getElementById('detail-empty').style.display = 'none';
  document.getElementById('detail-type').innerHTML =
    `<strong style="color:${typeColors[ev.type] || '#fff'}">${ev.type}</strong> ` +
    `seq ${ev.sequence_id}`;
  const ts = new Date(ev.timestamp * 1000).toLocaleString();
  let meta = `<div>Time: ${ts}</div>`;
  if (ev.data?.system_event) meta += `<div>System Event: ${ev.data.system_event}</div>`;
  if (ev.data?.action_type) meta += `<div>Action: ${ev.data.action_type}</div>`;
  if (ev.data?.app_name) meta += `<div>App: ${ev.data.app_name}</div>`;
  if (ev.data?.window_title) meta += `<div>Window: ${ev.data.window_title}</div>`;
  if (ev.data?.display_size) meta += `<div>Display: ${ev.data.display_size.width}×${ev.data.display_size.height}</div>`;
  document.getElementById('detail-meta').innerHTML = meta;

  renderA11yTree(ev.data?.accessibility_tree);

  const dataClone = JSON.parse(JSON.stringify(ev.data));
  delete dataClone.screenshot;
  delete dataClone.accessibility_tree;
  document.getElementById('detail-data').textContent = JSON.stringify(dataClone, null, 2);
}

// ── Labels ───────────────────────────────────────────────

async function loadLabels() {
  try {
    const res = await fetch(`/api/sessions/${currentSession}/labels`);
    labels = await res.json();
  } catch (e) {
    labels = {};
  }
}

function showLabels(ev) {
  const seq = String(ev.sequence_id);
  const lbl = labels[seq] || {};
  document.getElementById('label-tag').value = lbl.tag || '';
  document.getElementById('label-action-class').value = lbl.action_class || '';
  document.getElementById('label-notes').value = lbl.notes || '';
  document.getElementById('label-status').textContent = '';
}

async function saveLabel() {
  const ev = events[currentIndex];
  if (!ev) return;
  const seqId = ev.sequence_id;
  const data = {};
  const tag = document.getElementById('label-tag').value;
  const actionClass = document.getElementById('label-action-class').value;
  const notes = document.getElementById('label-notes').value;
  if (tag) data.tag = tag;
  if (actionClass) data.action_class = actionClass;
  if (notes) data.notes = notes;
  try {
    const res = await fetch(`/api/sessions/${currentSession}/labels/${seqId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const result = await res.json();
    labels[String(seqId)] = result;
    document.getElementById('label-status').textContent = '✓ Label saved';
    renderTimeline();
    loadSessionList();
  } catch (e) {
    document.getElementById('label-status').textContent = '✗ Failed to save';
  }
}

async function clearLabel() {
  const ev = events[currentIndex];
  if (!ev) return;
  const seqId = ev.sequence_id;
  try {
    await fetch(`/api/sessions/${currentSession}/labels/${seqId}`, { method: 'DELETE' });
    delete labels[String(seqId)];
    document.getElementById('label-tag').value = '';
    document.getElementById('label-action-class').value = '';
    document.getElementById('label-notes').value = '';
    document.getElementById('label-status').textContent = '✓ Label cleared';
    renderTimeline();
    loadSessionList();
  } catch (e) {
    document.getElementById('label-status').textContent = '✗ Failed to clear';
  }
}

// ── Timeline ─────────────────────────────────────────────

function renderTimeline() {
  const track = document.getElementById('timeline-track');
  track.innerHTML = '';
  if (events.length === 0) return;
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    const bar = document.createElement('div');
    bar.className = `tl-event ${ev.type === 'observation' ? 'obs' : ev.type === 'action' ? 'act' : 'sys'}`;
    if (i === currentIndex) bar.classList.add('active');
    if (labels[String(ev.sequence_id)]) bar.classList.add('lbl');
    bar.title = `${ev.type} #${ev.sequence_id}`;
    bar.dataset.index = i;
    bar.onclick = (e) => { e.stopPropagation(); showEvent(parseInt(bar.dataset.index)); };
    track.appendChild(bar);
  }
}

function timelineClick(e) {
  const rect = document.getElementById('timeline').getBoundingClientRect();
  const pct = (e.clientX - rect.left) / rect.width;
  const idx = Math.floor(pct * events.length);
  if (idx >= 0 && idx < events.length) showEvent(idx);
}

function timelineHover(e) {
  const rect = document.getElementById('timeline').getBoundingClientRect();
  const pct = (e.clientX - rect.left) / rect.width;
  const idx = Math.floor(pct * events.length);
  const scrubber = document.getElementById('timeline-scrubber');
  if (idx >= 0 && idx < events.length) {
    scrubber.style.display = 'block';
    scrubber.style.left = (pct * 100) + '%';
  } else {
    scrubber.style.display = 'none';
  }
}

// ── Playback ─────────────────────────────────────────────

function togglePlay() {
  const btn = document.getElementById('play-btn');
  if (isPlaying) {
    stopPlay();
  } else {
    isPlaying = true;
    btn.textContent = '⏸';
    playTimer = setInterval(() => {
      if (currentIndex >= events.length - 1) { stopPlay(); return; }
      showEvent(currentIndex + 1);
    }, playbackSpeed);
  }
}

function stopPlay() {
  isPlaying = false;
  document.getElementById('play-btn').textContent = '▶';
  if (playTimer) { clearInterval(playTimer); playTimer = null; }
}

function setPlaybackSpeed() {
  playbackSpeed = parseInt(document.getElementById('speed-select').value);
  if (isPlaying) {
    stopPlay();
    togglePlay();
  }
}

// ── Filter & Search ──────────────────────────────────────

function applyFilter() { if (currentSession) { currentIndex = 0; loadEvents(); } }

function debounceSearch() {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => { if (currentSession) { currentIndex = 0; loadEvents(); } }, 300);
}

// ── Export ───────────────────────────────────────────────

async function exportLabeled() {
  try {
    const res = await fetch(`/api/sessions/${currentSession}/export?fmt=jsonl`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentSession}_labeled.jsonl`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    console.error('Export failed', e);
  }
}

async function exportStats() {
  try {
    const [statsRes, eventsRes] = await Promise.all([
      fetch(`/api/sessions/${currentSession}/stats`),
      fetch(`/api/sessions/${currentSession}/labels`),
    ]);
    const stats = await statsRes.json();
    const allLabels = await eventsRes.json();
    const now = new Date().toISOString().slice(0, 19).replace('T', ' ');
    let report = `CUA Dataset Session Report\n`;
    report += `Generated: ${now}\n`;
    report += `Session: ${currentSession}\n`;
    report += `${'='.repeat(50)}\n\n`;
    report += `Total Events: ${stats.total_events}\n\n`;
    report += `By Type:\n`;
    for (const [k, v] of Object.entries(stats.by_type || {})) {
      report += `  ${k}: ${v}\n`;
    }
    if (stats.by_action && Object.keys(stats.by_action).length > 0) {
      report += `\nBy Action:\n`;
      for (const [k, v] of Object.entries(stats.by_action)) {
        report += `  ${k}: ${v}\n`;
      }
    }
    if (stats.top_apps && Object.keys(stats.top_apps).length > 0) {
      report += `\nTop Apps:\n`;
      for (const [k, v] of Object.entries(stats.top_apps)) {
        report += `  ${k}: ${v}\n`;
      }
    }
    report += `\nLabels: ${Object.keys(allLabels).length}\n`;
    const tagCounts = {};
    for (const lbl of Object.values(allLabels)) {
      if (lbl.tag) tagCounts[lbl.tag] = (tagCounts[lbl.tag] || 0) + 1;
    }
    if (Object.keys(tagCounts).length > 0) {
      report += `\nLabel Tags:\n`;
      for (const [k, v] of Object.entries(tagCounts)) {
        report += `  ${k}: ${v}\n`;
      }
    }
    report += `${'='.repeat(50)}\n`;
    report += `End of Report\n`;
    const blob = new Blob([report], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentSession}_report.txt`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    console.error('Export stats failed', e);
  }
}

// ── Session Delete ───────────────────────────────────────

async function deleteSession() {
  const existing = document.querySelector('.confirm-overlay');
  if (existing) existing.remove();

  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `
    <div class="confirm-box">
      <p><strong>Delete session?</strong></p>
      <p style="font-size:13px;color:var(--text-dim)">This will permanently remove <code>${currentSession}</code> and all its data. This cannot be undone.</p>
      <div>
        <button class="confirm-yes">Delete</button>
        <button class="confirm-no">Cancel</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  overlay.querySelector('.confirm-yes').onclick = async () => {
    try {
      const res = await fetch(`/api/sessions/${currentSession}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Delete failed');
      overlay.remove();
      currentSession = null;
      events = [];
      document.getElementById('session-info').innerHTML = '';
      document.getElementById('stats-panel').innerHTML = '';
      document.getElementById('export-btn').style.display = 'none';
      document.getElementById('export-stats-btn').style.display = 'none';
      document.getElementById('delete-btn').style.display = 'none';
      document.getElementById('screenshot-container').style.display = 'none';
      document.getElementById('timeline-container').style.display = 'none';
      document.getElementById('zoom-controls').style.display = 'none';
      document.getElementById('detail-content').style.display = 'none';
      document.getElementById('detail-empty').style.display = 'block';
      document.getElementById('welcome').style.display = 'flex';
      document.getElementById('session-select').value = '';
      await loadSessionList();
    } catch (e) {
      document.getElementById('label-status').textContent = '✗ Failed to delete';
      overlay.remove();
    }
  };

  overlay.querySelector('.confirm-no').onclick = () => overlay.remove();
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
}
