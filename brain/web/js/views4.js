/* Views: Market Data control centre, and the SQL screener. */
import { api, h, num, pct, money, signClass, stat, pill, claimBlock, banner, table,
         details, jsonBlock, field, input, select, val, numVal, loading, errorBox }
  from './util.js';
import { barChart } from './charts.js';

const BYTES = b => b >= 1e9 ? (b / 1e9).toFixed(2) + ' GB'
  : b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB' : (b / 1e3).toFixed(0) + ' KB';

/** How fresh is the data, and is it honest about what feed it came from? */
export function freshnessSummary(f) {
  if (!f) return { level: 'unknown', label: 'NO DATA', detail: 'no market database yet' };
  const age = f.last_bar_age_minutes;
  const rt = f.realtime || {};
  const feed = f.primary_feed || (rt.feed || null);
  const synthetic = (f.providers || []).some(p => (p.provider || '').includes('synthetic'));

  let level = 'stale', label = 'STALE';
  if (rt.state === 'live') { level = 'live'; label = 'LIVE'; }
  else if (age !== null && age !== undefined && age < 60 * 24) { level = 'recent'; label = 'EOD'; }
  else if (age !== null && age !== undefined && age < 60 * 24 * 4) { level = 'delayed'; label = 'DELAYED'; }
  if (synthetic) { level = 'synthetic'; label = 'SYNTHETIC'; }

  const bits = [];
  if (f.last_bar_ts) bits.push('last bar ' + f.last_bar_ts.replace('T', ' '));
  if (feed) bits.push('feed ' + feed);
  if (f.symbols_tracked) bits.push(f.symbols_tracked + ' symbols');
  if (rt.state && rt.state !== 'stopped') bits.push('stream ' + rt.state);
  return { level, label, detail: bits.join(' · '), synthetic, feed,
           full_market: rt.full_market_coverage };
}

/* ------------------------------------------------------- DATA CONTROL CENTRE */
export async function dataCenter(root, ctx) {
  root.replaceChildren(loading('reading the market database…'));
  let d;
  try { d = await api('/api/data/status'); }
  catch (e) { return root.replaceChildren(errorBox(e)); }

  const out = h('div', { style: 'margin-top:14px' });
  const busy = (btn, fn) => async () => {
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = 'working…';
    out.replaceChildren(loading('this can take a while — it is fetching and computing, '
      + 'not just reading'));
    try { out.replaceChildren(jsonBlock(await fn())); }
    catch (e) { out.replaceChildren(errorBox(e)); }
    btn.disabled = false; btn.textContent = label;
    ctx.refreshFreshness && ctx.refreshFreshness();
  };
  const action = (label, fn, cls) => {
    const b = h('button', { class: cls || '' }, label);
    b.addEventListener('click', busy(b, fn));
    return b;
  };

  const f = d.freshness || {};
  const s = freshnessSummary(f);
  const db = d.database || {};
  const rt = d.realtime || {};

  const wrap = h('div', {});

  if (s.synthetic) {
    wrap.appendChild(banner('The market database contains bars written by the SYNTHETIC '
      + 'generator. They exercise the whole pipeline — ingestion, validation, features, '
      + 'screener, backtests — but they are not market data. Configure a provider and '
      + 're-run bootstrap to replace them.', 'synth'));
  }
  const alpaca = (d.providers || []).find(p => p.name === 'alpaca');
  if (alpaca && alpaca.available && alpaca.full_market_coverage === false) {
    wrap.appendChild(banner('Alpaca feed is ' + (alpaca.feed_label || 'IEX') + '. '
      + (alpaca.feed_description || ''), ''));
  }

  wrap.appendChild(h('div', { class: 'grid g4' },
    h('div', { class: 'card tight' }, stat('Data status', s.label, s.detail || '',
      s.level === 'live' ? 'up' : s.level === 'stale' ? 'down' : '')),
    h('div', { class: 'card tight' }, stat('Read path', d.active_read_path || '--',
      'engines read this, not a vendor')),
    h('div', { class: 'card tight' }, stat('Daily bars',
      (db.row_counts?.market_data_daily ?? 0).toLocaleString(),
      `${db.active_symbols ?? 0} active symbols`)),
    h('div', { class: 'card tight' }, stat('Database', BYTES(db.size_bytes || 0),
      `${(db.row_counts?.strategy_features ?? 0).toLocaleString()} feature rows`))));

  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Actions'),
    h('div', { class: 'row' },
      action('Sync now (incremental)', () => api('/api/data/sync', { timeframe: '1d' }),
             'primary'),
      action('Bootstrap everything', () => api('/api/data/bootstrap',
        { years: numVal('dc-years', 5), max_symbols: numVal('dc-max', null) })),
      action('Refresh universe', () => api('/api/data/universe', {})),
      action('Recalculate features', () => api('/api/data/features',
        { bars: numVal('dc-bars', null) })),
      action('Refresh regime & sectors', () => api('/api/data/context', {})),
      action('Validate database', () => api('/api/data/validate', { timeframe: '1d' })),
      action('Clear derived tables', () => api('/api/data/clear', { what: 'all' }))),
    h('div', { class: 'row', style: 'margin-top:8px' },
      field('History (years)', input('dc-years', '5', { type: 'number', step: '0.5' })),
      field('Max symbols (blank = all)', input('dc-max', '', { type: 'number' })),
      field('Feature bars (blank = full history)', input('dc-bars', '', { type: 'number' })),
      field('Backfill symbol(s)', input('dc-sym', '', { placeholder: 'NVDA,AAPL' })),
      h('div', { class: 'narrow' }, action('Backfill', () => api('/api/data/backfill', {
        symbols: val('dc-sym') || null, timeframe: val('dc-tf') || '1d',
        start: val('dc-start') || null }))),
      field('Timeframe', select('dc-tf', ['1d', '1h', '15m', '5m', '1m'], '1d')),
      field('From', input('dc-start', '', { placeholder: 'YYYY-MM-DD' }))),
    h('p', { class: 'faint' },
      'Every action is incremental: a backfill asks the provider only for trading days '
      + 'the store is missing, so running it twice costs almost nothing. Clearing derived '
      + 'tables never touches raw bars.')));

  wrap.appendChild(out);

  wrap.appendChild(h('div', { class: 'grid g2', style: 'margin-top:14px' },
    h('div', { class: 'card' }, h('h3', {}, 'Providers'),
      table([
        { label: 'Provider', key: 'name' },
        { label: 'Status', get: p => pill(p.available ? 'available' : 'unavailable',
            p.available ? 'up' : 'down') },
        { label: 'Feed', get: p => p.feed_label || '--' },
        { label: 'Full market', get: p => p.full_market_coverage === undefined ? '--'
            : pill(p.full_market_coverage ? 'consolidated' : 'partial',
                   p.full_market_coverage ? 'up' : 'warn') },
        { label: 'Capabilities', get: p => (p.capabilities || []).join(', ') },
      ], d.providers || []),
      h('p', { class: 'faint' }, d.data_note || ''),
      ...(d.providers || []).filter(p => p.feed_description)
        .map(p => h('p', { class: 'faint' }, `${p.name}: ${p.feed_description}`))),

    h('div', { class: 'card' }, h('h3', {}, 'Real-time stream'),
      h('div', {}, pill(rt.state || 'stopped',
        rt.state === 'live' ? 'up' : rt.state === 'reconnecting' ? 'warn'
        : rt.state === 'disabled' ? 'down' : ''), ' ',
        rt.feed ? pill('feed ' + rt.feed, 'info') : null),
      h('div', { class: 'kv', style: 'margin-top:8px' },
        h('span', { class: 'k' }, 'Messages'), h('span', { class: 'v' }, rt.messages ?? 0),
        h('span', { class: 'k' }, 'Bars written'), h('span', { class: 'v' }, rt.bars_written ?? 0),
        h('span', { class: 'k' }, 'Duplicates dropped'), h('span', { class: 'v' }, rt.duplicates ?? 0),
        h('span', { class: 'k' }, 'Gaps detected'), h('span', { class: 'v' }, rt.gaps ?? 0),
        h('span', { class: 'k' }, 'Reconnects'), h('span', { class: 'v' }, rt.reconnects ?? 0),
        h('span', { class: 'k' }, 'Last message'), h('span', { class: 'v' }, rt.last_message_at || '--'),
        h('span', { class: 'k' }, 'Last error'), h('span', { class: 'v' }, rt.last_error || 'none')),
      rt.feed_note ? h('p', { class: 'warnc' }, rt.feed_note) : null,
      h('div', { class: 'row', style: 'margin-top:8px' },
        field('Symbols', input('dc-rt', '', { placeholder: 'blank = first 30 tracked' })),
        h('div', { class: 'narrow' }, action('Start stream', () => api('/api/data/stream',
          { action: 'start', symbols: val('dc-rt') ? val('dc-rt').split(',') : null }))),
        h('div', { class: 'narrow' }, action('Stop stream', () => api('/api/data/stream',
          { action: 'stop' })))))));

  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Coverage & sync status'),
    table([
      { label: 'Symbol', key: 'symbol' },
      { label: 'TF', key: 'timeframe' },
      { label: 'Provider', key: 'provider' },
      { label: 'Feed', get: r => r.feed || '--' },
      { label: 'Rows', key: 'rows', num: true },
      { label: 'From', key: 'first' },
      { label: 'To', key: 'last' },
      { label: 'Status', get: r => pill(r.status, r.status === 'ok' ? 'up'
          : r.status === 'error' ? 'down' : 'warn') },
      { label: 'Last sync', get: r => (r.last_successful_sync || '').slice(0, 16) },
      { label: 'Error', key: 'error' },
    ], d.sync || [], { scrollY: true }),
    h('p', { class: 'faint' }, 'Rows are per symbol and timeframe. A gap here is why a '
      + 'backtest would refuse to run rather than quietly test a shorter window.')));

  wrap.appendChild(h('div', { class: 'grid g2', style: 'margin-top:14px' },
    h('div', { class: 'card' }, h('h3', {}, 'Data-quality flags'),
      (d.quality_flags || []).length ? table([
        { label: 'When', get: r => (r.ts || '').slice(0, 16) },
        { label: 'Symbol', key: 'symbol' },
        { label: 'Code', key: 'code' },
        { label: 'Severity', get: r => pill(r.severity, r.severity === 'error' ? 'down' : 'warn') },
        { label: 'Detail', key: 'detail' },
      ], d.quality_flags, { scrollY: true })
        : h('div', { class: 'dim' }, 'no unresolved flags'),
      h('p', { class: 'faint' }, 'Suspicious data is flagged, never silently accepted. '
        + 'A blocking error stops the bars being written at all.')),
    h('div', { class: 'card' }, h('h3', {}, 'API usage (24h)'),
      h('div', { class: 'kv' },
        h('span', { class: 'k' }, 'Calls'), h('span', { class: 'v' }, d.api_usage_24h?.calls ?? 0),
        h('span', { class: 'k' }, 'Errors'), h('span', { class: 'v' }, d.api_usage_24h?.errors ?? 0)),
      (d.api_usage_24h?.by_endpoint || []).length
        ? table([{ label: 'Endpoint', key: 'endpoint' },
                 { label: 'Calls', key: 'n', num: true },
                 { label: 'Rows', key: 'rows', num: true },
                 { label: 'Errors', key: 'errors', num: true },
                 { label: 'Avg ms', num: true, get: r => num(r.avg_ms, 0) }],
                d.api_usage_24h.by_endpoint)
        : h('div', { class: 'dim' }, 'no vendor calls in the last 24 hours — the engines '
            + 'have been reading the local store'),
      (d.api_usage_24h?.recent_errors || []).length
        ? details('Recent API errors', () => table([
            { label: 'When', get: r => (r.ts || '').slice(0, 16) },
            { label: 'Endpoint', key: 'endpoint' }, { label: 'Error', key: 'error' }],
            d.api_usage_24h.recent_errors)) : null)));

  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Locked datasets — what makes a backtest reproducible'),
    (d.datasets || []).length ? table([
      { label: 'ID', key: 'id', num: true },
      { label: 'Created', get: r => (r.created_at || '').slice(0, 16) },
      { label: 'Label', key: 'label' },
      { label: 'Provider', key: 'provider' },
      { label: 'Feed', get: r => r.feed || '--' },
      { label: 'Origin', get: r => pill(r.origin, r.origin === 'REAL' ? 'up' : 'synth') },
      { label: 'Symbols', key: 'symbol_count', num: true },
      { label: 'Rows', key: 'row_count', num: true },
      { label: 'Adjustment', key: 'adjustment' },
      { label: 'Checksum', get: r => h('code', { class: 'faint' }, (r.checksum || '').slice(0, 12)) },
    ], d.datasets) : h('div', { class: 'dim' }, 'no datasets locked yet'),
    h('p', { class: 'faint' }, 'A backtest records the dataset id and checksum it ran on. '
      + 'Re-adjusting, backfilling or switching provider produces a different checksum, so '
      + 'an old result stays attributable to the data it actually saw.')));

  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Row counts'),
    barChart(Object.entries(db.row_counts || {})
      .filter(([, v]) => v > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => ({ label: k, value: v, color: k.startsWith('market_data') ? '#2f6d51'
        : k.startsWith('raw_') ? '#3d5a8f' : '#5a4a7a' })),
      { rowH: 20, labelW: 200, decimals: 0 }),
    h('p', { class: 'faint' }, 'Green = raw market data (costs API calls to replace). '
      + 'Blue = raw ticks. Violet = derived, recomputable at any time.'),
    details('Ingestion log', () => table([
      { label: 'When', get: r => (r.ts || '').slice(0, 19) },
      { label: 'Level', get: r => pill(r.level, r.level === 'error' ? 'down'
          : r.level === 'warning' ? 'warn' : 'info') },
      { label: 'Kind', key: 'kind' },
      { label: 'Symbol', key: 'symbol' },
      { label: 'Detail', key: 'detail' },
    ], d.recent_log || []))));

  root.replaceChildren(wrap);
}

/* ------------------------------------------------------------------ SCREENER */
const NUMERIC_FIELDS = [
  ['min_price', 'Min price', '5'], ['max_price', 'Max price', ''],
  ['min_avg_dollar_volume', 'Min 20d $ volume', '5000000'],
  ['min_relative_volume', 'Min relative volume', ''],
  ['min_adr_pct', 'Min ADR %', ''], ['max_adr_pct', 'Max ADR %', ''],
  ['min_rsi', 'Min RSI', ''], ['max_rsi', 'Max RSI', ''],
  ['min_prior_20d_return', 'Min 20d return %', ''],
  ['min_prior_60d_return', 'Min 60d return %', ''],
  ['min_prior_move_pct', 'Min prior move %', '30'],
  ['max_distance_from_20sma', 'Max % above 20 SMA', ''],
  ['min_consolidation_days', 'Min base length', '5'],
  ['max_consolidation_days', 'Max base length', '40'],
  ['max_consolidation_depth_pct', 'Max base depth %', '35'],
  ['max_range_contraction', 'Max range contraction', '1.0'],
  ['max_volume_contraction', 'Max volume contraction', '1.0'],
  ['min_base_quality', 'Min base quality', ''],
  ['max_breakout_distance_pct', 'Max % to breakout', '5'],
  ['min_breakout_volume_ratio', 'Min breakout volume ratio', ''],
  ['min_close_position', 'Min close position (0-1)', '0.5'],
  ['min_relative_strength_pct', 'Min RS percentile', '50'],
  ['min_sector_rank_percentile', 'Min sector percentile', ''],
  ['min_theme_rank_percentile', 'Min theme percentile', ''],
];

const BOOL_FIELDS = [
  ['require_above_10sma', 'Above 10 SMA'], ['require_above_20sma', 'Above 20 SMA'],
  ['require_above_50sma', 'Above 50 SMA'], ['require_above_200sma', 'Above 200 SMA'],
  ['require_sma_stack', '10 > 20 > 50 stack'],
  ['require_base_qualifies', 'Base qualifies'],
  ['require_breakout_today', 'Broke out today'],
];

export async function screener(root, ctx) {
  root.replaceChildren(loading('loading filters…'));
  let meta;
  try { meta = await api('/api/screen/filters'); }
  catch (e) { return root.replaceChildren(errorBox(e)); }
  const out = h('div', { style: 'margin-top:14px' });

  const collect = () => {
    const f = {};
    for (const [key] of NUMERIC_FIELDS) {
      const v = numVal('sc-' + key, null);
      if (v !== null) f[key] = v;
    }
    for (const [key] of BOOL_FIELDS) {
      const v = val('sc-' + key);
      if (v === 'yes') f[key] = true;
      else if (v === 'no') f[key] = false;
    }
    const regimes = val('sc-regimes');
    if (regimes) f.require_market_regimes = regimes.split(',').map(x => x.trim());
    return f;
  };

  const run = async () => {
    out.replaceChildren(loading('screening…'));
    try {
      const r = await api('/api/screen', { filters: collect(), limit: numVal('sc-limit', 100),
                                           order_by: val('sc-order'), strategy: 'ui' });
      out.replaceChildren(renderScreen(r, ctx));
    } catch (e) { out.replaceChildren(errorBox(e)); }
  };

  const applyPreset = () => {
    const p = meta.presets.sar;
    for (const [key] of NUMERIC_FIELDS) {
      const el = document.getElementById('sc-' + key);
      if (el) el.value = p[key] !== undefined ? p[key] : '';
    }
    for (const [key] of BOOL_FIELDS) {
      const el = document.getElementById('sc-' + key);
      if (el) el.value = p[key] === true ? 'yes' : p[key] === false ? 'no' : '';
    }
  };

  root.replaceChildren(h('div', {},
    banner('This screener reads precomputed features from the local database. It makes no '
      + 'vendor API calls and recomputes nothing, so it scans the whole tracked universe in '
      + 'milliseconds. Freshness is bounded by the last feature computation — the Market '
      + 'Data page shows when that was.', 'info'),
    h('div', { class: 'card' },
      h('h3', {}, 'Filters'),
      h('div', { class: 'grid g4' },
        ...NUMERIC_FIELDS.map(([key, label, def]) =>
          field(label, input('sc-' + key, '', { type: 'number', step: 'any',
                                                placeholder: def || 'any',
                                                title: meta.described[key] || '' })))),
      h('div', { class: 'grid g4', style: 'margin-top:6px' },
        ...BOOL_FIELDS.map(([key, label]) =>
          field(label, select('sc-' + key, [{ value: '', label: 'any' },
                                            { value: 'yes', label: 'required' },
                                            { value: 'no', label: 'must not' }], '')))),
      h('div', { class: 'row', style: 'margin-top:8px' },
        field('Market regimes (comma separated)', input('sc-regimes', '',
          { placeholder: 'BULL,BULL_PULLBACK' })),
        field('Order by', select('sc-order', ['base_quality', 'relative_strength_pct',
          'prior_move_pct', 'breakout_distance_pct', 'relative_volume', 'close'],
          'base_quality')),
        field('Limit', input('sc-limit', '100', { type: 'number' })),
        h('div', { class: 'narrow' }, h('button', { class: 'primary', onclick: run }, 'Screen')),
        h('div', { class: 'narrow' }, h('button', {
          onclick: () => { applyPreset(); run(); } }, 'SAR preset')))),
    h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, 'Filters this build cannot offer'),
      table([{ label: 'Filter', get: e => e[0] }, { label: 'What it needs', get: e => e[1] }],
            Object.entries(meta.unavailable)),
      h('p', { class: 'faint' }, 'These are refused explicitly rather than ignored. '
        + 'Requesting one returns the reason instead of quietly dropping it.')),
    out));
  applyPreset();
  await run();
}

function renderScreen(r, ctx) {
  if (!r.ok) return h('div', { class: 'card' }, h('p', {}, r.reason || 'screen failed'));
  const wrap = h('div', {});
  const s = freshnessSummary(r.data_freshness);
  if (s.synthetic) wrap.appendChild(banner('Results computed from SYNTHETIC bars.', 'synth'));
  (r.context_notes || []).forEach(n => wrap.appendChild(banner(n)));
  if (Object.keys(r.unavailable_filters || {}).length) {
    wrap.appendChild(banner('Refused filters: '
      + Object.entries(r.unavailable_filters).map(([k, v]) => `${k} — ${v}`).join(' | ')));
  }

  wrap.appendChild(h('div', { class: 'card' },
    h('h3', {}, `${r.matched} of ${r.symbols_scanned} symbols matched — ${r.seconds}s, `
      + `${r.engine}`),
    h('p', { class: 'faint' },
      `as of ${r.as_of}` + (r.market_regime ? ` · regime ${r.market_regime.label}` : '')
      + ` · ${r.note}`),
    table([
      { label: 'Symbol', key: 'symbol' },
      { label: 'Sector', key: 'sector' },
      { label: 'Close', num: true, get: x => num(x.close, 2) },
      { label: 'Prior move', num: true, get: x => pct(x.prior_move_pct, 1) },
      { label: '20d', num: true, get: x => pct(x.prior_20d_return, 1) },
      { label: 'Base', num: true, get: x => x.consolidation_days ?? '--' },
      { label: 'Depth', num: true, get: x => pct(x.consolidation_depth_pct, 1) },
      { label: 'Range c.', num: true, get: x => num(x.range_contraction, 2) },
      { label: 'Vol c.', num: true, get: x => num(x.volume_contraction, 2) },
      { label: 'Quality', num: true, get: x => num(x.base_quality, 3) },
      { label: 'To breakout', num: true, get: x => pct(x.breakout_distance_pct, 2) },
      { label: 'RVol', num: true, get: x => num(x.relative_volume, 2) },
      { label: 'ADR', num: true, get: x => pct(x.adr20, 2) },
      { label: 'RS', num: true, get: x => num(x.relative_strength_pct, 0) },
      { label: 'Sector %ile', num: true, get: x => num(x.sector_rank_percentile, 0) },
      { label: '$ vol', num: true, get: x => money(x.dollar_volume_20d, 0) },
    ], r.results || [], { onRow: x => ctx.go('analyzer', { symbol: x.symbol }),
                          scrollY: true }),
    details('Exact configuration stored with this run', () => jsonBlock({
      run_id: r.run_id, filters: r.filters, as_of: r.as_of }))));
  return wrap;
}
