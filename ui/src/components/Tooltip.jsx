import { useState, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'

const GAP = 8

function getTooltipStyle(rect, position) {
  if (!rect) return {}
  const scrollX = window.scrollX
  const scrollY = window.scrollY
  switch (position) {
    case 'bottom':
      return { top: rect.bottom + scrollY + GAP, left: rect.left + scrollX + rect.width / 2 }
    case 'left':
      return { top: rect.top + scrollY + rect.height / 2, left: rect.left + scrollX - GAP }
    case 'right':
      return { top: rect.top + scrollY + rect.height / 2, left: rect.right + scrollX + GAP }
    case 'top':
    default:
      return { top: rect.top + scrollY - GAP, left: rect.left + scrollX + rect.width / 2 }
  }
}

const TRANSFORM = {
  top: 'translateX(-50%) translateY(-100%)',
  bottom: 'translateX(-50%)',
  left: 'translateX(-100%) translateY(-50%)',
  right: 'translateY(-50%)',
}

export default function Tooltip({ content, position = 'top', delay = 300, children }) {
  const [visible, setVisible] = useState(false)
  const [rect, setRect] = useState(null)
  const wrapperRef = useRef(null)
  const timer = useRef(null)

  const show = useCallback(() => {
    timer.current = setTimeout(() => {
      if (wrapperRef.current) {
        setRect(wrapperRef.current.getBoundingClientRect())
      }
      setVisible(true)
    }, delay)
  }, [delay])

  const hide = useCallback(() => {
    clearTimeout(timer.current)
    setVisible(false)
  }, [])

  const style = getTooltipStyle(rect, position)

  return (
    <span ref={wrapperRef} className="inline-flex" onMouseEnter={show} onMouseLeave={hide}>
      {children}
      {visible && rect && createPortal(
        <div
          className="fixed z-[9999] px-2.5 py-1.5 bg-[#1a1d27] border border-[#2d3148] text-[#e2e8f0] text-xs rounded-md shadow-lg shadow-black/40 max-w-[250px] break-words whitespace-normal animate-tooltip-in pointer-events-none"
          style={{ top: style.top, left: style.left, transform: TRANSFORM[position] }}
        >
          {content}
        </div>,
        document.body
      )}
    </span>
  )
}
