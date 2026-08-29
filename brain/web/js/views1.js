/* Views: Dashboard, Markets, Scanner, Trade Analyzer. */
import { api, h, num, pct, money, signClass, stat, pill, claimBlock, banner, table,
         details, jsonBlock, field, input, select, val, numVal, loading, errorBox }
  from './util.js';
import { candleChart, lineChart, barChart, histogram, sparkline } from './charts.js';

const REGIME_KIND = { BULL: 'up', BULL_PULLBACK: 'info', SIDEWAYS: 'warn',
                      CORRECTION: 'down', BEAR: 'down', UNKNOWN: '' };

export function originBanner(origin, note) {
  if (origin === 'SYNTHETIC') {
    return banner('SYNTHETIC DATA — the active data path is the built-in generator. Every '
      + 'figure on this page describes a generated market. It exercises the engines; it is '
      + 'not evidence about real markets and must not be traded on. Drop broker CSVs into '
      + 'the cache directory, or set a market-data API key, to change the active data path.',
      'synth');
  }
  return note ? banner(note, 'info') : null;
}

/* ------------------------------------------------------------- DASHBOARD */
export async function dashboard(root, ctx) {
  root.replaceChildren(loading('building dashboard…'));
  let d;
  try { d = await api('/api/dashboard?limit=12'); }
  catch (e) { return root.replaceChildren(errorBox(e)); }
  ctx.setMeta(d.data_origin, d.data_provider, d.as_of);

  const m = d.market;
  const wrap = h('div', {});
  const ob = originBanner(d.data_origin, d.data_note);
  if (ob) wrap.appendChild(ob);

  wrap.appendChild(h('div', { class: 'grid g4' },
    h('div', { class: 'card tight' }, stat('Market', m.label,
      `${m.volatility_label} volatility`, REGIME_KIND[m.label] === 'down' ? 'down' : 'up')),
    h('div', { class: 'card tight' }, stat('Strength', num(m.strength, 0) + '/100',
      m.filter_pass ? 'index MA filter passes' : 'index MA filter FAILS',
      m.filter_pass ? 'up' : 'down')),
    h('div', { class: 'card tight' }, stat('Setups today',
      String(d.scan.candidates_found ?? 0),
      `${d.scan.breakouts_today ?? 0} triggered / ${d.scan.approaching ?? 0} approaching`)),
    h('div', { class: 'card tight' }, stat('Research',
      `${d.research.counts.experiments}`,
      `${d.research.open_hypotheses} open hypotheses`))));

  const breadth = m.breadth || {};
  wrap.appendChild(h('div', { class: 'grid g2', style: 'margin-top:14px' },
    h('div', { class: 'card' }, h('h3', {}, 'Market regime'),
      h('div', { class: 'kv' },
        ...Object.entries(m.indexes || {}).flatMap(([sym, s]) => [
          h('span', { class: 'k' }, sym),
          h('span', { class: 'v' }, `${s.label} · close ${num(s.close)} · 10SMA ${num(s.sma_fast)} `
            + `${s.fast_above_slow ? '>' : '<'} 20SMA ${num(s.sma_slow)} · drawdown ${pct(s.drawdown_pct)}`)]),
        h('span', { class: 'k' }, 'Breadth'),
        h('span', { class: 'v' }, breadth.available
          ? `${pct(breadth.pct_above_50sma)} > 50SMA · ${pct(breadth.pct_above_200sma)} > 200SMA `
            + `· N=${breadth.symbols_counted}`
          : (breadth.reason || 'unavailable'))),
      claimBlock(m.claim)),
    h('div', { class: 'card' }, h('h3', {}, 'Leading sectors & themes'),
      barChart((d.sectors || []).map(s => ({ label: s.name, value: s.score })),
               { rowH: 19, labelW: 150 }),
      h('h3', { style: 'margin-top:12px' }, 'Themes'),
      barChart((d.themes || []).map(s => ({ label: s.name, value: s.score,
                                            color: '#3d5a8f' })),
               { rowH: 19, labelW: 150 }))));

  const rows = d.scan.results || [];
  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, "Today's top setups — click a row for the full analysis"),
    rows.length ? table([
      { label: '#', get: (r) => String(rows.indexOf(r) + 1), num: true },
      { label: 'Symbol', key: 'symbol' },
      { label: 'Score', num: true, get: r => h('span', {},
          h('b', {}, num(r.score, 1)),
          h('div', { class: 'score-bar', style: 'width:60px;margin-top:3px' },
            h('i', { style: `width:${Math.max(0, Math.min(100, r.score))}%` }))) },
      { label: 'State', get: r => pill(r.state, r.state === 'breakout' ? 'up' : 'info') },
      { label: 'Sector', key: 'sector' },
      { label: 'Themes', get: r => (r.themes || []).join(', ') || '--' },
      { label: 'Close', num: true, get: r => num(r.close) },
      { label: 'Trigger', num: true, get: r => num(r.breakout_level) },
      { label: 'To trigger', num: true, get: r => pct(r.pct_to_breakout, 2) },
      { label: 'Stop risk', num: true, get: r => pct(r.risk_pct, 2) },
      { label: 'Prior move', num: true, get: r => pct(r.setup?.prior_move_pct, 1) },
      { label: 'Base', num: true, get: r => r.setup?.base_length ?? '--' },
    ], rows, { onRow: r => ctx.go('analyzer', { symbol: r.symbol }) })
      : h('div', { class: 'dim' }, 'No symbol matched the setup definition today. '
          + 'That is a normal outcome, not an error.'),
    claimBlock(d.scan.claim)));

  wrap.appendChild(h('div', { class: 'grid g2', style: 'margin-top:14px' },
    h('div', { class: 'card' }, h('h3', {}, 'Your trading'),
      h('div', { class: 'kv' },
        h('span', { class: 'k' }, 'Closed trades'), h('span', { class: 'v' }, d.journal.closed),
        h('span', { class: 'k' }, 'Open'), h('span', { class: 'v' }, d.journal.open),
        h('span', { class: 'k' }, 'Net P/L'),
        h('span', { class: 'v ' + signClass(d.journal.net_pnl) }, money(d.journal.net_pnl, 2)),
        h('span', { class: 'k' }, 'Expectancy'),
        h('span', { class: 'v' }, d.journal.expectancy_r === null ? '--'
          : num(d.journal.expectancy_r, 3) + 'R')),
      d.journal.closed < 5 ? h('p', { class: 'faint' },
        'Behavioural analysis needs dozens of trades before any pattern separates from noise.')
        : null),
    h('div', { class: 'card' }, h('h3', {}, 'Recent alerts'),
      (d.alerts || []).length
        ? h('ul', { class: 'bul' }, d.alerts.map(a =>
            h('li', {}, h('span', { class: 'mono faint' }, a.fired_at.slice(0, 16) + ' '),
              pill(a.kind, 'info'), ' ', (a.payload && a.payload.detail) || '')))
        : h('div', { class: 'dim' }, 'No alerts have fired. Create them in the Scanner view.'))));

  root.replaceChildren(wrap);
}

/* --------------------------------------------------------------- MARKETS */
export async function markets(root, ctx, params = {}) {
  const sym = (params.symbol || ctx.state.symbol || 'NVDA').toUpperCase();
  ctx.state.symbol = sym;
  root.replaceChildren(loading('loading ' + sym + '…'));
  let universe = ctx.state.universe;
  if (!universe) {
    try { universe = (await api('/api/providers')).universe; ctx.state.universe = universe; }
    catch { universe = [sym]; }
  }
  let d;
  try { d = await api(`/api/market/${encodeURIComponent(sym)}?bars=320`); }
  catch (e) { return root.replaceChildren(errorBox(e)); }
  if (d.error) return root.replaceChildren(errorBox(d.error));
  ctx.setMeta(d.data_origin, d.provider, d.as_of);

  const bars = d.bars || [];
  const closes = bars.map(b => b.c);
  const sma = (n) => closes.map((_, i) => i < n - 1 ? null
    : closes.slice(i - n + 1, i + 1).reduce((a, b) => a + b, 0) / n);
  const cons = d.consolidation?.consolidation;

  const wrap = h('div', {});
  const ob = originBanner(d.data_origin, d.note);
  if (ob) wrap.appendChild(ob);

  wrap.appendChild(h('div', { class: 'card' }, h('div', { class: 'row' },
    field('Symbol', select('mkt-sym', universe, sym)),
    h('div', { class: 'narrow' },
      h('button', { class: 'primary', onclick: () => ctx.go('markets', { symbol: val('mkt-sym') }) },
        'Load')),
    h('div', { style: 'flex:2' }, h('div', { class: 'kv' },
      h('span', { class: 'k' }, 'Close'), h('span', { class: 'v' }, num(d.close)),
      h('span', { class: 'k' }, 'Change'),
      h('span', { class: 'v ' + signClass(d.change_pct) }, pct(d.change_pct, 2)),
      h('span', { class: 'k' }, 'Volume'), h('span', { class: 'v' }, num(d.volume, 0)))))));

  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, `${sym} — daily, ${bars.length} bars`),
    candleChart(bars, {
      height: 380,
      overlays: [{ values: sma(10), color: '#4da3ff' },
                 { values: sma(20), color: '#ffb648' },
                 { values: sma(50), color: '#7c5cff' }],
      levels: cons ? [{ value: cons.high, label: `base high ${num(cons.high)}`, color: '#35c47a' },
                      { value: cons.low, label: `base low ${num(cons.low)}`, color: '#ff5c6c' }] : [],
    }),
    h('div', { class: 'faint', style: 'margin-top:6px' },
      'blue 10 SMA · amber 20 SMA · violet 50 SMA' + (cons ? ' · green/red the detected base' : ''))));

  const ind = d.indicators || {};
  wrap.appendChild(h('div', { class: 'grid g2', style: 'margin-top:14px' },
    h('div', { class: 'card' }, h('h3', {}, 'Indicators'),
      h('div', { class: 'kv' }, ...Object.entries(ind).flatMap(([k, v]) =>
        [h('span', { class: 'k' }, k), h('span', { class: 'v' }, num(v, 3))]))),
    h('div', { class: 'card' }, h('h3', {}, 'Consolidation detector'),
      cons ? h('div', { class: 'kv' },
        h('span', { class: 'k' }, 'Length'), h('span', { class: 'v' }, cons.length + ' bars'),
        h('span', { class: 'k' }, 'High / Low'),
        h('span', { class: 'v' }, `${num(cons.high)} / ${num(cons.low)}`),
        h('span', { class: 'k' }, 'Depth'), h('span', { class: 'v' }, pct(cons.depth_pct, 2)),
        h('span', { class: 'k' }, 'Range contraction'),
        h('span', { class: 'v' }, num(cons.range_contraction, 3)),
        h('span', { class: 'k' }, 'Volume contraction'),
        h('span', { class: 'v' }, num(cons.volume_contraction, 3)),
        h('span', { class: 'k' }, 'Higher lows'),
        h('span', { class: 'v' }, num(cons.higher_low_ratio, 2)),
        h('span', { class: 'k' }, 'Quality'), h('span', { class: 'v' }, num(cons.quality, 3)),
        h('span', { class: 'k' }, 'Qualifies'),
        h('span', { class: 'v' }, cons.qualifies ? 'yes' : 'no'))
        : h('div', { class: 'dim' }, 'No base ending on the latest bar under the active definition.'),
      details('Definition used', () => jsonBlock(d.consolidation?.definition || {})))));

  const q = d.quality || {};
  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Data quality'),
    h('div', {}, pill(q.ok ? 'no blocking errors' : 'ERRORS PRESENT', q.ok ? 'up' : 'down'),
      ' ', h('span', { class: 'faint' }, `${q.bars} bars`)),
    (q.issues || []).length ? h('ul', { class: 'bul' }, q.issues.map(i =>
      h('li', {}, pill(i.severity, i.severity === 'error' ? 'down'
        : i.severity === 'warning' ? 'warn' : 'info'), ' ',
        h('b', {}, i.code), ' — ', i.detail, i.count > 1 ? ` (${i.count})` : '')))
      : h('div', { class: 'dim' }, 'no issues detected')));

  root.replaceChildren(wrap);
}

/* --------------------------------------------------------------- SCANNER */
export async function scanner(root, ctx) {
  root.replaceChildren(loading('scanning…'));
  let strategies = ctx.state.strategies;
  if (!strategies) {
    strategies = (await api('/api/strategies')).library;
    ctx.state.strategies = strategies;
  }
  const key = ctx.state.scanStrategy || 'sar_v1_1_adr_stop';
  let d;
  try { d = await api(`/api/scan?limit=60&strategy=${key}&pre_breakout=true`); }
  catch (e) { return root.replaceChildren(errorBox(e)); }
  if (!d.ok) return root.replaceChildren(errorBox(d.reason || 'scan failed'));
  ctx.setMeta(d.data_origin, d.data_provider, d.as_of);

  const wrap = h('div', {});
  const ob = originBanner(d.data_origin);
  if (ob) wrap.appendChild(ob);

  wrap.appendChild(h('div', { class: 'card' }, h('div', { class: 'row' },
    field('Strategy definition', select('scan-strat',
      strategies.map(s => ({ value: s.key, label: `${s.name} v${s.version}` })), key)),
    h('div', { class: 'narrow' }, h('button', {
      class: 'primary', onclick: () => {
        ctx.state.scanStrategy = val('scan-strat');
        ctx.go('scanner');
      } }, 'Rescan')),
    h('div', { class: 'narrow' }, h('button', {
      onclick: async (e) => {
        e.target.disabled = true;
        const r = await api('/api/alerts', { kind: 'setup_detected',
          condition: { min_score: 70, strategy_key: key } });
        e.target.disabled = false;
        alert(r.ok ? `Alert created (#${r.id}) for setups scoring 70+.`
                   : (r.reason || 'failed'));
      } }, 'Alert on 70+')),
    h('div', { style: 'flex:2' }, h('div', { class: 'faint' },
      `${d.candidates_found} of ${d.symbols_scanned} symbols matched · `
      + `${d.breakouts_today} triggered · ${d.approaching} approaching`)))));

  const rows = d.results || [];
  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Ranked candidates'),
    table([
      { label: 'Symbol', key: 'symbol' },
      { label: 'Score', num: true, get: r => num(r.score, 1) },
      { label: 'State', get: r => pill(r.state, r.state === 'breakout' ? 'up' : 'info') },
      { label: 'Regime', num: true, get: r => num(r.components.market_regime.score, 0) },
      { label: 'Sector', num: true, get: r => num(r.components.sector_strength.score, 0) },
      { label: 'Theme', num: true, get: r => num(r.components.theme_strength.score, 0) },
      { label: 'RS', num: true, get: r => num(r.components.relative_strength.score, 0) },
      { label: 'Momentum', num: true, get: r => num(r.components.prior_momentum.score, 0) },
      { label: 'Base', num: true, get: r => num(r.components.consolidation_quality.score, 0) },
      { label: 'Volume', num: true, get: r => num(r.components.volume_pattern.score, 0) },
      { label: 'Trigger', num: true, get: r => num(r.components.breakout_quality.score, 0) },
      { label: 'To trigger', num: true, get: r => pct(r.pct_to_breakout, 2) },
      { label: 'Stop risk', num: true, get: r => pct(r.risk_pct, 2) },
    ], rows, { onRow: r => ctx.go('analyzer', { symbol: r.symbol }), scrollY: true }),
    h('div', { class: 'faint', style: 'margin-top:8px' },
      'Columns after Score are the eight weighted components, each 0-100. '
      + 'Weights: ' + Object.entries(d.weights).map(([k, v]) =>
        `${k} ${(v * 100).toFixed(0)}%`).join(' · ')),
    details('How a score is computed (worked example)', () => {
      const r = rows[0];
      if (!r) return h('div', { class: 'dim' }, 'no candidates');
      return h('div', {},
        h('p', {}, `${r.symbol} scored ${num(r.score, 1)}:`),
        table([{ label: 'Component', key: 'k' },
               { label: 'Raw measurement', key: 'raw', num: true },
               { label: 'Sub-score', key: 'score', num: true },
               { label: 'Weight', key: 'w', num: true },
               { label: 'Contribution', key: 'c', num: true },
               { label: 'Meaning', key: 'note' }],
          Object.entries(r.components).map(([k, v]) => ({
            k, raw: num(v.raw, 3), score: num(v.score, 1),
            w: num(v.weight * 100, 0) + '%', c: num(v.contribution, 2), note: v.note }))),
        h('p', { class: 'faint' }, 'The weights are the ones suggested in the build brief. '
          + 'They are configurable and have NOT been shown to be optimal; the Research Lab '
          + 'can test alternative weightings out of sample.'));
    }),
    claimBlock(d.claim)));
  root.replaceChildren(wrap);
}

/* -------------------------------------------------------------- ANALYZER */
export async function analyzer(root, ctx, params = {}) {
  const sym = (params.symbol || ctx.state.symbol || 'NVDA').toUpperCase();
  ctx.state.symbol = sym;
  const form = h('div', { class: 'card' });
  const out = h('div', { style: 'margin-top:14px' }, loading('analysing ' + sym + '…'));
  root.replaceChildren(h('div', {}, form, out));

  let strategies = ctx.state.strategies;
  if (!strategies) { strategies = (await api('/api/strategies')).library;
                     ctx.state.strategies = strategies; }

  const run = async () => {
    out.replaceChildren(loading('analysing…'));
    let d;
    try {
      d = await api('/api/analyze', {
        symbol: val('an-sym') || sym,
        entry: numVal('an-entry'), stop: numVal('an-stop'), target: numVal('an-target'),
        account_equity: numVal('an-eq', 100000), risk_pct: numVal('an-risk', 1),
        strategy_key: val('an-strat'), with_analogs: true });
    } catch (e) { return out.replaceChildren(errorBox(e)); }
    if (!d.ok) return out.replaceChildren(errorBox(d.reason || 'analysis failed'));
    ctx.setMeta(d.data_origin, d.data_provider, d.as_of);
    out.replaceChildren(renderAnalysis(d));
  };

  form.replaceChildren(h('div', { class: 'row' },
    field('Symbol', input('an-sym', sym)),
    field('Entry', input('an-entry', '', { placeholder: 'last close' })),
    field('Stop', input('an-stop', '', { placeholder: 'signal-bar low' })),
    field('Target', input('an-target', '')),
    field('Equity', input('an-eq', '100000')),
    field('Risk %', input('an-risk', '1.0')),
    field('Strategy', select('an-strat',
      strategies.map(s => ({ value: s.key, label: `${s.name} v${s.version}` })),
      'sar_v1_1_adr_stop')),
    h('div', { class: 'narrow' }, h('button', { class: 'primary', onclick: run }, 'Analyze'))));
  await run();
}

function renderAnalysis(d) {
  const v = d.verdict;
  const kind = v.label.startsWith('HIGH') ? 'up' : v.label.startsWith('NO') ? 'down'
    : v.label.startsWith('LOW') ? 'warn' : 'info';
  const wrap = h('div', {});
  const ob = originBanner(d.data_origin);
  if (ob) wrap.appendChild(ob);

  wrap.appendChild(h('div', { class: 'card' },
    h('div', { style: 'display:flex;gap:14px;align-items:center;flex-wrap:wrap' },
      h('h2', { style: 'margin:0;font-size:20px;letter-spacing:0' }, d.symbol),
      pill(v.label, kind),
      h('span', { class: 'mono' }, num(v.score, 1) + '/100'),
      h('span', { class: 'faint' }, d.as_of),
      h('span', { class: 'faint' }, d.strategy)),
    h('p', {}, v.headline),
    details('How the verdict was computed', () =>
      h('ul', { class: 'bul mono' }, v.reasons.map(r => h('li', {}, r))))));

  wrap.appendChild(h('div', { class: 'grid g2', style: 'margin-top:14px' },
    h('div', { class: 'card' }, h('h3', {}, 'Why it qualifies'),
      h('ul', { class: 'bul' }, d.bull_case.map(x => h('li', {}, x)))),
    h('div', { class: 'card' }, h('h3', {}, 'Why it may fail — the bear case'),
      h('ul', { class: 'bul' }, d.bear_case.map(x => h('li', { class: 'warnc' }, x))))));

  wrap.appendChild(h('div', { class: 'grid g3', style: 'margin-top:14px' },
    h('div', { class: 'card' }, h('h3', {}, 'Market'),
      h('div', { class: 'kv' },
        h('span', { class: 'k' }, 'Regime'), h('span', { class: 'v' }, d.market.regime),
        h('span', { class: 'k' }, 'Volatility'), h('span', { class: 'v' }, d.market.volatility),
        h('span', { class: 'k' }, 'Strength'), h('span', { class: 'v' }, num(d.market.strength, 0)),
        h('span', { class: 'k' }, 'MA filter'),
        h('span', { class: 'v ' + (d.market.index_filter_passes ? 'up' : 'down') },
          d.market.index_filter_passes ? 'passes' : 'FAILS')),
      h('p', { class: 'faint' }, d.market.index_filter_detail)),
    h('div', { class: 'card' }, h('h3', {}, 'Sector & theme'),
      h('div', { class: 'kv' },
        h('span', { class: 'k' }, 'Sector'), h('span', { class: 'v' }, d.sector.name || '--'),
        h('span', { class: 'k' }, 'Rank'),
        h('span', { class: 'v' }, `${d.sector.rank ?? '--'} of ${d.sector.of}`),
        h('span', { class: 'k' }, 'Percentile'), h('span', { class: 'v' }, num(d.sector.percentile, 0)),
        h('span', { class: 'k' }, 'Themes'),
        h('span', { class: 'v' }, (d.themes || []).map(t => `${t.name} (#${t.rank})`).join(', ') || 'none')),
      d.sector.leaders ? h('p', { class: 'faint' },
        'Sector leaders: ' + d.sector.leaders.map(l => l.symbol).join(', ')) : null),
    h('div', { class: 'card' }, h('h3', {}, 'Stock'),
      h('div', { class: 'kv' },
        h('span', { class: 'k' }, 'RS percentile'),
        h('span', { class: 'v' }, num(d.stock.relative_strength_percentile, 0)),
        h('span', { class: 'k' }, '1m / 3m / 6m'),
        h('span', { class: 'v' }, `${pct(d.stock.return_1m)} / ${pct(d.stock.return_3m)} / ${pct(d.stock.return_6m)}`),
        h('span', { class: 'k' }, 'ADR'), h('span', { class: 'v' }, pct(d.stock.adr_pct, 2)),
        h('span', { class: 'k' }, 'RVol'), h('span', { class: 'v' }, num(d.stock.rvol, 2)),
        h('span', { class: 'k' }, 'Above 20SMA'),
        h('span', { class: 'v' }, `${pct(d.stock.extension.pct_above_ma, 1)} (${num(d.stock.extension.adr_above_ma, 1)} ADRs)`),
        h('span', { class: 'k' }, 'Orderliness R²'),
        h('span', { class: 'v' }, num(d.stock.orderliness_r2_20, 3))))));

  const s = d.setup;
  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Setup'),
    h('p', {}, s.state, ' — ',
      h('b', { class: s.matches_strategy ? 'up' : 'down' },
        s.matches_strategy ? 'matches the strategy definition' : 'does NOT match')),
    s.failed_checks.length ? h('ul', { class: 'bul' },
      s.failed_checks.map(x => h('li', { class: 'down' }, x))) : null,
    details('Every measurement taken', () => jsonBlock(s.measurements))));

  const an = d.historical_analogs || {};
  if (an.run) {
    const horizons = Object.entries(an.horizons || {});
    wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, 'Historical evidence — comparable setups'),
      h('p', { class: 'faint' }, an.note || ''),
      h('div', {}, pill(an.sample_verdict || '', an.n >= 100 ? 'up' : 'warn')),
      horizons.length ? table([
        { label: 'Horizon', get: r => r[0] + 'd' },
        { label: 'N', num: true, get: r => r[1].n },
        { label: '% positive', num: true, get: r => pct(r[1].pct_positive, 1) },
        { label: '95% CI', num: true, get: r => `[${num(r[1].pct_positive_ci[0], 1)}, ${num(r[1].pct_positive_ci[1], 1)}]` },
        { label: 'Mean', num: true, get: r => pct(r[1].mean_return_pct, 2) },
        { label: 'Median', num: true, get: r => pct(r[1].median_return_pct, 2) },
        { label: 'p05 / p95', num: true, get: r => `${pct(r[1].distribution.p05, 1)} / ${pct(r[1].distribution.p95, 1)}` },
      ], horizons) : h('div', { class: 'dim' }, 'no forward windows available'),
      an.path ? h('p', {}, an.path.statement, ' ',
        h('span', { class: 'faint' }, `95% CI [${num(an.path.pct_hit_target_first_ci[0],1)}, `
          + `${num(an.path.pct_hit_target_first_ci[1],1)}] · median `
          + `${num(an.path.median_bars_to_outcome,0)} bars to resolution`)) : null,
      an.path ? h('p', { class: 'faint' }, an.path.caveat) : null,
      an.excursions?.mfe_r ? h('div', { class: 'grid g2' },
        h('div', {}, h('h3', {}, 'Max favourable excursion (R)'),
          histogram((an.examples || []).map(e => e.mfe_r).filter(x => x !== null),
                    { height: 140, bins: 24 })),
        h('div', {}, h('h3', {}, 'Max adverse excursion (R)'),
          histogram((an.examples || []).map(e => e.mae_r).filter(x => x !== null),
                    { height: 140, bins: 24 }))) : null,
      details('Statistical claims in full', () =>
        h('div', {}, (an.claims || []).map(claimBlock))),
      details('Example historical analogs', () => table([
        { label: 'Symbol', key: 'symbol' }, { label: 'Date', key: 'date' },
        { label: 'Prior move', num: true, get: r => pct(r.snapshot?.prior_move_pct, 1) },
        { label: 'Base', num: true, get: r => r.snapshot?.base_length },
        { label: '10d fwd', num: true, get: r => pct(r.forward_returns?.['10'], 2) },
        { label: 'MFE (R)', num: true, get: r => num(r.mfe_r, 2) },
        { label: 'MAE (R)', num: true, get: r => num(r.mae_r, 2) },
      ], an.examples || []))));
  }

  const plan = d.risk?.plan;
  wrap.appendChild(h('div', { class: 'grid g2', style: 'margin-top:14px' },
    h('div', { class: 'card' }, h('h3', {}, 'Risk & trade plan'),
      plan && plan.ok ? h('div', { class: 'kv' },
        h('span', { class: 'k' }, 'Entry'), h('span', { class: 'v' }, num(d.risk.entry)),
        h('span', { class: 'k' }, 'Stop'),
        h('span', { class: 'v' }, `${num(d.risk.stop)} (${pct(plan.risk_per_share_pct, 2)})`),
        h('span', { class: 'k' }, 'Shares'), h('span', { class: 'v' }, plan.shares),
        h('span', { class: 'k' }, 'Position'),
        h('span', { class: 'v' }, `${money(plan.position_value)} = ${pct(plan.position_pct_of_equity, 1)} of equity`),
        h('span', { class: 'k' }, 'Risk'),
        h('span', { class: 'v' }, `${money(plan.actual_risk_dollars, 2)} (${pct(plan.actual_risk_pct, 2)})`),
        ...(d.risk.risk_reward ? [h('span', { class: 'k' }, 'Reward/risk'),
          h('span', { class: 'v' }, num(d.risk.risk_reward.reward_risk, 2)),
          h('span', { class: 'k' }, 'Breakeven win rate'),
          h('span', { class: 'v' }, pct(d.risk.risk_reward.breakeven_win_rate_pct, 1))] : []))
        : h('div', { class: 'dim' }, plan?.reason || 'no plan computed'),
      (plan?.constraints_applied || []).length ? h('p', { class: 'faint' },
        'Capped by: ' + plan.constraints_applied.join('; ')) : null,
      (plan?.warnings || []).map(w => h('p', { class: 'warnc' }, w))),
    h('div', { class: 'card' }, h('h3', {}, 'Invalidation — what proves this wrong'),
      h('ul', { class: 'bul' }, d.invalidation.map(x => h('li', {}, x))),
      h('h3', { style: 'margin-top:12px' }, 'Evidence quality'),
      h('div', {}, pill(d.evidence_quality.grade,
        d.evidence_quality.grade === 'NONE' ? 'down' : 'warn')),
      h('ul', { class: 'bul faint' }, d.evidence_quality.notes.map(x => h('li', {}, x))))));

  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Provenance'), claimBlock(d.claim),
    details('Raw analysis object', () => jsonBlock(d))));
  return wrap;
}
