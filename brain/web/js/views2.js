/* Views: Research Lab, Strategy Builder, Backtester, Knowledge Base. */
import { api, h, num, pct, money, signClass, stat, pill, claimBlock, banner, table,
         details, jsonBlock, field, input, select, val, numVal, loading, errorBox }
  from './util.js';
import { lineChart, barChart, histogram } from './charts.js';
import { originBanner } from './views1.js';

/* ----------------------------------------------------------- RESEARCH LAB */
export async function research(root, ctx) {
  root.replaceChildren(loading('loading research…'));
  let d, feats, conflicts;
  try {
    [d, feats, conflicts] = await Promise.all([
      api('/api/research'), api('/api/research/features'), api('/api/conflicts')]);
  } catch (e) { return root.replaceChildren(errorBox(e)); }

  const wrap = h('div', {});
  const out = h('div', { style: 'margin-top:14px' });

  wrap.appendChild(h('div', { class: 'grid g4' },
    h('div', { class: 'card tight' }, stat('Hypotheses', d.counts.hypotheses,
      `${d.counts.open} open`)),
    h('div', { class: 'card tight' }, stat('Experiments', d.counts.experiments)),
    h('div', { class: 'card tight' }, stat('Supported', d.supported.length, '', 'up')),
    h('div', { class: 'card tight' }, stat('Unsupported / mixed',
      `${d.unsupported.length} / ${d.mixed.length}`, `${d.insufficient.length} insufficient`))));

  const featureOpts = Object.entries(feats.split_features).map(([k, v]) =>
    ({ value: k, label: `${k} — ${v.question}` }));

  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Run an experiment'),
    h('div', { class: 'row' },
      field('Kind', select('rl-kind', [
        { value: 'split', label: 'split — compare two groups of historical setups' },
        { value: 'variants', label: 'variants — compare competing definitions' },
        { value: 'filter', label: 'filter — rule on vs off' }], 'split')),
      field('Split feature', select('rl-feature', featureOpts, 'volume_contraction')),
      field('Concept (variants)', select('rl-concept', [
        'trailing stop', 'strong prior move', 'tight consolidation', 'volume dry-up',
        'high-volume breakout', 'extended', 'orderly price action', 'leading stock',
        'leading sector', 'strong market', 'partial profit taking'], 'trailing stop')),
      field('Horizon (bars)', input('rl-horizon', '10')),
      field('Start', input('rl-start', '2012-01-01')),
      h('div', { class: 'narrow' }, h('button', { class: 'primary', id: 'rl-run',
        onclick: async () => {
          const btn = document.getElementById('rl-run');
          btn.disabled = true;
          out.replaceChildren(loading('running experiment — backtests can take a minute…'));
          try {
            const kind = val('rl-kind');
            const r = await api('/api/research/run', {
              kind, feature: val('rl-feature'), concept: val('rl-concept'),
              horizon: numVal('rl-horizon', 10), start: val('rl-start'),
              max_symbols: kind === 'split' ? null : 40 });
            out.replaceChildren(renderExperiment(r));
          } catch (e) { out.replaceChildren(errorBox(e)); }
          btn.disabled = false;
        } }, 'Run'))),
    h('div', { class: 'faint' },
      'A `split` experiment finds every historical occurrence of the setup and compares the '
      + 'forward outcomes of two groups. `variants` backtests each competing definition of a '
      + 'concept. `filter` runs the same strategy with a rule on and off.')));

  wrap.appendChild(out);

  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Open hypotheses'),
    d.active_hypotheses.length ? table([
      { label: 'ID', key: 'id', num: true },
      { label: 'Question', key: 'question' },
      { label: 'Statement', key: 'statement' },
      { label: 'Category', key: 'category' },
      { label: '', get: r => h('button', { onclick: async (e) => {
          e.stopPropagation();
          out.replaceChildren(loading('running…'));
          try { out.replaceChildren(renderExperiment(
            await api('/api/research/run', { hypothesis_id: r.id }))); }
          catch (err) { out.replaceChildren(errorBox(err)); }
          window.scrollTo({ top: 0, behavior: 'smooth' });
        } }, 'Test') },
    ], d.active_hypotheses) : h('div', { class: 'dim' }, 'none open')));

  const done = d.tested_hypotheses;
  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Completed experiments'),
    done.length ? table([
      { label: 'Question', key: 'question' },
      { label: 'Verdict', get: r => pill(r.verdict || '--',
          r.verdict === 'SUPPORTED' ? 'up' : r.verdict === 'UNSUPPORTED' ? 'down' : 'warn') },
      { label: 'Runs', num: true, get: r => (r.experiments || []).length },
      { label: 'Latest N', num: true, get: r => (r.experiments || [])[0]?.sample_size ?? '--' },
      { label: 'Data', get: r => (r.experiments || [])[0]?.data_origin ?? '--' },
      { label: '', get: r => {
          const e0 = (r.experiments || [])[0];
          return e0 ? h('button', { onclick: async (ev) => {
            ev.stopPropagation();
            out.replaceChildren(loading('loading…'));
            const full = await api(`/api/research/experiments/${e0.id}`);
            out.replaceChildren(renderExperiment(full.results || full));
            window.scrollTo({ top: 0, behavior: 'smooth' });
          } }, 'View') : '--'; } },
    ], done) : h('div', { class: 'dim' }, 'no experiments have run yet')));

  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Knowledge conflicts — sources that disagree'),
    h('p', { class: 'faint' }, conflicts.principle),
    ...conflicts.conflicts.map(c => h('div', { class: 'card', style: 'margin-bottom:10px' },
      h('b', {}, c.topic), ' ', pill(c.status, c.status === 'OPEN' ? 'warn' : 'up'),
      h('div', { class: 'grid g2', style: 'margin-top:8px' },
        h('div', {}, h('b', {}, 'Side A — ' + c.side_a.source),
          h('p', {}, c.side_a.position),
          h('code', { class: 'faint' }, c.side_a.strategy_key)),
        h('div', {}, h('b', {}, 'Side B — ' + c.side_b.source),
          h('p', {}, c.side_b.position),
          h('code', { class: 'faint' }, c.side_b.strategy_key))),
      h('p', { class: 'faint' }, 'Why unresolved: ' + c.why_unresolved),
      h('p', { class: 'faint' }, c.note)))));

  wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    h('h3', {}, 'Reproducibility'), h('p', { class: 'faint' }, d.reproducibility)));
  root.replaceChildren(wrap);
}

function renderExperiment(r) {
  const verdictKind = r.verdict === 'SUPPORTED' ? 'up'
    : r.verdict === 'UNSUPPORTED' ? 'down' : 'warn';
  const wrap = h('div', { class: 'card' },
    h('h3', {}, `Experiment ${r.experiment_id ? '#' + r.experiment_id : ''} — ${r.kind || ''}`),
    h('p', {}, h('b', {}, r.question || r.name || '')),
    h('div', {}, pill(r.verdict || 'n/a', verdictKind), ' ',
      r.sample_verdict ? pill(r.sample_verdict, 'info') : null, ' ',
      r.data_origin === 'SYNTHETIC' ? pill('SYNTHETIC DATA', 'synth') : null),
    h('p', {}, r.conclusion || ''));

  if (r.direction_matches_hypothesis === false) {
    wrap.appendChild(banner('The effect is statistically detectable but points AGAINST the '
      + 'hypothesis. This is evidence against the source claim, not confirmation of it.', ''));
  }
  if (r.groups) {
    wrap.appendChild(table([
      { label: 'Group', key: 'label' },
      { label: 'N', key: 'n', num: true },
      { label: 'Mean fwd %', num: true, get: g => num(g.stats?.mean, 3) },
      { label: 'Median', num: true, get: g => num(g.stats?.median, 3) },
      { label: '% positive', num: true, get: g => pct(g.pct_positive, 2) },
      { label: '95% CI', num: true, get: g => g.pct_positive_ci
          ? `[${num(g.pct_positive_ci[0], 1)}, ${num(g.pct_positive_ci[1], 1)}]` : '--' },
      { label: 'Hit target first', num: true, get: g => pct(g.pct_hit_target_first, 1) },
      { label: 'p05 / p95', num: true, get: g =>
          `${num(g.stats?.p05, 1)} / ${num(g.stats?.p95, 1)}` },
    ], r.groups));
    const t = r.tests || {};
    wrap.appendChild(h('div', { class: 'kv', style: 'margin-top:10px' },
      h('span', { class: 'k' }, 'Permutation p'),
      h('span', { class: 'v' }, num(t.permutation?.p_value, 4)),
      h('span', { class: 'k' }, "Cohen's d"),
      h('span', { class: 'v' }, num(t.permutation?.effect_size, 3)),
      h('span', { class: 'k' }, 'Welch t p'),
      h('span', { class: 'v' }, num(t.welch_t?.p_value, 4)),
      h('span', { class: 'k' }, 'Method'),
      h('span', { class: 'v' }, t.permutation?.method || '')));
  }
  if (r.rows) {
    wrap.appendChild(table([
      { label: 'Definition', key: 'label' },
      { label: 'Trades', key: 'n_trades', num: true },
      { label: 'Win rate', num: true, get: x => pct(x.win_rate_pct, 1) },
      { label: 'Expectancy R', num: true, get: x => num(x.expectancy_r, 3) },
      { label: '95% CI', num: true, get: x => x.expectancy_ci
          ? `[${num(x.expectancy_ci[0], 3)}, ${num(x.expectancy_ci[1], 3)}]` : '--' },
      { label: 'Profit factor', num: true, get: x => num(x.profit_factor, 2) },
      { label: 'Return %', num: true, get: x => num(x.total_return_pct, 1) },
      { label: 'Max DD %', num: true, get: x => num(x.max_drawdown_pct, 1) },
      { label: 'Sample', key: 'sample_verdict' },
    ], r.rows));
  }
  if (r.comparison) {
    wrap.appendChild(h('div', { class: 'kv', style: 'margin-top:10px' },
      h('span', { class: 'k' }, 'Expectancy delta'),
      h('span', { class: 'v ' + signClass(r.comparison.expectancy_delta_r) },
        num(r.comparison.expectancy_delta_r, 3) + 'R'),
      h('span', { class: 'k' }, 'Drawdown delta'),
      h('span', { class: 'v' }, pct(r.comparison.drawdown_delta_pct, 2)),
      h('span', { class: 'k' }, 'Trades removed by the filter'),
      h('span', { class: 'v' }, r.comparison.trades_removed)));
  }
  if (r.ambiguity_note) {
    wrap.appendChild(h('p', { class: 'faint' }, 'Ambiguity preserved: ' + r.ambiguity_note));
  }
  (r.caveats || []).forEach(c => wrap.appendChild(h('p', { class: 'warnc' }, c)));
  if (r.claim) wrap.appendChild(claimBlock(r.claim));
  wrap.appendChild(details('Full experiment record (reproducible)', () => jsonBlock(r)));
  return wrap;
}

/* ------------------------------------------------------- STRATEGY BUILDER */
export async function builder(root, ctx) {
  root.replaceChildren(loading('loading strategies…'));
  const lib = await api('/api/strategies');
  ctx.state.strategies = lib.library;
  const out = h('div', { style: 'margin-top:14px' });
  let spec = (await api('/api/strategies/sar_v1_1_adr_stop')).spec;

  const editors = {};
  const numField = (path, label, step = 'any') => {
    const id = 'sb-' + path.replace(/\./g, '-');
    editors[path] = id;
    return field(label, input(id, String(get(spec, path) ?? ''), { type: 'number', step }));
  };
  const boolField = (path, label) => {
    const id = 'sb-' + path.replace(/\./g, '-');
    editors[path] = id;
    return field(label, select(id, [{ value: 'true', label: 'yes' },
                                    { value: 'false', label: 'no' }],
                               String(!!get(spec, path))));
  };

  function get(o, path) {
    return path.split('.').reduce((a, k) => (a === null || a === undefined ? a : a[k]), o);
  }
  function set(o, path, v) {
    const parts = path.split('.');
    let n = o;
    for (const p of parts.slice(0, -1)) n = n[p];
    n[parts.at(-1)] = v;
  }

  function collect() {
    const copy = JSON.parse(JSON.stringify(spec));
    for (const [path, id] of Object.entries(editors)) {
      const raw = val(id);
      const cur = get(spec, path);
      let v;
      if (typeof cur === 'boolean' || raw === 'true' || raw === 'false') v = raw === 'true';
      else if (raw === '') v = null;
      else if (typeof cur === 'number' || !Number.isNaN(parseFloat(raw))) v = parseFloat(raw);
      else v = raw;
      set(copy, path, v);
    }
    copy.management.trail.length = numVal('sb-trail-len', copy.management.trail.length);
    copy.management.partials = [{
      at_r: numVal('sb-partial-r', 0), fraction: numVal('sb-partial-frac', 0.33),
      after_days: numVal('sb-partial-days', null) }].filter(p => p.fraction > 0);
    return copy;
  }

  const render = () => {
    root.replaceChildren(h('div', {},
      banner('A strategy is data, never code. Nothing here can execute; the backtester '
        + 'interprets this structure. Every threshold is a parameter whose default is a '
        + 'starting point for testing, not a validated value.', 'info'),
      h('div', { class: 'card' }, h('div', { class: 'row' },
        field('Start from', select('sb-base',
          lib.library.map(s => ({ value: s.key, label: `${s.name} v${s.version}` })),
          'sar_v1_1_adr_stop')),
        h('div', { class: 'narrow' }, h('button', { onclick: async () => {
          spec = (await api('/api/strategies/' + val('sb-base'))).spec; render();
        } }, 'Load')))),
      h('div', { class: 'grid g3', style: 'margin-top:14px' },
        h('div', { class: 'card' }, h('h3', {}, 'Prior move'),
          numField('setup.prior_move_min_pct', 'Minimum advance %'),
          numField('setup.prior_move_lookback', 'Lookback bars'),
          numField('setup.prior_move_max_bars', 'Advance completes within (bars)'),
          numField('setup.prior_move_min_bars', 'Minimum bars (excludes one-day pops)')),
        h('div', { class: 'card' }, h('h3', {}, 'Consolidation'),
          numField('setup.cons_min_len', 'Min length (bars)'),
          numField('setup.cons_max_len', 'Max length (bars)'),
          numField('setup.cons_max_depth_pct', 'Max depth %'),
          numField('setup.cons_max_range_ratio', 'Max range ratio (2nd/1st half)'),
          numField('setup.cons_max_volume_ratio', 'Max volume ratio (2nd/1st half)'),
          numField('setup.cons_ma_len', 'Support MA length'),
          numField('setup.cons_min_close_above_ma_frac', 'Min fraction of closes above MA')),
        h('div', { class: 'card' }, h('h3', {}, 'Entry & trigger'),
          numField('entry.breakout_min_rvol', 'Min relative volume'),
          numField('entry.breakout_min_close_position', 'Min close position in bar (0-1)'),
          numField('entry.breakout_buffer_pct', 'Buffer above base high %'),
          boolField('entry.breakout_require_gt_prev_volume', 'Volume > prior day'),
          field('Fill model', select('sb-fill',
            ['next_open', 'close', 'stop_at_level'], spec.entry.fill))),
        h('div', { class: 'card' }, h('h3', {}, 'Market & sector filters'),
          boolField('market_filter.enabled', 'Index MA filter on'),
          numField('market_filter.fast', 'Fast MA'),
          numField('market_filter.slow', 'Slow MA'),
          field('Mode', select('sb-mode', ['both', 'either'], spec.market_filter.mode)),
          boolField('sector_filter.enabled', 'Sector filter on'),
          numField('sector_filter.min_rank_percentile', 'Min sector percentile')),
        h('div', { class: 'card' }, h('h3', {}, 'Risk & stop'),
          field('Stop type', select('sb-stoptype',
            ['low_of_day', 'low_of_base', 'pct', 'atr', 'adr'], spec.stop.type)),
          numField('stop.value', 'Stop value (for pct/atr/adr)'),
          numField('stop.max_stop_adr_multiple', 'Max stop in ADRs (blank = off)'),
          numField('stop.max_stop_pct', 'Max stop % (blank = off)'),
          numField('risk.risk_pct', 'Risk per trade %'),
          numField('risk.max_positions', 'Max concurrent positions'),
          numField('risk.max_position_pct', 'Max position % of equity'),
          numField('risk.account_equity', 'Starting equity')),
        h('div', { class: 'card' }, h('h3', {}, 'Management'),
          field('Partial at R', input('sb-partial-r',
            String(spec.management.partials[0]?.at_r ?? 0), { type: 'number', step: 'any' })),
          field('Partial fraction', input('sb-partial-frac',
            String(spec.management.partials[0]?.fraction ?? 0.33), { type: 'number', step: 'any' })),
          field('…or after N days', input('sb-partial-days',
            String(spec.management.partials[0]?.after_days ?? ''), { type: 'number' })),
          numField('management.breakeven_after_r', 'Breakeven after R'),
          field('Trail type', select('sb-trailtype', ['none', 'sma', 'ema', 'chandelier'],
                                     spec.management.trail.type)),
          field('Trail length', input('sb-trail-len',
            String(spec.management.trail.length), { type: 'number' })),
          numField('management.max_hold_days', 'Max hold (bars)')),
        h('div', { class: 'card' }, h('h3', {}, 'Universe & costs'),
          numField('universe.min_price', 'Min price'),
          numField('universe.min_dollar_volume', 'Min 20d dollar volume'),
          numField('universe.min_adr_pct', 'Min ADR %'),
          numField('setup.max_pct_above_sma20', 'Max % above 20 SMA (blank = off)'),
          numField('setup.min_r2_20', 'Min orderliness R² (blank = off)'),
          numField('setup.min_rs_percentile', 'Min RS percentile (blank = off)'),
          numField('costs.slippage_bps', 'Slippage (bps)'),
          numField('costs.commission_per_share', 'Commission per share'))),
      h('div', { class: 'card', style: 'margin-top:14px' }, h('div', { class: 'row' },
        field('Name', input('sb-name', spec.name)),
        field('Version', input('sb-version', bump(spec.version))),
        h('div', { class: 'narrow' }, h('button', { class: 'primary', onclick: async () => {
          out.replaceChildren(loading('backtesting…'));
          const s = collect();
          s.entry.fill = val('sb-fill');
          s.market_filter.mode = val('sb-mode');
          s.stop.type = val('sb-stoptype');
          s.management.trail.type = val('sb-trailtype');
          s.name = val('sb-name'); s.version = val('sb-version');
          try { out.replaceChildren(renderBacktest(await api('/api/backtest', { spec: s }))); }
          catch (e) { out.replaceChildren(errorBox(e)); }
        } }, 'Backtest')),
        h('div', { class: 'narrow' }, h('button', { onclick: async () => {
          const s = collect();
          s.name = val('sb-name'); s.version = val('sb-version');
          const r = await api('/api/strategies/save', { spec: s,
            change_reason: 'edited in the strategy builder' });
          alert(r.ok ? `Saved ${r.name} v${r.version} (id ${r.id}). ${r.note}`
                     : 'Error: ' + r.error);
        } }, 'Save version')),
        h('div', { class: 'narrow' }, h('button', { onclick: () => {
          out.replaceChildren(jsonBlock(collect()));
        } }, 'Show JSON')))),
      out));
  };
  render();
}

function bump(v) {
  const m = String(v).match(/^(\d+)\.(\d+)(.*)$/);
  return m ? `${m[1]}.${parseInt(m[2], 10) + 1}${m[3]}` : v + '.1';
}

/* ------------------------------------------------------------- BACKTESTER */
export async function backtester(root, ctx) {
  root.replaceChildren(loading('loading…'));
  const lib = ctx.state.strategies || (await api('/api/strategies')).library;
  ctx.state.strategies = lib;
  const prior = await api('/api/backtests?limit=25');
  const out = h('div', { style: 'margin-top:14px' });

  const wrap = h('div', {},
    h('div', { class: 'card' }, h('div', { class: 'row' },
      field('Strategy', select('bt-strat',
        lib.map(s => ({ value: s.key, label: `${s.name} v${s.version}` })), 'sar_v1_1_adr_stop')),
      field('Start', input('bt-start', '2015-01-01')),
      field('End', input('bt-end', '')),
      field('Max symbols', input('bt-max', '', { type: 'number', placeholder: 'all' })),
      h('div', { class: 'narrow' }, h('button', { class: 'primary', id: 'bt-run',
        onclick: async () => {
          const b = document.getElementById('bt-run'); b.disabled = true;
          out.replaceChildren(loading('running backtest…'));
          try {
            out.replaceChildren(renderBacktest(await api('/api/backtest', {
              strategy_key: val('bt-strat'), start: val('bt-start') || null,
              end: val('bt-end') || null, max_symbols: numVal('bt-max', null) })));
          } catch (e) { out.replaceChildren(errorBox(e)); }
          b.disabled = false;
        } }, 'Run backtest'))),
      h('div', { class: 'faint' },
        'Fills default to the next open. Stops fill at the open when a bar gaps through them. '
        + 'A bar containing both the stop and a target resolves to the stop. Costs apply to '
        + 'every fill including partials.')),
    out,
    h('div', { class: 'card', style: 'margin-top:14px' }, h('h3', {}, 'Previous runs'),
      table([
        { label: 'ID', key: 'id', num: true },
        { label: 'Strategy', key: 'label' },
        { label: 'Trades', num: true, get: r => r.metrics?.n_trades },
        { label: 'Win %', num: true, get: r => num(r.metrics?.win_rate_pct, 1) },
        { label: 'Expectancy R', num: true, get: r => num(r.metrics?.expectancy_r, 3) },
        { label: 'PF', num: true, get: r => num(r.metrics?.profit_factor, 2) },
        { label: 'Max DD %', num: true, get: r => num(r.metrics?.max_drawdown_pct, 1) },
        { label: 'Data', key: 'data_origin' },
        { label: 'When', get: r => (r.created_at || '').slice(0, 16) },
      ], prior.backtests, { onRow: async r => {
        out.replaceChildren(loading('loading…'));
        out.replaceChildren(renderBacktest(await api('/api/backtests/' + r.id)));
        window.scrollTo({ top: 0, behavior: 'smooth' });
      } })));
  root.replaceChildren(wrap);
}

export function renderBacktest(d) {
  const m = d.metrics || {};
  const curve = d.equity_curve || [];
  const wrap = h('div', {});
  const ob = originBanner(d.data_origin);
  if (ob) wrap.appendChild(ob);
  (d.warnings || []).slice(0, 6).forEach(w => wrap.appendChild(banner(w)));

  wrap.appendChild(h('div', { class: 'grid g4' },
    h('div', { class: 'card tight' }, stat('Trades', m.n_trades ?? '--',
      `${m.n_wins ?? 0}W / ${m.n_losses ?? 0}L`)),
    h('div', { class: 'card tight' }, stat('Win rate', pct(m.win_rate_pct, 1))),
    h('div', { class: 'card tight' }, stat('Expectancy', num(m.expectancy_r, 3) + 'R',
      `median ${num(m.median_r, 3)}R`, signClass(m.expectancy_r))),
    h('div', { class: 'card tight' }, stat('Profit factor', num(m.profit_factor, 2))),
    h('div', { class: 'card tight' }, stat('Total return', pct(m.total_return_pct, 1),
      `CAGR ${pct(m.cagr_pct, 1)}`, signClass(m.total_return_pct))),
    h('div', { class: 'card tight' }, stat('Max drawdown', pct(m.max_drawdown_pct, 1),
      `avg ${pct(m.avg_drawdown_pct, 1)}`, 'down')),
    h('div', { class: 'card tight' }, stat('Sharpe / Sortino',
      `${num(m.sharpe, 2)} / ${num(m.sortino, 2)}`, `Calmar ${num(m.calmar, 2)}`)),
    h('div', { class: 'card tight' }, stat('Exposure', pct(m.exposure_pct, 1),
      `${num(m.avg_hold_bars, 1)} bars average hold`))));

  if (curve.length) {
    const eq = curve.map(c => c.equity);
    const peak = []; let mx = -Infinity;
    for (const v of eq) { mx = Math.max(mx, v); peak.push((v / mx - 1) * 100); }
    wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, `Equity curve — ${d.start} to ${d.end}`),
      lineChart([{ name: 'equity', values: eq, color: '#4da3ff' }],
                { height: 240, labels: curve.map(c => c.date) }),
      h('h3', { style: 'margin-top:10px' }, 'Drawdown %'),
      lineChart([{ name: 'dd', values: peak, color: '#ff5c6c' }],
                { height: 120, labels: curve.map(c => c.date), max: 0 })));
  }

  const trades = d.trades || [];
  if (trades.length) {
    wrap.appendChild(h('div', { class: 'grid g2', style: 'margin-top:14px' },
      h('div', { class: 'card' }, h('h3', {}, 'R-multiple distribution'),
        histogram(trades.map(t => t.r_multiple), { height: 180, bins: 40 }),
        h('div', { class: 'faint' },
          `p05 ${num(m.r_distribution?.p05, 2)} · p25 ${num(m.r_distribution?.p25, 2)} · `
          + `median ${num(m.r_distribution?.p50, 2)} · p75 ${num(m.r_distribution?.p75, 2)} · `
          + `p95 ${num(m.r_distribution?.p95, 2)}`)),
      h('div', { class: 'card' }, h('h3', {}, 'Why candidates were rejected'),
        barChart(Object.entries(d.signals_rejected || {}).slice(0, 10).map(([k, v]) =>
          ({ label: k, value: v, color: '#3d4a63' })), { rowH: 20, labelW: 200, decimals: 0 }),
        h('div', { class: 'faint' }, `${d.signals_generated ?? '--'} raw breakout signals were `
          + 'examined; the bars show which filter removed them.'))));

    wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, `Trades (${trades.length} shown)`),
      table([
        { label: 'Symbol', key: 'symbol' },
        { label: 'Entry', key: 'entry_date' },
        { label: 'Exit', key: 'exit_date' },
        { label: 'Bars', key: 'bars_held', num: true },
        { label: 'In', num: true, get: t => num(t.entry_price, 2) },
        { label: 'Stop', num: true, get: t => num(t.initial_stop, 2) },
        { label: 'Out', num: true, get: t => num(t.avg_exit_price, 2) },
        { label: 'P/L', num: true, get: t => h('span', { class: signClass(t.pnl) },
            money(t.pnl, 2)) },
        { label: 'R', num: true, get: t => h('span', { class: signClass(t.r_multiple) },
            num(t.r_multiple, 2)) },
        { label: 'MFE R', num: true, get: t => num(t.mfe_r, 2) },
        { label: 'MAE R', num: true, get: t => num(t.mae_r, 2) },
        { label: 'Exit reason', key: 'exit_reason' },
      ], trades.slice(0, 400), { scrollY: true })));
  } else {
    wrap.appendChild(banner('This run produced no trades. The rejection breakdown below shows '
      + 'which filter removed every candidate — that is the diagnostic, not an error.'));
    wrap.appendChild(h('div', { class: 'card' }, jsonBlock(d.signals_rejected || {})));
  }

  if (d.backtest_id) {
    const mcOut = h('div', {});
    wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, 'Robustness'),
      h('div', { class: 'row' },
        h('div', { class: 'narrow' }, h('button', { onclick: async (e) => {
          e.target.disabled = true;
          mcOut.replaceChildren(loading('simulating…'));
          try { mcOut.replaceChildren(renderMonteCarlo(await api('/api/montecarlo',
            { backtest_id: d.backtest_id, simulations: 5000 }))); }
          catch (err) { mcOut.replaceChildren(errorBox(err)); }
          e.target.disabled = false;
        } }, 'Monte Carlo')),
        h('div', { class: 'narrow' }, h('button', { onclick: async (e) => {
          e.target.disabled = true;
          mcOut.replaceChildren(loading('sweeping parameters — this runs many backtests…'));
          try {
            const r = await api('/api/sensitivity', {
              strategy_key: 'sar_v1_0', parameter: 'setup.prior_move_min_pct',
              values: [20, 25, 30, 35, 40], start: d.start, end: d.end, max_symbols: 40 });
            mcOut.replaceChildren(renderSensitivity(r));
          } catch (err) { mcOut.replaceChildren(errorBox(err)); }
          e.target.disabled = false;
        } }, 'Sensitivity')),
        h('div', { class: 'narrow' }, h('button', { onclick: async (e) => {
          e.target.disabled = true;
          mcOut.replaceChildren(loading('walk-forward: training and testing each fold…'));
          try {
            const r = await api('/api/walkforward', { strategy_key: 'sar_v1_0',
              parameter: 'setup.prior_move_min_pct', values: [20, 30, 40],
              start: '2012-01-01', train_years: 4, test_years: 1.5, max_symbols: 40 });
            mcOut.replaceChildren(renderWalkForward(r));
          } catch (err) { mcOut.replaceChildren(errorBox(err)); }
          e.target.disabled = false;
        } }, 'Walk-forward'))),
      mcOut));
  }

  if (d.claim) wrap.appendChild(h('div', { class: 'card', style: 'margin-top:14px' },
    claimBlock(d.claim)));
  wrap.appendChild(details('All metrics', () => jsonBlock(m)));
  return wrap;
}

export function renderMonteCarlo(r) {
  if (!r.ok) return h('div', { class: 'card' }, h('p', {}, r.reason));
  const c = r.equity_percentile_curves;
  return h('div', { class: 'card' }, h('h3', {}, 'Monte Carlo'),
    lineChart([
      { name: 'p95', values: c.p95, color: '#2a5a44', width: 1 },
      { name: 'p75', values: c.p75, color: '#35c47a', width: 1, dash: '3 3' },
      { name: 'p50', values: c.p50, color: '#4da3ff', width: 2 },
      { name: 'p25', values: c.p25, color: '#c78a3a', width: 1, dash: '3 3' },
      { name: 'p05', values: c.p05, color: '#ff5c6c', width: 1 },
    ], { height: 240 }),
    h('div', { class: 'grid g4', style: 'margin-top:10px' },
      h('div', {}, stat('Median return', pct(r.total_return_pct.p50, 1),
        `p05 ${pct(r.total_return_pct.p05, 1)} · p95 ${pct(r.total_return_pct.p95, 1)}`)),
      h('div', {}, stat('Median max DD', pct(r.max_drawdown_pct.p50, 1),
        `worst ${pct(r.max_drawdown_pct.worst, 1)}`, 'down')),
      h('div', {}, stat('P(end below start)', pct(r.prob_decline_pct, 1))),
      h('div', {}, stat('Losing streak p99', num(r.losing_streak.p99, 0),
        `worst ${num(r.losing_streak.worst, 0)}`))),
    h('ul', { class: 'bul faint', style: 'margin-top:10px' },
      r.assumptions.map(a => h('li', {}, a))),
    claimBlock(r.claim));
}

export function renderSensitivity(r) {
  const wrap = h('div', { class: 'card' }, h('h3', {}, 'Parameter sensitivity'),
    h('div', {}, pill(r.overall_verdict,
      r.overall_verdict === 'FRAGILE' ? 'down' : 'up')),
    h('p', { class: 'faint' }, `baseline ${r.objective} = ${num(r.baseline_score, 4)}`));
  for (const [param, sw] of Object.entries(r.sweeps)) {
    wrap.appendChild(h('div', { style: 'margin-top:10px' },
      h('b', {}, param), ' ', pill(sw.verdict, sw.verdict === 'FRAGILE' ? 'down'
        : sw.verdict === 'ROBUST' ? 'up' : 'warn'),
      h('p', { class: 'faint' }, sw.detail),
      table([
        { label: 'Value', key: 'value', num: true },
        { label: 'Trades', key: 'n_trades', num: true },
        { label: 'Expectancy R', num: true, get: p => num(p.expectancy_r, 3) },
        { label: 'Win %', num: true, get: p => num(p.win_rate_pct, 1) },
        { label: 'PF', num: true, get: p => num(p.profit_factor, 2) },
        { label: 'Return %', num: true, get: p => num(p.total_return_pct, 1) },
        { label: 'Max DD %', num: true, get: p => num(p.max_drawdown_pct, 1) },
      ], sw.points)));
  }
  wrap.appendChild(claimBlock(r.claim));
  return wrap;
}

export function renderWalkForward(r) {
  if (!r.ok) return h('div', { class: 'card' }, h('p', {}, r.reason || 'walk-forward failed'));
  const wrap = h('div', { class: 'card' }, h('h3', {}, 'Walk-forward'),
    h('div', {}, pill(r.verdict, r.verdict === 'OUT_OF_SAMPLE_HOLDS' ? 'up'
      : r.verdict === 'FAILS_OUT_OF_SAMPLE' ? 'down' : 'warn')),
    h('div', { class: 'kv', style: 'margin-top:8px' },
      h('span', { class: 'k' }, 'Objective'), h('span', { class: 'v' }, r.objective),
      h('span', { class: 'k' }, 'Folds'), h('span', { class: 'v' }, `${r.n_scored} / ${r.n_windows}`),
      h('span', { class: 'k' }, 'Mean in-sample'), h('span', { class: 'v' }, num(r.mean_train_score, 4)),
      h('span', { class: 'k' }, 'Mean out-of-sample'),
      h('span', { class: 'v ' + signClass(r.mean_test_score) }, num(r.mean_test_score, 4)),
      h('span', { class: 'k' }, 'Degradation'),
      h('span', { class: 'v ' + signClass(r.degradation) }, num(r.degradation, 4)),
      h('span', { class: 'k' }, '% test windows positive'),
      h('span', { class: 'v' }, pct(r.pct_test_windows_positive, 0))),
    table([
      { label: 'Train', get: f => `${f.window.train_start} → ${f.window.train_end}` },
      { label: 'Test', get: f => `${f.window.test_start} → ${f.window.test_end}` },
      { label: 'Chosen', get: f => JSON.stringify(f.selected) },
      { label: 'Train score', num: true, get: f => num(f.train_score, 4) },
      { label: 'Test score', num: true, get: f => num(f.test_score, 4) },
      { label: 'Test trades', num: true, get: f => f.test_metrics?.n_trades },
      { label: 'Test max DD %', num: true, get: f => num(f.test_metrics?.max_drawdown_pct, 1) },
    ], r.folds.filter(f => f.selected)),
    h('h3', { style: 'margin-top:12px' }, 'Parameter stability'),
    table([
      { label: 'Parameter', get: e => e[0] },
      { label: 'Modal choice', get: e => e[1].modal_value },
      { label: 'Modal share', get: e => pct(e[1].modal_share_pct, 0) },
      { label: 'Distinct choices', num: true, get: e => e[1].distinct_choices },
      { label: 'Per fold', get: e => e[1].values.join(', ') },
    ], Object.entries(r.parameter_stability || {})),
    h('p', { class: 'faint' }, 'A parameter whose optimum jumps every fold is noise, not an edge.'),
    claimBlock(r.claim));
  return wrap;
}

/* --------------------------------------------------------- KNOWLEDGE BASE */
export async function knowledge(root, ctx) {
  root.replaceChildren(loading('loading knowledge base…'));
  let d, concepts;
  try { [d, concepts] = await Promise.all([api('/api/knowledge'), api('/api/concepts')]); }
  catch (e) { return root.replaceChildren(errorBox(e)); }
  const why = h('div', { style: 'margin-top:14px' });

  const wrap = h('div', {},
    banner(d.stats.verification_note),
    h('div', { class: 'grid g4' },
      h('div', { class: 'card tight' }, stat('Knowledge items', d.stats.items)),
      h('div', { class: 'card tight' }, stat('Explicit / inferred',
        `${d.stats.explicit_rules} / ${d.stats.inferred_rules}`)),
      h('div', { class: 'card tight' }, stat('Candidate definitions',
        d.stats.candidate_definitions)),
      h('div', { class: 'card tight' }, stat('Sources',
        `${d.stats.sources_fulltext_verified} / ${d.stats.sources}`, 'full text verified'))),
    h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, 'Rules and where they came from'),
      h('div', { class: 'row' },
        field('Search', input('kb-q', '', { placeholder: 'volume, stop, trail…' })),
        field('Category', select('kb-cat', ['', 'SETUP', 'ENTRY', 'RISK',
          'TRADE_MANAGEMENT', 'MARKET_REGIME', 'STOCK_SELECTION', 'SECTOR_THEME'], '')),
        h('div', { class: 'narrow' }, h('button', { class: 'primary', onclick: async () => {
          const r = await api(`/api/knowledge?q=${encodeURIComponent(val('kb-q'))}`
            + `&category=${encodeURIComponent(val('kb-cat'))}`);
          document.getElementById('kb-table').replaceChildren(kbTable(r.items, why));
        } }, 'Search'))),
      h('div', { id: 'kb-table' }, kbTable(d.items, why))),
    why,
    h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, 'Qualitative concepts and their competing definitions'),
      h('p', { class: 'faint' },
        'No single operationalisation is treated as correct. Each concept keeps every '
        + 'candidate so the Research Lab can test which one, if any, matters.'),
      ...Object.entries(concepts.concepts).map(([name, c]) =>
        details(`${name.toUpperCase()} — ${c.category}`, () => h('div', {},
          h('p', {}, c.description),
          h('p', { class: 'warnc' }, 'Ambiguity preserved: ' + c.ambiguity),
          table([{ label: 'Candidate definition', key: 'label' },
                 { label: 'Applied as', get: x => JSON.stringify(x.params) }],
                c.candidates))))),
    h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, 'Sources'),
      table([
        { label: 'Source', key: 'source' },
        { label: 'Title', key: 'title' },
        { label: 'Author', key: 'author' },
        { label: 'Type', key: 'source_type' },
        { label: 'Retrieval', key: 'retrieval_method' },
        { label: 'Verified', get: s => pill(s.fulltext_verified ? 'full text' : 'UNVERIFIED',
            s.fulltext_verified ? 'up' : 'warn') },
        { label: 'URL', get: s => s.url ? h('a', { href: s.url, target: '_blank',
            rel: 'noopener noreferrer' }, s.url.slice(0, 46) + '…') : '--' },
      ], d.sources),
      h('p', { class: 'faint' }, 'Run `python -m tradingbrain.cli research refresh` from a '
        + 'machine with outbound network access to fetch first-party pages, attach '
        + 'citation-length excerpts and flip the verified flag.')));
  root.replaceChildren(wrap);
}

function kbTable(items, whyBox) {
  return table([
    { label: 'Category', key: 'category' },
    { label: 'Concept', key: 'concept' },
    { label: 'Rule implied', key: 'rule_text' },
    { label: 'Stated?', get: k => pill(k.explicit ? 'explicit' : 'inferred',
        k.explicit ? 'info' : 'warn') },
    { label: 'Source', get: k => h('span', {}, k.source,
        k.fulltext_verified ? '' : ' ', k.fulltext_verified ? null : pill('unverified', 'warn')) },
    { label: 'Definitions', num: true,
      get: k => (k.quantitative_interpretation || []).length },
    { label: '', get: k => h('button', { onclick: async (e) => {
        e.stopPropagation();
        whyBox.replaceChildren(loading('tracing…'));
        const r = await api('/api/knowledge/why?concept=' + encodeURIComponent(k.concept));
        whyBox.replaceChildren(renderWhy(r));
        whyBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } }, 'Why?') },
  ], items, { scrollY: true });
}

function renderWhy(r) {
  if (!r.found) return h('div', { class: 'card' }, h('p', {}, r.reason));
  return h('div', { class: 'card' },
    h('h3', {}, `Why the system holds "${r.concept}"`),
    ...r.chains.map(c => h('div', { class: 'card', style: 'margin-bottom:10px' },
      h('div', { class: 'kv' },
        h('span', { class: 'k' }, '1. Source'),
        h('span', { class: 'v' }, `${c.source.name}${c.source.author ? ' — ' + c.source.author : ''}`,
          c.source.url ? h('span', {}, ' ', h('a', { href: c.source.url, target: '_blank',
            rel: 'noopener noreferrer' }, 'link')) : null,
          ' ', pill(c.source.fulltext_verified ? 'verified' : 'UNVERIFIED',
                    c.source.fulltext_verified ? 'up' : 'warn')),
        h('span', { class: 'k' }, '   says'), h('span', { class: 'v' }, c.step_1_source_says),
        h('span', { class: 'k' }, '   quote'),
        h('span', { class: 'v' }, c.step_1_verbatim_quote
          || 'none stored — the page was not fetched in full, so nothing is quoted'),
        h('span', { class: 'k' }, '2. Rule implied'),
        h('span', { class: 'v' }, `${c.step_2_rule_implied} (${c.step_2_explicit_or_inferred})`),
        h('span', { class: 'k' }, '3. Candidate definitions'),
        h('span', { class: 'v' }, (c.step_3_quantitative_candidates || [])
          .map(x => x.label).join(' | ') || 'none'),
        h('span', { class: 'k' }, '4. Hypothesis'),
        h('span', { class: 'v' }, c.step_4_backtest_hypothesis || '--'),
        h('span', { class: 'k' }, '5. Evidence'),
        h('span', { class: 'v' }, (c.step_5_evidence || []).length
          ? (c.step_5_evidence || []).map(e => `#${e.id} ${e.verdict}`).join(', ')
          : c.step_5_note),
        h('span', { class: 'k' }, 'Implemented at'),
        h('span', { class: 'v mono' }, c.implemented_at || '--')))),
    claimBlock(r.claim));
}
