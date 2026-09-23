// HCM brand mark: an H monogram whose crossbar rises left to right — the
// initial and a growth line in one stroke system. One geometry, rendered as
// full logo, icon-only, and day/night variants (colours come from theme
// tokens). Static SVG copies live in public/brand/.
export const BRAND = { name: 'HCM', tagline: 'Sales Growth Engine' }

const STROKES = ['M10.5 8.5 V23.5', 'M21.5 8.5 V23.5', 'M10.5 19 L21.5 13']

export function LogoMark({ size = 28, className = '', title = BRAND.name }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label={title} className={className}>
      <rect width="32" height="32" rx="8.5" fill="rgb(var(--primary))" />
      {STROKES.map((d) => (
        <path key={d} d={d} fill="none" stroke="rgb(var(--primary-foreground))" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
      ))}
    </svg>
  )
}

export default function Logo({ variant = 'full', size = 28, showTagline = true, className = '' }) {
  if (variant === 'icon') return <LogoMark size={size} className={className} />
  return (
    <div className={`flex items-center gap-2.5 ${className}`}>
      <LogoMark size={size} />
      <div className="leading-none">
        <p className="text-[15px] font-bold tracking-[0.04em] text-foreground">{BRAND.name}</p>
        {showTagline && <p className="mt-1 text-2xs text-muted-foreground">{BRAND.tagline}</p>}
      </div>
    </div>
  )
}
