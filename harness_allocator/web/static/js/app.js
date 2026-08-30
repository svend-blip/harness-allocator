// Harness Allocator UI — renders the allocator's capability manifest.
// House rules: no innerHTML for dynamic content; all user-facing text
// through lbl(key, fallback); event delegation on containers.
const LABELS = {
  'en-US': {
    'app.title': 'Harness Allocator',
    'section.supported': 'Harnesses',
    'section.experimental': 'Experimental',
    'hint.experimental': 'Registered in the adapter surface but gated — not exposed as defaults.',
    'col.harness': 'Harness', 'col.status': 'Status', 'col.capabilities': 'Capabilities',
    'status.available': 'AVAILABLE', 'status.missing': 'MISSING',
    'cap.terminal': 'terminal', 'cap.headless': 'headless', 'cap.interactive': 'interactive',
    'cap.read_only': 'read-only', 'cap.workspace_write': 'workspace', 'cap.full_access': 'full access',
    'cap.persistent_session': 'persistent session', 'cap.session_resume': 'resume',
    'cap.skills': 'skills', 'cap.mcp': 'MCP', 'cap.custom_tools': 'custom tools',
    'cap.repo_task_agent': 'repo-task agent', 'cap.git_aware': 'git-aware', 'cap.patch_output': 'patch output',
    'cap.openai_compatible_endpoint': 'OpenAI-compatible endpoint',
    'error.fetch': 'Could not load harness data.',
    'lbl.lang': 'Language'
  },
  'da-DK': {
    'app.title': 'Harness Allocator',
    'section.supported': 'Harnesses',
    'section.experimental': 'Eksperimentelle',
    'hint.experimental': 'Registreret i adapterfladen men gatet — eksponeres ikke som standard.',
    'col.harness': 'Harness', 'col.status': 'Status', 'col.capabilities': 'Kapabiliteter',
    'status.available': 'TILGÆNGELIG', 'status.missing': 'MANGLER',
    'cap.terminal': 'terminal', 'cap.headless': 'headless', 'cap.interactive': 'interaktiv',
    'cap.read_only': 'read-only', 'cap.workspace_write': 'workspace', 'cap.full_access': 'fuld adgang',
    'cap.persistent_session': 'persistent session', 'cap.session_resume': 'genoptag',
    'cap.skills': 'skills', 'cap.mcp': 'MCP', 'cap.custom_tools': 'egne tools',
    'cap.repo_task_agent': 'repo-task-agent', 'cap.git_aware': 'git-bevidst', 'cap.patch_output': 'patch-output',
    'cap.openai_compatible_endpoint': 'OpenAI-kompatibelt endpoint',
    'error.fetch': 'Kunne ikke hente harness-data.',
    'lbl.lang': 'Sprog'
  }
};
let locale = localStorage.getItem('ha-ui-locale') || 'en-US';

function lbl(key, fallback) {
  const table = LABELS[locale] || {};
  return table[key] || fallback;
}

function applyStaticLabels() {
  document.querySelectorAll('[data-slot]').forEach(el => {
    el.textContent = lbl(el.dataset.slot, el.textContent);
  });
  const dd = document.getElementById('lang-dropdown');
  if (dd) dd.value = locale;
}

// Chip order mirrors the manifest's group order; only true values render.
const CHIP_KEYS = [
  ['execution', ['terminal', 'headless', 'interactive']],
  ['workspace', ['read_only', 'workspace_write', 'full_access']],
  ['sessions', ['persistent_session', 'session_resume']],
  ['extensions', ['skills', 'mcp', 'custom_tools', 'repo_task_agent', 'git_aware', 'patch_output']],
  ['models', ['openai_compatible_endpoint']]
];

function renderRow(h) {
  const tr = document.createElement('tr');

  const nameTd = document.createElement('td');
  const nameDiv = document.createElement('div');
  nameDiv.className = 'hname';
  nameDiv.textContent = h.name;
  const binDiv = document.createElement('div');
  binDiv.className = 'hbin';
  binDiv.textContent = h.resolved_path || h.bin;
  nameTd.append(nameDiv, binDiv);

  const statusTd = document.createElement('td');
  const st = document.createElement('span');
  const ok = h.status === 'AVAILABLE';
  st.className = 'status ' + (ok ? 'available' : 'missing');
  st.textContent = ok ? lbl('status.available', 'AVAILABLE') : lbl('status.missing', 'MISSING');
  statusTd.appendChild(st);

  const capsTd = document.createElement('td');
  const chips = document.createElement('div');
  chips.className = 'chips';
  for (const [group, keys] of CHIP_KEYS) {
    const g = h.capabilities[group] || {};
    for (const key of keys) {
      if (g[key] === true) {
        const c = document.createElement('span');
        c.className = 'chip';
        c.textContent = lbl('cap.' + key, key);
        chips.appendChild(c);
      }
    }
  }
  const mode = (h.capabilities.sessions || {}).mode;
  if (mode) {
    const m = document.createElement('span');
    m.className = 'chip mode';
    m.textContent = 'mode: ' + mode;
    chips.appendChild(m);
  }
  capsTd.appendChild(chips);

  tr.append(nameTd, statusTd, capsTd);
  return tr;
}

function renderTables(data) {
  const fill = (tableId, rows) => {
    const tbody = document.querySelector('#' + tableId + ' tbody');
    tbody.replaceChildren(...rows.map(renderRow));
  };
  fill('supported-table', data.supported);
  fill('experimental-table', data.experimental);
}

let lastData = null;
async function load() {
  try {
    const res = await fetch('/api/harnesses');
    lastData = await res.json();
    renderTables(lastData);
  } catch (err) {
    console.error('[ha-ui] fetch failed:', err);
    const tbody = document.querySelector('#supported-table tbody');
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 3;
    td.textContent = lbl('error.fetch', 'Could not load harness data.');
    tr.appendChild(td);
    tbody.replaceChildren(tr);
  }
}

document.getElementById('lang-dropdown').addEventListener('change', e => {
  locale = e.target.value;
  try { localStorage.setItem('ha-ui-locale', locale); } catch (_) {}
  applyStaticLabels();
  if (lastData) renderTables(lastData);
});

applyStaticLabels();
load();
