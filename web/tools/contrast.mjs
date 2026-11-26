// WCAG contrast check of the M3 role pairs the UI actually uses. node tools/contrast.mjs
import fs from 'node:fs';
const css = fs.readFileSync(new URL('../src/tokens.css', import.meta.url), 'utf8');
const [light, dark] = [css.split('@media')[0], css.split('@media')[1]];
const parse = (b) => Object.fromEntries([...b.matchAll(/--md-([a-z-]+):\s*(#[0-9a-f]{6})/g)].map((m) => [m[1], m[2]]));
const lum = (h) => { const c = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const bgs = ['surface', 'surface-container-low', 'surface-container', 'surface-container-high', 'surface-container-highest'];
const pairs = [];
for (const r of ['primary', 'secondary', 'tertiary', 'error', 'success', 'warning']) { pairs.push([`on-${r}`, r]); pairs.push([`on-${r}-container`, `${r}-container`]); }
pairs.push(['inverse-on-surface', 'inverse-surface'], ['on-surface-variant', 'surface-variant']);
for (const b of bgs) pairs.push(['on-surface', b], ['on-surface-variant', b], ['primary', b], ['error', b], ['warning', b]);
// text roles that sit on container backgrounds
pairs.push(['on-surface', 'secondary-container'], ['on-secondary-container', 'secondary-container']);
let fail = 0;
for (const [name, t] of [['light', parse(light)], ['dark', parse(dark)]]) {
  console.log(`## ${name}`);
  for (const [fg, bg] of pairs) {
    if (!t[fg] || !t[bg]) continue;
    const r = ratio(t[fg], t[bg]); const ok = r >= 4.5;
    if (!ok) fail++;
    console.log(`${ok ? 'PASS' : 'FAIL'} ${r.toFixed(2).padStart(5)}  ${fg} on ${bg}`);
  }
}
console.log(fail ? `${fail} pair(s) below 4.5:1` : 'all pairs >= 4.5:1 (AA text)');
// non-text: outline vs surface needs 3:1
for (const [name, t] of [['light', parse(light)], ['dark', parse(dark)]]) console.log(`${name} outline on surface (non-text, needs 3): ${ratio(t.outline, t.surface).toFixed(2)}; primary on surface (focus ring): ${ratio(t.primary, t.surface).toFixed(2)}`);
