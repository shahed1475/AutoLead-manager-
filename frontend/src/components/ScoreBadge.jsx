import { Flame, Sun, Snowflake } from 'lucide-react'
import clsx from 'clsx'

const CONFIGS = {
  HOT:  { icon: Flame,     bg: 'bg-red-500/15',   text: 'text-red-400',   border: 'border-red-500/30'   },
  WARM: { icon: Sun,       bg: 'bg-amber-500/15', text: 'text-amber-400', border: 'border-amber-500/30' },
  COLD: { icon: Snowflake, bg: 'bg-blue-500/15',  text: 'text-blue-400',  border: 'border-blue-500/30'  },
}

export default function ScoreBadge({ score, label, showScore = true, size = 'sm' }) {
  const key = (label || 'COLD').toUpperCase()
  const cfg = CONFIGS[key] || CONFIGS.COLD
  const Icon = cfg.icon

  const iconSize = size === 'xs' ? 10 : 12
  const textCls  = size === 'xs' ? 'text-[9px]' : 'text-[10px]'

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border font-semibold',
        cfg.bg, cfg.text, cfg.border, textCls,
      )}
      title={`Score: ${score ?? 0}/100`}
    >
      <Icon size={iconSize} />
      {key}
      {showScore && score != null && (
        <span className="opacity-60 font-normal">{score}</span>
      )}
    </span>
  )
}
