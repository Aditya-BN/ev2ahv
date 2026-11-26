// Generates src/tokens.css colour roles from the brand seed. Run in a scratch dir (not a project dependency):
//   npm i @material/material-color-utilities@0.3.0 && node gen-m3-tokens.cjs | node -e "..." (see docs/ui-design.md)
// Prints JSON {light, dark} of CSS custom properties.
const { argbFromHex, themeFromSourceColor, hexFromArgb, Blend, TonalPalette, Hct } = require('@material/material-color-utilities');
const seed = '#0B7F86';
const t = themeFromSourceColor(argbFromHex(seed), [
  { name: 'success', value: argbFromHex('#2E8B3D'), blend: true },
  { name: 'warning', value: argbFromHex('#C77700'), blend: true },
]);
const kebab = (s) => s.replace(/[A-Z]/g, (m) => '-' + m.toLowerCase());
const out = {};
for (const mode of ['light', 'dark']) {
  const s = t.schemes[mode].toJSON();
  let css = '';
  for (const [k, v] of Object.entries(s)) css += `--md-${kebab(k)}:${hexFromArgb(v)};`;
  for (const c of t.customColors) {
    const g = c.color.name; const cc = c[mode];
    css += `--md-${g}:${hexFromArgb(cc.color)};--md-on-${g}:${hexFromArgb(cc.onColor)};--md-${g}-container:${hexFromArgb(cc.colorContainer)};--md-on-${g}-container:${hexFromArgb(cc.onColorContainer)};`;
  }
  out[mode] = css;
}
// surface container tones (not in this util version's scheme)
const n = t.palettes.neutral;
for (const mode of ['light','dark']) {
  const tones = mode==='light' ? {'surface-container-lowest':100,'surface-container-low':96,'surface-container':94,'surface-container-high':92,'surface-container-highest':90,'surface-dim':87,'surface-bright':98}
    : {'surface-container-lowest':4,'surface-container-low':10,'surface-container':12,'surface-container-high':17,'surface-container-highest':22,'surface-dim':6,'surface-bright':24};
  for (const [k,v] of Object.entries(tones)) out[mode]+=`--md-${k}:${hexFromArgb(n.tone(v))};`;
}
console.log(JSON.stringify(out));
