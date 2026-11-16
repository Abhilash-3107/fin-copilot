import { describe, it, expect } from 'vitest'

// Small text must be readable (WCAG AA, 4.5:1) on the dark surfaces. Two greys
// and the accent blue failed that bar wherever they were used as text, so they
// may not be used as text again. They are still fine for borders and fills,
// which this does not touch.
const FORBIDDEN_TEXT = ['475569', '64748b', '6366f1']
const SURFACES = ['0f1117', '13151f', '1a1d27', '1e2235']
const TEXT_GREY = '94a3b8'
const TEXT_ACCENT = '818cf8'

function luminance(hex) {
  const [r, g, b] = [0, 2, 4].map(i => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const sources = import.meta.glob(['../**/*.{js,jsx,css}', '!../**/*.test.{js,jsx}', '!../test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

describe('text contrast', () => {
  it('knows which greys fail and which pass on every surface', () => {
    for (const surface of SURFACES) {
      expect(ratio(TEXT_GREY, surface)).toBeGreaterThanOrEqual(4.5)
      expect(ratio(TEXT_ACCENT, surface)).toBeGreaterThanOrEqual(4.5)
    }
    // The reason for the rule: each forbidden colour fails on at least one surface.
    for (const hex of FORBIDDEN_TEXT) {
      expect(Math.min(...SURFACES.map(s => ratio(hex, s)))).toBeLessThan(4.5)
    }
  })

  it('finds the sources it is meant to check', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(40)
    expect(Object.keys(sources)).toContain('../index.css')
  })

  it('uses none of the failing colours as text', () => {
    const hexes = FORBIDDEN_TEXT.join('|')
    const asText = [
      // Tailwind text colours, with any variant (hover:, placeholder:, marker:).
      new RegExp(`text-\\[#(${hexes})\\]`, 'i'),
      // Chart.js axis and legend colours, and inline styles.
      new RegExp(`\\bcolor:\\s*['"\`]#(${hexes})['"\`]`, 'i'),
      // The theme tokens meant for text.
      new RegExp(`--color-(muted|text[\\w-]*):\\s*#(${hexes})`, 'i'),
    ]
    // White text on the accent fill: #6366f1 gives 4.47:1, just under the bar. The deeper indigo clears it.
    asText.push(/bg-\[#6366f1\].*text-white|text-white.*bg-\[#6366f1\]/i)
    const offenders = Object.entries(sources).flatMap(([file, text]) =>
      text.split('\n').flatMap((line, i) =>
        asText.some(re => re.test(line)) ? [`${file}:${i + 1} ${line.trim().slice(0, 100)}`] : []))
    expect(offenders).toEqual([])
  })
})
