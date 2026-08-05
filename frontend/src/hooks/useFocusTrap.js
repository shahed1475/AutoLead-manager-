import { useEffect, useRef } from 'react'

const FOCUSABLE = 'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'

// Traps Tab/Shift+Tab focus inside a modal/drawer while `active`, restores
// focus to whatever triggered it on close, and moves initial focus inside.
// Usage: const ref = useFocusTrap(isOpen); <div ref={ref} role="dialog">...
export function useFocusTrap(active) {
  const ref = useRef(null)
  const triggerRef = useRef(null)

  useEffect(() => {
    if (!active) return
    triggerRef.current = document.activeElement

    const node = ref.current
    const focusables = () => node ? Array.from(node.querySelectorAll(FOCUSABLE)) : []

    const first = focusables()[0]
    ;(first || node)?.focus?.()

    function onKeyDown(e) {
      if (e.key !== 'Tab' || !node) return
      const items = focusables()
      if (items.length === 0) return
      const firstEl = items[0]
      const lastEl  = items[items.length - 1]

      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault()
        lastEl.focus()
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault()
        firstEl.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      triggerRef.current?.focus?.()
    }
  }, [active])

  return ref
}
