const state = {
  newTab: localStorage.getItem('venture.newTab') === 'true',
  theme: localStorage.getItem('venture.theme') || 'system',
};

const $ = (id) => document.getElementById(id);
const homeView = $('homeView');
const resultsView = $('resultsView');
const searchForm = $('searchForm');
const searchInput = $('searchInput');
const resultsForm = $('resultsForm');
const resultsInput = $('resultsInput');
const resultsList = $('resultsList');
const resultStatus = $('resultStatus');
const resultError = $('resultError');
const fallbackLink = $('fallbackLink');
const themeToggle = $('themeToggle');
const settingsDialog = $('settingsDialog');
const newTabToggle = $('newTabToggle');

function applyTheme() {
  const dark = state.theme === 'dark' || (state.theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  themeToggle.textContent = dark ? '☀' : '◐';
}

function cycleTheme() {
  state.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('venture.theme', state.theme);
  applyTheme();
}

function looksLikeURL(value) {
  return /^[a-zA-Z][a-zA-Z\d+.-]*:\/\//.test(value) || /^(localhost|127\.0\.0\.1)(:\d+)?(\/.*)?$/.test(value) || /^[^\s]+\.[^\s]+(\/.*)?$/.test(value);
}

function openDirectURL(value) {
  let url = value;
  if (!/^[a-zA-Z][a-zA-Z\d+.-]*:\/\//.test(url)) url = /^(localhost|127\.0\.0\.1)/.test(url) ? `http://${url}` : `https://${url}`;
  location.href = url;
}

function showResults(query) {
  homeView.hidden = true;
  resultsView.hidden = false;
  resultsInput.value = query;
  document.title = `${query} — Venture`;
}

function resultCard(result) {
  const article = document.createElement('article');
  article.className = 'result-card';
  const link = document.createElement('a');
  link.href = result.url;
  if (state.newTab) { link.target = '_blank'; link.rel = 'noopener noreferrer'; }

  const domain = document.createElement('div');
  domain.className = 'result-domain';
  try { domain.textContent = new URL(result.url).hostname.replace(/^www\./, ''); } catch { domain.textContent = result.url; }
  const title = document.createElement('h2');
  title.textContent = result.title || result.url;
  const snippet = document.createElement('p');
  snippet.textContent = result.description || '';
  link.append(domain, title, snippet);
  article.append(link);
  return article;
}

async function fetchResults(query) {
  showResults(query);
  resultsList.replaceChildren();
  resultError.hidden = true;
  resultStatus.textContent = `Searching for “${query}”…`;
  fallbackLink.href = `https://duckduckgo.com/?q=${encodeURIComponent(query)}`;

  try {
    // DuckDuckGo's Instant Answer API is a public JSON source. It is not a full
    // general-results API, so Venture also extracts useful related topics here.
    const endpoint = `https://api.duckduckgo.com/?q=${encodeURIComponent(query)}&format=json&no_html=1&skip_disambig=1`;
    const response = await fetch(endpoint);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const results = [];

    if (data.AbstractURL && (data.Heading || data.AbstractText)) {
      results.push({ url: data.AbstractURL, title: data.Heading || query, description: data.AbstractText });
    }

    const flatten = (topics) => {
      for (const item of topics || []) {
        if (item.Topics) flatten(item.Topics);
        else if (item.FirstURL && item.Text) {
          const split = item.Text.split(' - ');
          results.push({ url: item.FirstURL, title: split[0], description: split.slice(1).join(' - ') || item.Text });
        }
      }
    };
    flatten(data.RelatedTopics);

    const unique = [...new Map(results.map((item) => [item.url, item])).values()].slice(0, 12);
    if (!unique.length) throw new Error('No usable web results from bridge');
    unique.forEach((result) => resultsList.append(resultCard(result)));
    resultStatus.textContent = `${unique.length} Venture result${unique.length === 1 ? '' : 's'} for “${query}”`;
  } catch (error) {
    console.error(error);
    resultStatus.textContent = `No Venture results available for “${query}”`;
    resultError.hidden = false;
  }
}

function runSearch(raw) {
  const query = raw.trim();
  if (!query) return;
  if (looksLikeURL(query)) return openDirectURL(query);
  const url = new URL(location.href);
  url.search = '';
  url.searchParams.set('q', query);
  history.pushState({ q: query }, '', url);
  fetchResults(query);
}

searchForm.addEventListener('submit', (event) => { event.preventDefault(); runSearch(searchInput.value); });
resultsForm.addEventListener('submit', (event) => { event.preventDefault(); runSearch(resultsInput.value); });
themeToggle.addEventListener('click', cycleTheme);
$('settingsButton').addEventListener('click', () => { newTabToggle.checked = state.newTab; settingsDialog.showModal(); });
$('footerSettings').addEventListener('click', (event) => { event.preventDefault(); newTabToggle.checked = state.newTab; settingsDialog.showModal(); });
newTabToggle.addEventListener('change', () => { state.newTab = newTabToggle.checked; localStorage.setItem('venture.newTab', String(state.newTab)); });
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (state.theme === 'system') applyTheme(); });
window.addEventListener('popstate', () => { const q = new URLSearchParams(location.search).get('q'); if (q) fetchResults(q); else { resultsView.hidden = true; homeView.hidden = false; document.title = 'Venture'; } });

applyTheme();
const initialQuery = new URLSearchParams(location.search).get('q');
if (initialQuery) fetchResults(initialQuery); else searchInput.focus();
