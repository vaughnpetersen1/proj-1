/* Hand-rolled SVG charts. No CDN, no dependency -- this app has to run on a
   machine with no outbound network, which is the same environment it was built
   in. Everything here is plain SVG so it prints and scales cleanly. */

const NS = 'http://www.w3.org/2000/svg';

function el(name, attrs = {}, text) {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  if (text !== undefined) n.textContent = text;
  return n;
}

function niceTicks(min, max, count = 5) {
  if (!isFinite(min) || !isFinite(max) || min === max) return [min];
  const span = max - min;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(v);
  return out;
}

const fmt = (v, d = 2) =>
  (v === null || v === undefined || !isFinite(v)) ? '--'
    : Math.abs(v) >= 1e9 ? (v / 1e9).toFixed(1) + 'B'
    : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + 'M'
    : Math.abs(v) >= 1e4 ? (v / 1e3).toFixed(1) + 'K'
    : v.toFixed(d);

/* --------------------------------------------------------- candlestick */
export function candleChart(bars, opts = {}) {
  const w = opts.width || 900, h = opts.height || 340;
  const padL = 8, padR = 58, padT = 10, padB = 22;
  const volH = opts.volume === false ? 0 : Math.round(h * 0.18);
  const priceH = h - padT - padB - volH;
  const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, class: 'chart',
                          preserveAspectRatio: 'none', height: h });
  if (!bars || !bars.length) {
    svg.appendChild(el('text', { x: 12, y: 24, fill: '#5d6879', 'font-size': 12 },
                       'no data'));
    return svg;
  }
  const n = bars.length;
  const lo = Math.min(...bars.map(b => b.l));
  const hi = Math.max(...bars.map(b => b.h));
  const pad = (hi - lo) * 0.06 || 1;
  const y0 = lo - pad, y1 = hi + pad;
  const plotW = w - padL - padR;
  const x = i => padL + (i + 0.5) * (plotW / n);
  const y = v => padT + priceH - ((v - y0) / (y1 - y0)) * priceH;
  const bw = Math.max(1, Math.min(9, plotW / n * 0.62));

  for (const t of niceTicks(y0, y1, 5)) {
    const yy = y(t);
    svg.appendChild(el('line', { x1: padL, x2: padL + plotW, y1: yy, y2: yy,
                                 stroke: '#1d2431' }));
    svg.appendChild(el('text', { x: padL + plotW + 6, y: yy + 3, fill: '#5d6879',
                                 'font-size': 9, 'font-family': 'monospace' }, fmt(t)));
  }

  for (const line of (opts.overlays || [])) {
    let d = '', started = false;
    line.values.forEach((v, i) => {
      if (v === null || !isFinite(v)) { started = false; return; }
      d += (started ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
      started = true;
    });
    svg.appendChild(el('path', { d, fill: 'none', stroke: line.color || '#4da3ff',
                                 'stroke-width': line.width || 1.2, opacity: 0.9 }));
  }

  bars.forEach((b, i) => {
    const up = b.c >= b.o;
    const col = up ? '#35c47a' : '#ff5c6c';
    const cx = x(i);
    svg.appendChild(el('line', { x1: cx, x2: cx, y1: y(b.h), y2: y(b.l),
                                 stroke: col, 'stroke-width': 1 }));
    const top = y(Math.max(b.o, b.c)), bot = y(Math.min(b.o, b.c));
    svg.appendChild(el('rect', { x: cx - bw / 2, y: top, width: bw,
                                 height: Math.max(1, bot - top), fill: col, opacity: .92 }));
    const title = el('title', {}, `${b.t.slice(0, 10)}  O ${fmt(b.o)}  H ${fmt(b.h)}  ` +
                                   `L ${fmt(b.l)}  C ${fmt(b.c)}  V ${fmt(b.v, 0)}`);
    svg.appendChild(el('rect', { x: cx - bw, y: padT, width: bw * 2, height: priceH,
                                 fill: 'transparent' })).appendChild(title);
  });

  for (const m of (opts.markers || [])) {
    const i = m.index;
    if (i < 0 || i >= n) continue;
    svg.appendChild(el('line', { x1: x(i), x2: x(i), y1: padT, y2: padT + priceH,
                                 stroke: m.color || '#ffb648', 'stroke-dasharray': '3 3',
                                 'stroke-width': 1, opacity: .8 }));
    svg.appendChild(el('text', { x: x(i) + 3, y: padT + 10, fill: m.color || '#ffb648',
                                 'font-size': 9 }, m.label || ''));
  }
  for (const lv of (opts.levels || [])) {
    if (!isFinite(lv.value)) continue;
    svg.appendChild(el('line', { x1: padL, x2: padL + plotW, y1: y(lv.value), y2: y(lv.value),
                                 stroke: lv.color || '#ffb648', 'stroke-dasharray': '4 4',
                                 'stroke-width': 1, opacity: .85 }));
    svg.appendChild(el('text', { x: padL + 4, y: y(lv.value) - 3, fill: lv.color || '#ffb648',
                                 'font-size': 9 }, lv.label || ''));
  }

  if (volH) {
    const vmax = Math.max(...bars.map(b => b.v)) || 1;
    const vy = v => padT + priceH + volH - (v / vmax) * (volH - 4);
    bars.forEach((b, i) => {
      const up = b.c >= b.o;
      svg.appendChild(el('rect', {
        x: x(i) - bw / 2, y: vy(b.v), width: bw,
        height: Math.max(1, padT + priceH + volH - vy(b.v)),
        fill: up ? '#1e5a3d' : '#5e2730' }));
    });
  }

  const step = Math.max(1, Math.floor(n / 7));
  for (let i = 0; i < n; i += step) {
    svg.appendChild(el('text', { x: x(i), y: h - 6, fill: '#5d6879', 'font-size': 9,
                                 'text-anchor': 'middle' }, bars[i].t.slice(0, 10)));
  }
  return svg;
}

/* --------------------------------------------------------------- lines */
export function lineChart(series, opts = {}) {
  const w = opts.width || 900, h = opts.height || 240;
  const padL = 8, padR = 58, padT = 10, padB = 22;
  const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, class: 'chart',
                          preserveAspectRatio: 'none', height: h });
  const all = series.flatMap(s => s.values.filter(v => v !== null && isFinite(v)));
  if (!all.length) {
    svg.appendChild(el('text', { x: 12, y: 24, fill: '#5d6879', 'font-size': 12 }, 'no data'));
    return svg;
  }
  let y0 = opts.min !== undefined ? opts.min : Math.min(...all);
  let y1 = opts.max !== undefined ? opts.max : Math.max(...all);
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const n = Math.max(...series.map(s => s.values.length));
  const plotW = w - padL - padR, plotH = h - padT - padB;
  const x = i => padL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = v => padT + plotH - ((v - y0) / (y1 - y0)) * plotH;

  for (const t of niceTicks(y0, y1, 5)) {
    svg.appendChild(el('line', { x1: padL, x2: padL + plotW, y1: y(t), y2: y(t),
                                 stroke: '#1d2431' }));
    svg.appendChild(el('text', { x: padL + plotW + 6, y: y(t) + 3, fill: '#5d6879',
                                 'font-size': 9, 'font-family': 'monospace' },
                       fmt(t, opts.decimals ?? 2)));
  }
  if (y0 < 0 && y1 > 0) {
    svg.appendChild(el('line', { x1: padL, x2: padL + plotW, y1: y(0), y2: y(0),
                                 stroke: '#2f3a4d', 'stroke-width': 1 }));
  }
  for (const s of series) {
    if (s.fillTo !== undefined) {
      let d = '';
      s.values.forEach((v, i) => { if (isFinite(v)) d += (d ? 'L' : 'M') + x(i) + ' ' + y(v) + ' '; });
      const other = series.find(o => o.name === s.fillTo);
      if (other) {
        for (let i = other.values.length - 1; i >= 0; i--) {
          if (isFinite(other.values[i])) d += 'L' + x(i) + ' ' + y(other.values[i]) + ' ';
        }
        d += 'Z';
        svg.appendChild(el('path', { d, fill: s.fill || '#4da3ff', opacity: s.opacity ?? .1 }));
      }
    }
    let d = '', started = false;
    s.values.forEach((v, i) => {
      if (v === null || !isFinite(v)) { started = false; return; }
      d += (started ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
      started = true;
    });
    svg.appendChild(el('path', { d, fill: 'none', stroke: s.color || '#4da3ff',
                                 'stroke-width': s.width || 1.4,
                                 'stroke-dasharray': s.dash || null }));
  }
  if (opts.labels && opts.labels.length) {
    const step = Math.max(1, Math.floor(opts.labels.length / 7));
    for (let i = 0; i < opts.labels.length; i += step) {
      svg.appendChild(el('text', { x: x(i), y: h - 6, fill: '#5d6879', 'font-size': 9,
                                   'text-anchor': 'middle' }, opts.labels[i]));
    }
  }
  return svg;
}

/* ---------------------------------------------------------------- bars */
export function barChart(items, opts = {}) {
  const w = opts.width || 900;
  const rowH = opts.rowH || 20;
  const h = Math.max(40, items.length * rowH + 16);
  const labelW = opts.labelW || 170;
  const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, class: 'chart', height: h,
                          preserveAspectRatio: 'xMinYMin meet' });
  const vals = items.map(i => i.value).filter(isFinite);
  const max = Math.max(...vals.map(Math.abs), 1e-9);
  const zeroX = Math.min(...vals) < 0 ? labelW + (w - labelW - 60) / 2 : labelW;
  const scale = (w - labelW - 60) / (Math.min(...vals) < 0 ? max * 2 : max);
  items.forEach((it, i) => {
    const y = 8 + i * rowH;
    svg.appendChild(el('text', { x: 0, y: y + rowH / 2 + 3.5, fill: '#8a97ab',
                                 'font-size': 11 }, it.label));
    const len = (isFinite(it.value) ? it.value : 0) * scale;
    svg.appendChild(el('rect', {
      x: len >= 0 ? zeroX : zeroX + len, y: y + 3, width: Math.abs(len) || 1,
      height: rowH - 8, fill: it.color || (it.value >= 0 ? '#2f6d51' : '#6d3038'),
      rx: 2 }));
    svg.appendChild(el('text', {
      x: (len >= 0 ? zeroX + Math.abs(len) : zeroX + len) + (len >= 0 ? 6 : -6),
      y: y + rowH / 2 + 3.5, fill: '#d7dee9', 'font-size': 10.5,
      'font-family': 'monospace', 'text-anchor': len >= 0 ? 'start' : 'end' },
      it.display ?? fmt(it.value, opts.decimals ?? 2)));
  });
  return svg;
}

/* ----------------------------------------------------------- histogram */
export function histogram(values, opts = {}) {
  const w = opts.width || 900, h = opts.height || 200;
  const bins = opts.bins || 30;
  const clean = values.filter(v => isFinite(v));
  const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, class: 'chart', height: h,
                          preserveAspectRatio: 'none' });
  if (!clean.length) return svg;
  const lo = opts.min ?? Math.min(...clean), hi = opts.max ?? Math.max(...clean);
  const step = (hi - lo) / bins || 1;
  const counts = new Array(bins).fill(0);
  clean.forEach(v => {
    let b = Math.floor((v - lo) / step);
    if (b < 0) b = 0; if (b >= bins) b = bins - 1;
    counts[b]++;
  });
  const cmax = Math.max(...counts, 1);
  const padB = 20, padT = 8;
  const bw = w / bins;
  counts.forEach((c, i) => {
    const bh = (c / cmax) * (h - padB - padT);
    const mid = lo + (i + 0.5) * step;
    const r = el('rect', { x: i * bw + 1, y: h - padB - bh, width: bw - 2,
                           height: Math.max(0, bh),
                           fill: mid >= 0 ? '#2f6d51' : '#6d3038' });
    r.appendChild(el('title', {}, `${fmt(mid, 2)} : ${c}`));
    svg.appendChild(r);
  });
  if (lo < 0 && hi > 0) {
    const zx = ((0 - lo) / (hi - lo)) * w;
    svg.appendChild(el('line', { x1: zx, x2: zx, y1: padT, y2: h - padB, stroke: '#5d6879' }));
  }
  svg.appendChild(el('text', { x: 2, y: h - 6, fill: '#5d6879', 'font-size': 9 }, fmt(lo)));
  svg.appendChild(el('text', { x: w - 4, y: h - 6, fill: '#5d6879', 'font-size': 9,
                               'text-anchor': 'end' }, fmt(hi)));
  return svg;
}

export function sparkline(values, opts = {}) {
  const w = opts.width || 160, h = opts.height || 30;
  const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, class: 'chart spark', height: h });
  const clean = values.filter(v => isFinite(v));
  if (clean.length < 2) return svg;
  const lo = Math.min(...clean), hi = Math.max(...clean);
  const x = i => (i / (values.length - 1)) * w;
  const y = v => h - 2 - ((v - lo) / ((hi - lo) || 1)) * (h - 4);
  let d = '';
  values.forEach((v, i) => { if (isFinite(v)) d += (d ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' '; });
  svg.appendChild(el('path', { d, fill: 'none', stroke: opts.color ||
    (clean[clean.length - 1] >= clean[0] ? '#35c47a' : '#ff5c6c'), 'stroke-width': 1.3 }));
  return svg;
}

export { fmt };
