import { useCallback, useEffect, useRef, useState } from 'react'

// Drag-to-resize column widths, persisted to localStorage so they stick
// across sessions. `key` is the column id; widths are plain pixel numbers.
export function useResizableColumns(storageKey, defaultWidths) {
  const [widths, setWidths] = useState(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(storageKey) || '{}')
      return { ...defaultWidths, ...stored }
    } catch {
      return defaultWidths
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(widths))
    } catch {
      // localStorage unavailable (private mode, quota) — resize just won't persist
    }
  }, [storageKey, widths])

  const dragRef = useRef(null)

  const startResize = useCallback((key) => (e) => {
    e.preventDefault()
    e.stopPropagation()
    dragRef.current = { key, startX: e.clientX, startWidth: widths[key] || 120 }

    function onMove(ev) {
      if (!dragRef.current) return
      const delta = ev.clientX - dragRef.current.startX
      const next = Math.max(60, dragRef.current.startWidth + delta)
      setWidths((w) => ({ ...w, [dragRef.current.key]: next }))
    }
    function onUp() {
      dragRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [widths])

  return [widths, startResize]
}
