import { useEffect, useRef, useState } from 'react'

// Eases a displayed number toward `value` over `duration` ms instead of
// snapping — used for live campaign counters so updates feel alive rather
// than jumpy on every poll tick.
export function useAnimatedNumber(value, duration = 400) {
  const target = Number(value) || 0
  const [display, setDisplay] = useState(target)
  const fromRef = useRef(target)
  const rafRef  = useRef(null)

  useEffect(() => {
    const from = fromRef.current
    if (from === target) return

    const start = performance.now()
    function tick(now) {
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - t, 3)
      setDisplay(Math.round(from + (target - from) * eased))
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        fromRef.current = target
      }
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [target, duration])

  return display
}
