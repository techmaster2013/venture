const providers = {
  duckduckgo: (q) => `https://duckduckgo.com/?q=${encodeURIComponent(q)}`,
  google: (q) => `https://www.google.com/search?q=${encodeURIComponent(q)}`,
  bing: (q) => `https://www.bing.com/search?q=${encodeURIComponent(q)}`,
  brave: (q) => `https://search.brave.com/search?q=${encodeURIComponent(q)}`,
};

const state = {
  provider: localStorage.getItem('venture.provider') || 'duckduckgo',
  newTab: localStorage.getItem('venture.newTab') === 'true',
  theme: localStorage.getItem('venture.theme') || 'system',
};

const searchForm = document.getElementById('searchForm');
const searchInput = document.getElementById('searchInput');
const themeToggle = document.getElementById('themeToggle');
const settingsButton = document.getElementById('settingsButton');
const footerSettings = document.getElementById('footerSettings');
const settingsDialog = document.getElementById('settingsDialog');
const providerSelect = document.getElementById('providerSelect');
const newTabToggle = document.getElementById('newTabToggle');

function applyTheme() {
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const isDark = state.theme === 'dark' || (state.theme === 'system' && prefersDark);
  document.documentElement.dataset.theme = isDark ? 'dark' : 'light';
  themeToggle.textContent = isDark ? '☀' : '◐';
}

function cycleTheme() {
  const active = document.documentElement.dataset.theme;
  state.theme = active === 'dark' ? 'light' : 'dark';
  localStorage.setItem('venture.theme', state.theme);
  applyTheme();
}

function normalizeQuery(value) {
  return value.trim();
}

function looksLikeURL(value) {
  if (/^[a-zA-Z][a-zA-Z\d+.-]*:\/\//.test(value)) return true;
  if (/^(localhost|127\.0\.0\.1)(:\d+)?(\/.*)?$/.test(value)) return true;
  return /^[^\s]+\.[^\s]+(\/.*)?$/.test(value);
}

function destinationFor(value) {
  const query = normalizeQuery(value);
  if (!query) return null;

  if (looksLikeURL(query)) {
    if (/^[a-zA-Z][a-zA-Z\d+.-]*:\/\//.test(query)) return query;
    if (/^(localhost|127\.0\.0\.1)/.test(query)) return `http://${query}`;
    return `https://${query}`;
  }

  return providers[state.provider](query);
}

function runSearch(value) {
  const destination = destinationFor(value);
  if (!destination) return;
  if (state.newTab) {
    window.open(destination, '_blank', 'noopener,noreferrer');
  } else {
    window.location.href = destination;
  }
}

function openSettings() {
  providerSelect.value = state.provider;
  newTabToggle.checked = state.newTab;
  settingsDialog.showModal();
}

searchForm.addEventListener('submit', (event) => {
  event.preventDefault();
  runSearch(searchInput.value);
});

themeToggle.addEventListener('click', cycleTheme);
settingsButton.addEventListener('click', openSettings);
footerSettings.addEventListener('click', (event) => {
  event.preventDefault();
  openSettings();
});

providerSelect.addEventListener('change', () => {
  state.provider = providerSelect.value;
  localStorage.setItem('venture.provider', state.provider);
});

newTabToggle.addEventListener('change', () => {
  state.newTab = newTabToggle.checked;
  localStorage.setItem('venture.newTab', String(state.newTab));
});

window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (state.theme === 'system') applyTheme();
});

applyTheme();
searchInput.focus();
