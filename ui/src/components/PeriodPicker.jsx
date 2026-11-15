import dayjs from 'dayjs'
import { usePeriod, ALL_TIME } from '../contexts/PeriodContext.jsx'

// The single month selector shared across Dashboard, Money Map and
// Transactions. Whatever the user picks follows them between those pages.
//
// Month screens (Dashboard, Money Map) pass allowAll={false}: they answer "how
// was this month", so "All time" is not offered, and if the shared period is
// All time when the user arrives they show the latest month and the picker
// says so, never a value the data does not honour.
export default function PeriodPicker({ className = '', allowAll = true }) {
  const { month, setMonth, months } = usePeriod()
  const chosen = month && month !== ALL_TIME ? month : null
  const value = allowAll
    ? (month ?? ALL_TIME)
    : (chosen && months.includes(chosen) ? chosen : (months[0] ?? ''))

  return (
    <select
      value={value}
      onChange={e => setMonth(e.target.value)}
      aria-label="Period"
      className={`bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1] cursor-pointer ${className}`}
    >
      {allowAll && <option value={ALL_TIME}>All time</option>}
      {months.map(m => (
        <option key={m} value={m}>{dayjs(m).format('MMM YYYY')}</option>
      ))}
    </select>
  )
}
