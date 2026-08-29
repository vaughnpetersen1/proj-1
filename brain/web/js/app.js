/* Router + shell. */
import { api, h, pill } from './util.js';
import { dashboard, markets, scanner, analyzer } from './views1.js';
import { research, builder, backtester, knowledge } from './views2.js';
import { options, journal, brain, settings } from './views3.js';

const VIEWS = [
  { id: 'dashboard', label: 'Dashboard', fn: dashboard },
  { id: 'markets', label: 'Markets', fn: markets },
  { id: 'scanner', label: 'Scanner', fn: scanner },
  { id: 'analyzer', label: 'Trade Analyzer', fn: analyzer },
  { id: 'research', label: 'Research Lab', fn: research },
  { id: 'builder', label: 'Strategy Builder', fn: builder },
  { id: 'backtester', label: 'Backtester', fn: backtester },
  { id: 'options', label: 'Options Lab', fn: options },
  { id: 'journal', label: 'Trade Journal', fn: journal },
  { id: 'brain', label: 'AI Brain', fn: brain },
  { id: 'knowledge', label: 'Knowledge Base', fn: knowledge },
  { id: 'settings', label: 'Settings', fn: settings },
];

const ctx = {
  state: {},
  go(id, params = {}) {
    const v = VIEWS.find(x => x.id === id) || VIEWS[0];
    location.hash = '#' + v.id + (params.symbol ? '/' + params.symbol : '');
    render(v, params);
  },
  setMeta(origin, provider, asOf) {
    const op = document.getElementById('origin-pill');
    op.replaceChildren(origin
      ? pill(origin === 'SYNTHETIC' ? 'SYNTHETIC DATA' : origin,
             origin === 'SYNTHETIC' ? 'synth' : 'up')
      : document.createTextNode(''));
    // The provider pill would just repeat the origin pill when the generator is
    // the active path, so it is suppressed in that case.
    const showProvider = provider && provider.toLowerCase() !== 'synthetic';
    document.getElementById('provider-pill').replaceChildren(
      showProvider ? pill(provider, 'info') : document.createTextNode(''));
    document.getElementById('asof').textContent = asOf || '';
  },
};

const root = document.getElementById('view');

function render(view, params) {
  document.getElementById('title').textContent = view.label;
  [...document.querySelectorAll('#nav li')].forEach(li =>
    li.classList.toggle('active', li.dataset.id === view.id));
  ctx.setMeta(null, null, null);
  root.replaceChildren(h('div', { class: 'loading' }, 'loading…'));
  Promise.resolve(view.fn(root, ctx, params)).catch(e => {
    root.replaceChildren(h('div', { class: 'err' },
      'View failed: ' + (e && e.message ? e.message : String(e))));
  });
}

function parseHash() {
  const raw = (location.hash || '#dashboard').slice(1);
  const [id, sym] = raw.split('/');
  const v = VIEWS.find(x => x.id === id) || VIEWS[0];
  return [v, sym ? { symbol: sym } : {}];
}

document.getElementById('nav').replaceChildren(...VIEWS.map(v =>
  h('li', { 'data-id': v.id, onclick: () => ctx.go(v.id) }, v.label)));

window.addEventListener('hashchange', () => { const [v, p] = parseHash(); render(v, p); });

api('/api/status').then(s => {
  document.getElementById('navfoot').textContent =
    `${s.active_daily_provider} · ${s.universe_size} symbols · `
    + `${s.counts.experiments} experiments`;
}).catch(() => {
  document.getElementById('navfoot').textContent = 'backend unreachable';
});

const [v0, p0] = parseHash();
render(v0, p0);
