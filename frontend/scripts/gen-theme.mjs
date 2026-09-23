// Generates src/theme.css — the design-system colour tokens for day and
// night. Run after changing anything here:   node scripts/gen-theme.mjs
//
// Two layers:
//  1. Semantic tokens (--background, --surface, --primary, --muted-foreground,
//     --success …) — what new UI should use (see tailwind.config.js).
//  2. Palette tokens (--slate-500, --emerald-300 …) behind every existing
//     Tailwind colour class, so older markup follows the same system:
//     - slate  = the warm graphite/ivory neutral, inverted for day;
//     - brand  = the single accent (pine); indigo is aliased to it;
//     - status hues (green/amber/red families) are kept but calmed;
//     - decorative hues (blue, violet, purple, pink, sky, cyan) are heavily
//       desaturated so they read as quiet category tags, not competing accents.
//     Day mode mirrors each accent's ends (200-400 text tints -> deep shades,
//     800-950 fills -> light tints) and keeps the solid 500-700 fills.
import { writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const tw = require('tailwindcss/colors')

const SHADES = ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950']

// ── Neutrals: warm graphite (night) / soft ivory (day), by Tailwind slot ──
const GRAPHITE = {
  50: '#F7F6F3', 100: '#EDECE8', 200: '#D9D7D1', 300: '#BAB7AF', 400: '#97948B',
  500: '#77746B', 600: '#58564F', 700: '#3D3B37', 800: '#2A2926', 900: '#1C1B19', 950: '#121211',
}
const IVORY = {
  50: '#111110', 100: '#1D1C19', 200: '#302E2A', 300: '#45423C', 400: '#5E5A52',
  500: '#78746B', 600: '#A9A59B', 700: '#DCD9D1', 800: '#EAE8E2', 900: '#FDFCFA', 950: '#F6F5F1',
}
// ── The one accent: pine ──
const PINE = {
  50: '#EFF6F2', 100: '#D8EAE0', 200: '#B3D5C3', 300: '#85BA9F', 400: '#5C9E7F',
  500: '#3F8264', 600: '#2F6A51', 700: '#275643', 800: '#214537', 900: '#1C392E', 950: '#0F2019',
}
const DAY_MAP = {
  50: '950', 100: '900', 200: '800', 300: '700', 400: '700', 500: '500',
  600: '600', 700: '700', 800: '200', 900: '100', 950: '50',
}

// Chroma multipliers (1 = Tailwind default).
const STATUS = { emerald: 0.8, green: 0.75, teal: 0.7, amber: 0.85, yellow: 0.8, orange: 0.8, red: 0.82, rose: 0.75 }
const DECORATIVE = { blue: 0.45, sky: 0.42, cyan: 0.4, violet: 0.4, purple: 0.4, pink: 0.4 }

// ── sRGB <-> OKLCH (for perceptually even desaturation) ──
const toLin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
const toGam = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055)
function hexToOklch(hex) {
  const n = parseInt(hex.slice(1), 16)
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => toLin(v / 255))
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
  return [L, Math.hypot(A, B), Math.atan2(B, A)]
}
function oklchToHex([L, C, H]) {
  const A = C * Math.cos(H), B = C * Math.sin(H)
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map((c) => Math.round(Math.min(1, Math.max(0, toGam(c))) * 255))
  return '#' + rgb.map((v) => v.toString(16).padStart(2, '0')).join('')
}
const calm = (scale, k) => Object.fromEntries(SHADES.map((s) => {
  const [L, C, H] = hexToOklch(scale[s])
  return [s, oklchToHex([L, C * k, H])]
}))

const rgb = (hex) => {
  const n = parseInt(hex.slice(1), 16)
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`
}

const night = [], day = []
const add = (name, nightScale, dayScale) => {
  for (const s of SHADES) {
    night.push(`  --${name}-${s}: ${rgb(nightScale[s])};`)
    day.push(`  --${name}-${s}: ${rgb(dayScale(s))};`)
  }
}
add('slate', GRAPHITE, (s) => IVORY[s])
add('brand', PINE, (s) => PINE[DAY_MAP[s]])
add('indigo', PINE, (s) => PINE[DAY_MAP[s]])
for (const [name, k] of Object.entries({ ...STATUS, ...DECORATIVE })) {
  const scale = calm(tw[name], k)
  add(name, scale, (s) => scale[DAY_MAP[s]])
}

// ── Semantic tokens (rgb triplets unless noted) ──
const semantic = (t) => Object.entries(t).map(([k, v]) =>
  `  --${k}: ${typeof v === 'string' && v.startsWith('#') ? rgb(v) : v};`).join('\n')

const NIGHT = {
  background: '#121211', foreground: '#EDECE8',
  sidebar: '#161615',
  surface: '#181817', 'surface-elevated': '#1F1E1C', 'surface-muted': '#1B1A19',
  border: '#2E2D2A', 'border-subtle': '#242321',
  primary: '#4A8C6D', 'primary-hover': '#579B7B', 'primary-foreground': '#FFFFFF',
  secondary: '#262523', 'secondary-foreground': '#EDECE8',
  muted: '#1F1E1C', 'muted-foreground': '#97948B',
  success: '#5FAE84', warning: '#D9A441', error: '#E0685F', info: '#7F9BB8',
  ring: '#5C9E7F',
  'shadow-sm': '0 1px 2px 0 rgb(0 0 0 / 0.35)',
  'shadow-md': '0 1px 0 0 rgb(255 255 255 / 0.03) inset, 0 8px 24px -12px rgb(0 0 0 / 0.6)',
  'shadow-lg': '0 1px 0 0 rgb(255 255 255 / 0.04) inset, 0 28px 60px -20px rgb(0 0 0 / 0.75)',
}
const DAY = {
  background: '#F6F5F1', foreground: '#1D1C19',
  sidebar: '#EFEEE9',
  surface: '#FDFCFA', 'surface-elevated': '#FFFFFF', 'surface-muted': '#F0EFEA',
  border: '#E1DED6', 'border-subtle': '#EAE8E2',
  primary: '#2F6A51', 'primary-hover': '#275643', 'primary-foreground': '#FFFFFF',
  secondary: '#ECEAE4', 'secondary-foreground': '#1D1C19',
  muted: '#F0EFEA', 'muted-foreground': '#6B675F',
  success: '#2E7D57', warning: '#A86A0C', error: '#B93A32', info: '#4A6C8C',
  ring: '#3F8264',
  'shadow-sm': '0 1px 2px 0 rgb(29 28 25 / 0.05)',
  'shadow-md': '0 1px 2px 0 rgb(29 28 25 / 0.04), 0 8px 24px -14px rgb(29 28 25 / 0.14)',
  'shadow-lg': '0 2px 6px 0 rgb(29 28 25 / 0.05), 0 32px 64px -24px rgb(29 28 25 / 0.28)',
}

const css = `/* GENERATED by scripts/gen-theme.mjs — edit that file, not this one. */
/* Night is the default; [data-theme="dark"] also forces night on a subtree
   (e.g. the terminal-style log console, which stays dark in day mode). */
:root, [data-theme="dark"] {
  color-scheme: dark;
${semantic(NIGHT)}
${night.join('\n')}
}

[data-theme="light"] {
  color-scheme: light;
${semantic(DAY)}
${day.join('\n')}
}
`
writeFileSync(new URL('../src/theme.css', import.meta.url), css)
console.log('theme.css written')
