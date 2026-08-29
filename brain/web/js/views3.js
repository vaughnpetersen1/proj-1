/* Views: Options Lab, Trade Journal, AI Brain, Settings. */
import { api, h, num, pct, money, signClass, stat, pill, claimBlock, banner, table,
         details, jsonBlock, field, input, select, val, numVal, loading, errorBox }
  from './util.js';
import { lineChart, barChart } from './charts.js';
import { originBanner } from './views1.js';

/* ------------------------------------------------------------ OPTIONS LAB */
export async function options(root, ctx) {
  const sym = (ctx.state.symbol || 'NVDA').toUpperCase();
  root.replaceChildren(loading('loading chain…'));
  let d;
  try { d = await api(`/api/options/${encodeURIComponent(sym)}`); }
  catch (e) { return root.replaceChildren(errorBox(e)); }
  const out = h('div', { style: 'margin-top:14px' });
  const scenarioOut = h('div', { style: 'margin-top:14px' });

  const exps = d.expirations || [...new Set(d.contracts.map(c => c.expiration))].sort();
  const chosen = ctx.state.optExp && exps.includes(ctx.state.optExp) ? ctx.state.optExp
    : exps[Math.min(3, exps.length - 1)];
  const rows = d.contracts.filter(c => c.expiration === chosen);

  const wrap = h('div', {},
    d.source.is_model_estimate
      ? banner('MODEL-GENERATED CHAIN. ' + d.source.detail
          + ' Premiums, greeks and the bid/ask spread below are computed from the '
          + "underlying's realised volatility, not quoted by any exchange. They are "
          + 'MODEL_ESTIMATE values and are not tradeable.', 'synth')
      : banner(d.source.detail, 'info'),
    h('div', { class: 'card' }, h('div', { class: 'row' },
      field('Symbol', input('op-sym', sym)),
      field('Expiration', select('op-exp', exps, chosen)),
      h('div', { class: 'narrow' }, h('button', { class: 'primary', onclick: () => {
        ctx.state.symbol = val('op-sym').toUpperCase();
        ctx.state.optExp = val('op-exp');
        ctx.go('options');
      } }, 'Load')),
      h('div', { style: 'flex:2' }, h('div', { class: 'kv' },
        h('span', { class: 'k' }, 'Underlying'), h('span', { class: 'v' }, num(d.underlying)),
        h('span', { class: 'k' }, 'As of'), h('span', { class: 'v' }, d.as_of),
        h('span', { class: 'k' }, 'Volatility basis'),
        h('span', { class: 'v' }, d.volatility_basis?.realised_vol_annual
          ? `${num(d.volatility_basis.realised_vol_annual, 1)}% realised (30d)`
            + (d.volatility_basis.percentile_of_realised_vol !== null
               ? ` · ${num(d.volatility_basis.percentile_of_realised_vol, 0)}th percentile` : '')
          : 'vendor implied volatility'))))),

    d.volatility_basis?.note ? h('p', { class: 'faint' }, d.volatility_basis.note) : null,

    h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, `Chain — ${chosen}`),
      table([
        { label: 'Type', key: 'type' },
        { label: 'Strike', key: 'strike', num: true },
        { label: 'DTE', key: 'dte', num: true },
        { label: 'Bid', num: true, get: c => num(c.bid, 2) },
        { label: 'Ask', num: true, get: c => num(c.ask, 2) },
        { label: 'Mid', num: true, get: c => num(c.mid, 2) },
        { label: 'Spread %', num: true, get: c => num(c.spread_pct, 1) },
        { label: 'IV %', num: true, get: c => num((c.implied_volatility ?? 0) * 100, 1) },
        { label: 'Delta', num: true, get: c => num(c.delta, 3) },
        { label: 'Gamma', num: true, get: c => num(c.gamma, 5) },
        { label: 'Theta', num: true, get: c => num(c.theta, 3) },
        { label: 'Vega', num: true, get: c => num(c.vega, 3) },
        { label: 'Volume', num: true, get: c => c.volume ?? '--' },
        { label: 'OI', num: true, get: c => c.open_interest ?? '--' },
        { label: '', get: c => h('button', { onclick: () => {
            document.getElementById('sc-strike').value = c.strike;
            document.getElementById('sc-dte').value = c.dte;
            document.getElementById('sc-prem').value = num(c.mid, 2);
            document.getElementById('sc-iv').value = num((c.implied_volatility ?? .3) * 100, 1);
            document.getElementById('sc-type').value = c.type;
            document.getElementById('sc-und').value = num(d.underlying, 2);
          } }, 'Model') },
      ], rows, { scrollY: true }),
      h('p', { class: 'faint' }, 'Volume and open interest show "--" when the source cannot '
        + 'supply them. They are never invented.')),

    h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, 'Compare contracts on the same move'),
      h('div', { class: 'row' },
        field('Move %', input('op-move', '10')),
        field('Hold days', input('op-hold', '10')),
        h('div', { class: 'narrow' }, h('button', { class: 'primary', onclick: async () => {
          out.replaceChildren(loading('pricing…'));
          const strikes = [d.underlying, d.underlying * 1.05, d.underlying * 1.10];
          const contracts = strikes.map(s => ({ strike: s, expiration: chosen, type: 'call' }));
          const later = exps[Math.min(exps.indexOf(chosen) + 2, exps.length - 1)];
          contracts.push({ strike: d.underlying, expiration: later, type: 'call' });
          try {
            out.replaceChildren(renderCompare(await api('/api/options/compare',
              { symbol: sym, contracts, move_pct: numVal('op-move', 10),
                hold_days: numVal('op-hold', 10) })));
          } catch (e) { out.replaceChildren(errorBox(e)); }
        } }, 'Compare')))),
    out,

    h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, 'Scenario model'),
      h('div', { class: 'row' },
        field('Underlying', input('sc-und', num(d.underlying, 2))),
        field('Strike', input('sc-strike', num(d.underlying * 1.05, 2))),
        field('DTE', input('sc-dte', '30')),
        field('Premium', input('sc-prem', '5.00')),
        field('IV %', input('sc-iv', num((d.volatility_basis?.realised_vol_annual ?? 35), 1))),
        field('Type', select('sc-type', ['call', 'put'], 'call')),
        field('Contracts', input('sc-qty', '1')),
        h('div', { class: 'narrow' }, h('button', { class: 'primary', onclick: async () => {
          scenarioOut.replaceChildren(loading('modelling…'));
          try {
            scenarioOut.replaceChildren(renderScenario(await api('/api/options/scenario', {
              underlying: numVal('sc-und'), strike: numVal('sc-strike'),
              dte: numVal('sc-dte', 30), premium: numVal('sc-prem'),
              iv_pct: numVal('sc-iv', 35), type: val('sc-type'),
              contracts: numVal('sc-qty', 1) })));
          } catch (e) { scenarioOut.replaceChildren(errorBox(e)); }
        } }, 'Model')))),
    scenarioOut);
  root.replaceChildren(wrap);
}

function renderCompare(r) {
  const key = Object.keys(r.rows[0] || {}).find(k => k.startsWith('pnl_if_up'));
  const flatKey = Object.keys(r.rows[0] || {}).find(k => k.startsWith('pnl_if_flat'));
  const downKey = Object.keys(r.rows[0] || {}).find(k => k.startsWith('pnl_if_down'));
  return h('div', { class: 'card' }, h('h3', {}, 'Contract comparison'),
    table([
      { label: 'Strike', key: 'strike', num: true },
      { label: 'Expiry', key: 'expiration' },
      { label: 'DTE', key: 'dte', num: true },
      { label: 'Premium', num: true, get: x => num(x.entry_premium, 2) },
      { label: 'Capital', num: true, get: x => money(x.capital_per_contract, 0) },
      { label: 'Delta', num: true, get: x => num(x.delta, 3) },
      { label: 'Theta/day', num: true, get: x => num(x.theta_per_day, 3) },
      { label: 'Break-even move', num: true, get: x => pct(x.break_even_move_pct, 1) },
      { label: 'Up case', num: true, get: x => h('span', { class: signClass(x[key]) },
          pct(x[key], 0)) },
      { label: 'Flat case', num: true, get: x => h('span', { class: signClass(x[flatKey]) },
          pct(x[flatKey], 0)) },
      { label: 'Down case', num: true, get: x => h('span', { class: signClass(x[downKey]) },
          pct(x[downKey], 0)) },
    ], r.rows.filter(x => x.ok)),
    h('ul', { class: 'bul warnc' }, r.interpretation.map(x => h('li', {}, x))));
}

function renderScenario(r) {
  const wrap = h('div', { class: 'card' }, h('h3', {}, 'Scenario matrix'),
    h('div', { class: 'kv' },
      h('span', { class: 'k' }, 'Max loss'), h('span', { class: 'v' }, money(r.max_loss, 2)),
      h('span', { class: 'k' }, 'Break-even at expiry'),
      h('span', { class: 'v' }, num(r.break_even_at_expiry, 2))));
  for (const block of r.grid) {
    wrap.appendChild(h('h3', { style: 'margin-top:12px' },
      `IV shift ${block.iv_shift_pct >= 0 ? '+' : ''}${block.iv_shift_pct}% → `
      + `${num(block.iv_used_pct, 1)}% volatility`));
    const cols = [{ label: 'Days fwd', get: row => `${row.days_forward} (${row.dte_remaining} DTE)` }];
    r.prices.forEach((p, i) => cols.push({
      label: `${num(p, 2)}`, num: true,
      get: row => h('span', { class: signClass(row.values[i].pnl_dollars) },
        `${money(row.values[i].pnl_dollars, 0)}`) }));
    wrap.appendChild(table(cols, block.rows));
  }
  wrap.appendChild(claimBlock(r.claim));
  return wrap;
}

/* ----------------------------------------------------------- TRADE JOURNAL */
export async function journal(root, ctx) {
  root.replaceChildren(loading('loading journal…'));
  let d, a;
  try { [d, a] = await Promise.all([api('/api/journal'), api('/api/journal/analysis')]); }
  catch (e) { return root.replaceChildren(errorBox(e)); }
  const s = d.summary;

  const form = h('div', { class: 'card' }, h('h3', {}, 'Log a trade'),
    h('div', { class: 'row' },
      field('Ticker', input('j-ticker', '')),
      field('Direction', select('j-dir', ['long', 'short'], 'long')),
      field('Strategy', input('j-strategy', 'SAR Momentum Breakout')),
      field('Setup', input('j-setup', 'breakout')),
      field('Entry date', input('j-edate', new Date().toISOString().slice(0, 10),
                                { type: 'date' })),
      field('Entry', input('j-entry', '', { type: 'number', step: 'any' })),
      field('Stop', input('j-stop', '', { type: 'number', step: 'any' })),
      field('Target', input('j-target', '', { type: 'number', step: 'any' })),
      field('Shares', input('j-size', '', { type: 'number', step: 'any' })),
      field('Equity at entry', input('j-eq', '', { type: 'number', step: 'any' }))),
    h('div', { class: 'row' },
      field('Exit date', input('j-xdate', '', { type: 'date' })),
      field('Exit', input('j-exit', '', { type: 'number', step: 'any' })),
      field('Market regime', input('j-regime', '')),
      field('Sector', input('j-sector', '')),
      field('Instrument', select('j-instr', ['stock', 'option'], 'stock'))),
    h('div', { class: 'row' },
      field('Thesis', input('j-thesis', '')),
      field('Reason for entry', input('j-rentry', '')),
      field('Reason for exit', input('j-rexit', '')),
      field('Mistakes', input('j-mistakes', '')),
      field('Notes', input('j-notes', '')),
      field('Screenshot path/URL', input('j-shot', ''))),
    h('div', { class: 'row' },
      h('div', { class: 'narrow' }, h('button', { class: 'primary', onclick: async () => {
        const body = {
          ticker: val('j-ticker').toUpperCase(), direction: val('j-dir'),
          strategy: val('j-strategy'), setup: val('j-setup'),
          entry_date: val('j-edate'), entry_price: numVal('j-entry'),
          stop_price: numVal('j-stop'), target_price: numVal('j-target'),
          position_size: numVal('j-size'), account_equity_at_entry: numVal('j-eq'),
          exit_date: val('j-xdate'), exit_price: numVal('j-exit'),
          market_regime: val('j-regime'), sector: val('j-sector'),
          instrument: val('j-instr'), thesis: val('j-thesis'),
          reason_entry: val('j-rentry'), reason_exit: val('j-rexit'),
          mistakes: val('j-mistakes'), notes: val('j-notes'),
          screenshot_path: val('j-shot') };
        const r = await api('/api/journal/trade', body);
        if (!r.ok) return alert('Error: ' + r.error);
        ctx.go('journal');
      } }, 'Save trade')),
      h('div', { class: 'narrow' }, h('button', { onclick: () => {
        const el = document.getElementById('j-csv');
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
      } }, 'Import CSV'))),
    h('div', { id: 'j-csv', style: 'display:none;margin-top:10px' },
      field('Paste CSV (ticker,entry_date,entry_price,stop_price,position_size,exit_date,'
        + 'exit_price,setup,strategy,market_regime,notes …)',
        h('textarea', { id: 'j-csvtext', rows: 6 })),
      h('button', { onclick: async () => {
        const r = await api('/api/journal/import', { csv: val('j-csvtext') });
        alert(`Imported ${r.imported}. Ignored columns: ${r.ignored_columns.join(', ') || 'none'}.`
          + (r.errors.length ? '\nErrors:\n' + r.errors.slice(0, 10).join('\n') : ''));
        ctx.go('journal');
      } }, 'Import')));

  const wrap = h('div', {},
    s.sample_warning ? banner(s.sample_warning, 'synth') : null,
    h('div', { class: 'grid g4' },
      h('div', { class: 'card tight' }, stat('Closed', s.closed, `${s.open} open`)),
      h('div', { class: 'card tight' }, stat('Net P/L', money(s.net_pnl, 2), '',
        signClass(s.net_pnl))),
      h('div', { class: 'card tight' }, stat('Win rate', pct(s.win_rate_pct, 1))),
      h('div', { class: 'card tight' }, stat('Expectancy',
        s.expectancy_r === null ? '--' : num(s.expectancy_r, 3) + 'R',
        s.expectancy_dollars === null ? '' : money(s.expectancy_dollars, 2)))),
    h('div', { style: 'margin-top:14px' }, form),
    h('div', { class: 'card', style: 'margin-top:14px' },
      h('h3', {}, 'What the journal says about you'),
      h('p', { class: 'faint' }, a.disclaimer || ''),
      h('ul', { class: 'bul' }, (a.findings || []).map(f =>
        h('li', {}, pill(f.confidence || '', f.confidence === 'SUPPORTED' ? 'up'
          : f.confidence === 'INSUFFICIENT_DATA' ? 'warn' : 'info'), ' ',
          h('b', {}, f.finding), h('div', { class: 'faint' }, f.detail || ''),
          f.p_value !== undefined && f.p_value !== null
            ? h('div', { class: 'faint mono' }, `p = ${num(f.p_value, 4)}, N = ${f.n}`) : null))),
      (a.claims || []).map(claimBlock)),
    ...Object.entries(a.by || {}).filter(([k, v]) => Array.isArray(v) && v.length).map(([k, v]) =>
      h('div', { class: 'card', style: 'margin-top:14px' },
        h('h3', {}, 'By ' + k.replace(/_/g, ' ')),
        table([
          { label: k, key: 'group' },
          { label: 'N', key: 'n', num: true },
          { label: 'Net P/L', num: true, get: g => money(g.net_pnl, 2) },
          { label: 'Win %', num: true, get: g => num(g.win_rate_pct, 1) },
          { label: '95% CI', num: true, get: g => g.win_rate_ci_pct
              ? `[${num(g.win_rate_ci_pct[0], 0)}, ${num(g.win_rate_ci_pct[1], 0)}]` : '--' },
          { label: 'Expectancy R', num: true, get: g => num(g.expectancy_r, 3) },
          { label: 'Reportable', get: g => pill(g.reportable ? 'yes' : 'too few',
              g.reportable ? 'up' : 'warn') },
        ], v))),
    h('div', { class: 'card', style: 'margin-top:14px' }, h('h3', {}, 'Trades'),
      d.trades.length ? table([
        { label: 'Ticker', key: 'ticker' },
        { label: 'Dir', key: 'direction' },
        { label: 'Setup', key: 'setup' },
        { label: 'In', key: 'entry_date' },
        { label: 'Entry', num: true, get: t => num(t.entry_price, 2) },
        { label: 'Stop', num: true, get: t => num(t.stop_price, 2) },
        { label: 'Out', key: 'exit_date' },
        { label: 'Exit', num: true, get: t => num(t.exit_price, 2) },
        { label: 'P/L', num: true, get: t => h('span', { class: signClass(t.pnl) },
            money(t.pnl, 2)) },
        { label: 'R', num: true, get: t => h('span', { class: signClass(t.r_multiple) },
            num(t.r_multiple, 2)) },
        { label: 'Regime', key: 'market_regime' },
        { label: 'Mistakes', key: 'mistakes' },
        { label: '', get: t => h('button', { onclick: async () => {
            if (confirm(`Delete trade #${t.id} ${t.ticker}?`)) {
              await api('/api/journal/delete', { id: t.id }); ctx.go('journal');
            } } }, '×') },
      ], d.trades, { scrollY: true })
        : h('div', { class: 'dim' }, 'No trades logged yet.')));
  root.replaceChildren(wrap);
}

/* ---------------------------------------------------------------- AI BRAIN */
export async function brain(root, ctx) {
  const modes = await api('/api/ai/modes');
  const log = h('div', { class: 'chat' });
  const suggestions = [
    'What is the market regime?', "Find today's best setups",
    'Analyze NVDA', 'Why should I NOT take NVDA?',
    'What sectors are leading?', 'What themes are emerging?',
    'Does volume contraction during consolidation improve breakout expectancy?',
    'Does the market filter improve results?',
    'Compare the 10 SMA and 20 SMA trailing stops',
    'Backtest the Kullamagi breakout strategy',
    'Run a monte carlo on my last backtest',
    'Which option contract gives me the best risk/reward for NVDA?',
    'What mistakes have I been making?',
    'Where did the rule about volume drying up come from?',
  ];

  const send = async (q) => {
    if (!q.trim()) return;
    log.prepend(h('div', { class: 'msg user' }, h('b', {}, 'You: '), q));
    const pending = h('div', { class: 'msg' }, loading('thinking — running tools…'));
    log.prepend(pending);
    try {
      const r = await api('/api/ai/ask', { question: q, mode: val('ai-mode') || null });
      pending.replaceChildren(renderAnswer(r));
    } catch (e) { pending.replaceChildren(errorBox(e)); }
  };

  root.replaceChildren(h('div', {},
    banner('The language model never produces a number. It selects tools; the tools compute '
      + 'against the real engines; the answer is assembled from what came back. '
      + (modes.llm ? `LLM mode is available (${modes.model}).`
                   : `LLM mode is off (${modes.llm_reason}) — the deterministic router is `
                     + 'answering, which cannot hallucinate because it has no generative part.'),
      'info'),
    h('div', { class: 'card' },
      h('div', { class: 'row' },
        field('Ask', input('ai-q', '', { placeholder: 'Analyze NVDA…' })),
        field('Mode', select('ai-mode',
          [{ value: '', label: 'auto' }, { value: 'router', label: 'router (deterministic)' },
           { value: 'llm', label: 'llm (needs API key)' }], '')),
        h('div', { class: 'narrow' }, h('button', { class: 'primary', onclick: () => {
          const q = val('ai-q'); document.getElementById('ai-q').value = ''; send(q);
        } }, 'Ask'))),
      h('div', { style: 'display:flex;gap:6px;flex-wrap:wrap;margin-top:6px' },
        suggestions.map(sg => h('button', { onclick: () => send(sg),
          style: 'font-size:11px;padding:3px 8px' }, sg)))),
    h('div', { style: 'margin-top:14px' }, log)));

  document.getElementById('ai-q').addEventListener('keydown', e => {
    if (e.key === 'Enter') { const q = e.target.value; e.target.value = ''; send(q); }
  });
}

function renderAnswer(r) {
  const body = h('div', {});
  for (const b of r.blocks) {
    if (b.heading) body.appendChild(h('h3', {}, b.heading));
    const lines = Array.isArray(b.lines) ? b.lines : [String(b.lines)];
    body.appendChild(h('pre', {}, lines.join('\n')));
  }
  (r.warnings || []).forEach(w => body.appendChild(banner(w,
    w.includes('SYNTHETIC') ? 'synth' : '')));
  if ((r.claims || []).length) {
    body.appendChild(details(`Claims with provenance (${r.claims.length})`, () =>
      h('div', {}, r.claims.map(claimBlock))));
  }
  body.appendChild(details(`Tool calls (${(r.tool_calls || []).length}) — the source of every figure`,
    () => h('div', {},
      table([{ label: 'Tool', key: 'tool' },
             { label: 'Arguments', get: t => JSON.stringify(t.arguments) },
             { label: 'Error', key: 'error' }], r.tool_calls || []),
      details('Raw tool results', () => jsonBlock(r.tool_results || {})))));
  return h('div', {}, h('div', { class: 'faint mono' }, 'mode: ' + r.mode), body);
}

/* ---------------------------------------------------------------- SETTINGS */
export async function settings(root, ctx) {
  root.replaceChildren(loading('loading status…'));
  let s, alerts;
  try { [s, alerts] = await Promise.all([api('/api/status'), api('/api/alerts')]); }
  catch (e) { return root.replaceChildren(errorBox(e)); }
  const g = s.trading_guards;

  const wrap = h('div', {},
    h('div', { class: 'card' }, h('h3', {}, 'Trading safety'),
      h('div', {}, pill('MODE: ' + g.mode, g.mode === 'LIVE' ? 'down' : 'up'), ' ',
        pill(g.kill_switch_engaged ? 'KILL SWITCH ENGAGED' : 'kill switch off',
             g.kill_switch_engaged ? 'down' : ''), ' ',
        pill(g.broker_connected ? 'broker connected' : 'no broker', 'info')),
      h('div', { class: 'kv', style: 'margin-top:8px' },
        h('span', { class: 'k' }, 'Max daily loss'), h('span', { class: 'v' }, pct(g.max_daily_loss_pct)),
        h('span', { class: 'k' }, 'Max position'), h('span', { class: 'v' }, pct(g.max_position_pct)),
        h('span', { class: 'k' }, 'Max portfolio exposure'),
        h('span', { class: 'v' }, pct(g.max_portfolio_exposure_pct)),
        h('span', { class: 'k' }, 'Max order notional'),
        h('span', { class: 'v' }, money(g.max_order_notional))),
      h('p', { class: 'warnc' }, g.note)),

    h('div', { class: 'card', style: 'margin-top:14px' }, h('h3', {}, 'Data providers'),
      table([
        { label: 'Provider', key: 'name' },
        { label: 'Available', get: p => pill(p.available ? 'yes' : 'no',
            p.available ? 'up' : 'down') },
        { label: 'Kind', get: p => p.synthetic ? pill('SYNTHETIC', 'synth') : 'real' },
        { label: 'Capabilities', get: p => (p.capabilities || []).join(', ') },
        { label: 'Reason / detail', key: 'reason' },
      ], s.providers),
      h('p', { class: 'faint' }, s.data_note),
      h('p', { class: 'faint' }, `Universe currently served: ${s.universe_size} symbols. `
        + 'Set API keys in the environment (see .env.example) or drop CSVs into the cache '
        + 'directory to change the active data path.')),

    h('div', { class: 'card', style: 'margin-top:14px' }, h('h3', {}, 'Alerts'),
      h('div', { class: 'row' },
        field('Kind', select('al-kind', Object.keys(alerts.kinds), 'approaching_breakout')),
        field('Symbol', input('al-sym', 'NVDA')),
        field('Parameter', input('al-param', '3', { type: 'number', step: 'any' })),
        h('div', { class: 'narrow' }, h('button', { class: 'primary', onclick: async () => {
          const kind = val('al-kind');
          const p = numVal('al-param', 3);
          const cond = kind === 'approaching_breakout' ? { within_pct: p }
            : kind === 'volume_confirmation' ? { min_rvol: p }
            : kind === 'stop_triggered' || kind === 'target_reached' ? { level: p }
            : kind === 'setup_detected' ? { min_score: p } : {};
          const r = await api('/api/alerts', { kind, symbol: val('al-sym'), condition: cond });
          alert(r.ok ? `Created alert #${r.id}` : r.reason);
          ctx.go('settings');
        } }, 'Create')),
        h('div', { class: 'narrow' }, h('button', { onclick: async () => {
          const r = await api('/api/alerts/evaluate', {});
          alert(`Checked ${r.checked}, fired ${r.fired}.`);
          ctx.go('settings');
        } }, 'Evaluate now'))),
      table([{ label: 'Kind', key: 'kind' }, { label: 'What it means',
        get: a => alerts.kinds[a.kind] }], Object.keys(alerts.kinds).map(k => ({ kind: k }))),
      h('h3', { style: 'margin-top:12px' }, 'Configured alerts'),
      alerts.alerts.length ? table([
        { label: 'ID', key: 'id', num: true }, { label: 'Kind', key: 'kind' },
        { label: 'Symbol', key: 'symbol' },
        { label: 'Condition', get: a => JSON.stringify(a.condition) },
        { label: 'Channels', get: a => (a.channels || []).join(', ') },
        { label: 'Last fired', key: 'last_fired' },
      ], alerts.alerts) : h('div', { class: 'dim' }, 'none'),
      h('h3', { style: 'margin-top:12px' }, 'Delivery channels'),
      table([{ label: 'Channel', key: 'name' },
             { label: 'Status', get: c => pill(c.configured ? 'implemented' : 'not implemented',
                 c.configured ? 'up' : 'warn') },
             { label: 'What it needs', key: 'requirement' }], alerts.channels),
      h('h3', { style: 'margin-top:12px' }, 'Recent firings'),
      (alerts.events || []).length ? table([
        { label: 'When', get: e => (e.fired_at || '').slice(0, 16) },
        { label: 'Kind', key: 'kind' }, { label: 'Symbol', key: 'symbol' },
        { label: 'Detail', get: e => e.payload?.detail },
      ], alerts.events) : h('div', { class: 'dim' }, 'none yet')),

    h('div', { class: 'card', style: 'margin-top:14px' }, h('h3', {}, 'Evidence vocabulary'),
      table([{ label: 'Class', get: e => h('span', { class: 'evidence ' + e[0] }, e[0]) },
             { label: 'What it means', get: e => e[1] }],
            Object.entries(s.evidence_classes))),

    h('div', { class: 'card', style: 'margin-top:14px' }, h('h3', {}, 'Stored artefacts'),
      h('div', { class: 'kv' }, ...Object.entries(s.counts).flatMap(([k, v]) =>
        [h('span', { class: 'k' }, k), h('span', { class: 'v' }, v)]))),

    h('div', { class: 'card', style: 'margin-top:14px' }, h('h3', {}, 'Configuration'),
      h('p', { class: 'faint' }, 'Secrets are shown as "<set>" and never returned in full.'),
      jsonBlock(s.settings)));
  root.replaceChildren(wrap);
}
