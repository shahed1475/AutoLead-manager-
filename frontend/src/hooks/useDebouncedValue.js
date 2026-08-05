import { useEffect, useState } from 'react'

// Delays propagating `value` until it's stopped changing for `delay` ms —
// used on free-text filter inputs so they don't fire an API call per keystroke.
export function useDebouncedValue(value, delay = 350) {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])

  return debounced
}
