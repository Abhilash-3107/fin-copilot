import Amount from './Amount.jsx'

// The one way a month screen says how a figure moved. Renders nothing when
// there is no comparison (delta == null): not a dash, not "same as last month".
// `label` is the shared comparison wording ("October", "1-15 Oct").
export default function Delta({ delta, label, goodWhenDown = false, className = 'text-xs' }) {
  if (delta == null || !label) return null
  // Decide on the whole-rupee value that is rendered, so float noise never
  // draws a coloured "▲ ₹0".
  const rupees = Math.round(delta)
  if (rupees === 0) {
    return <span className={`${className} text-[#94a3b8]`}>same as {label}</span>
  }
  const up = rupees > 0
  const good = goodWhenDown ? !up : up
  return (
    <span className={`${className} tabular-nums ${good ? 'text-emerald-400' : 'text-red-400'}`}>
      {up ? '▲' : '▼'} <Amount value={Math.abs(rupees)} decimals={0} /> vs {label}
    </span>
  )
}

// What a verdict tile moved by, or null with no comparison.
export function tileDelta(verdict, key) {
  return verdict.prev ? verdict[key] - verdict.prev[key] : null
}
