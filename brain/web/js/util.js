/* Shared helpers: fetching, formatting, and the evidence-label rendering that
   keeps every number on screen attached to how it was produced. */

export async function api(path, body) {
  const opts = body === undefined ? {}
    : { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); }
  catch { throw new Error(`${res.status} ${path}: ${text.slice(0, 400)}`); }
  if (!res.ok && data && data.error) throw new Error(data.error);
  return data;
}

export const h = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat(3)) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
};

export const num = (v, d = 2) =>
  (v === null || v === undefined || Number.isNaN(v) || v === Infinity || v === -Infinity)
    ? '--'
    : (typeof v === 'number' ? v.toFixed(d) : String(v));

export const pct = (v, d = 1) => v === null || v === undefined ? '--' : `${num(v, d)}%`;

export const money = (v, d = 0) => v === null || v === undefined ? '--'
  : (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString(undefined,
      { minimumFractionDigits: d, maximumFractionDigits: d });

export const signClass = v => (v === null || v === undefined) ? '' : (v > 0 ? 'up' : v < 0 ? 'down' : '');

export function stat(k, v, s, cls) {
  return h('div', { class: 'stat' },
    h('span', { class: 'k' }, k),
    h('span', { class: 'v ' + (cls || '') }, v),
    s ? h('span', { class: 's' }, s) : null);
}

export function pill(text, kind) {
  return h('span', { class: 'pill ' + (kind || '') }, text);
}

export function evidenceTag(cls) {
  return h('span', { class: 'evidence ' + cls, title: cls.replace(/_/g, ' ') },
           cls.replace(/_/g, ' '));
}

/** Render a Claim dict exactly as the backend produced it -- statement, evidence
 *  class, sample size, interval, method and every caveat. Nothing is dropped. */
export function claimBlock(c) {
  if (!c) return null;
  const meta = [];
  if (c.sample_size !== null && c.sample_size !== undefined) meta.push(`N = ${c.sample_size}`);
  if (c.confidence_interval) meta.push(`95% CI [${num(c.confidence_interval[0], 3)}, ${num(c.confidence_interval[1], 3)}]`);
  if (c.period) meta.push(`period ${c.period}`);
  if (c.data_source) meta.push(`data ${c.data_source} (${c.data_origin})`);
  if (c.definition_of_success) meta.push(`success = ${c.definition_of_success}`);
  return h('div', { class: 'claim' },
    h('div', {}, evidenceTag(c.evidence), c.statement),
    meta.length ? h('div', { class: 'meta mono' }, meta.join('  ·  ')) : null,
    c.methodology ? h('div', { class: 'meta' }, 'Method: ' + c.methodology) : null,
    (c.caveats || []).length
      ? h('ul', { class: 'bul meta' }, (c.caveats || []).map(x => h('li', {}, x)))
      : null);
}

export function banner(text, kind = '') {
  return h('div', { class: 'banner ' + kind }, text);
}

export function table(cols, rows, opts = {}) {
  const t = h('table', {},
    h('thead', {}, h('tr', {}, cols.map(c =>
      h('th', { class: c.num ? 'num' : '', title: c.title || '' }, c.label)))),
    h('tbody', {}, rows.map(r =>
      h('tr', { onclick: opts.onRow ? () => opts.onRow(r) : null,
                style: opts.onRow ? 'cursor:pointer' : '' },
        cols.map(c => {
          const v = typeof c.get === 'function' ? c.get(r) : r[c.key];
          return h('td', { class: (c.num ? 'num ' : '') + (c.cls ? c.cls(r) : '') },
                   v instanceof Node ? v : (v === null || v === undefined ? '--' : String(v)));
        })))));
  return h('div', { class: 'scroll-x' + (opts.scrollY ? ' scroll-y' : '') }, t);
}

export function details(summary, contentFn) {
  const body = h('div', { class: 'body' });
  const d = h('details', {}, h('summary', {}, summary), body);
  let built = false;
  d.addEventListener('toggle', () => {
    if (d.open && !built) { built = true; body.appendChild(contentFn()); }
  });
  return d;
}

export function jsonBlock(obj) {
  return h('pre', { class: 'json' }, JSON.stringify(obj, null, 2));
}

export function field(label, input) {
  return h('div', { class: 'field' }, h('label', {}, label), input);
}

export function input(id, value, attrs = {}) {
  return h('input', { id, value: value ?? '', ...attrs });
}

export function select(id, options, value) {
  return h('select', { id }, options.map(o =>
    h('option', { value: o.value ?? o, selected: (o.value ?? o) === value ? 'selected' : null },
      o.label ?? o)));
}

export const val = id => document.getElementById(id)?.value ?? '';
export const numVal = (id, d = null) => {
  const v = parseFloat(val(id));
  return Number.isFinite(v) ? v : d;
};

export function loading(msg = 'working…') {
  return h('div', { class: 'loading' }, msg);
}

export function errorBox(e) {
  return h('div', { class: 'err' }, String(e && e.message ? e.message : e));
}
