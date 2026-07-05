import dayjs from 'dayjs'
import { usePeriod, ALL_TIME } from '../contexts/PeriodContext.jsx'

// The single month selector shared across Dashboard, Money Map and
// Transactions. Whatever the user picks follows them between those pages.
export default function PeriodPicker({ className = '' }) {
  const { month, setMonth, months } = usePeriod()

  return (
    <select
      value={month ?? ALL_TIME}
      onChange={e => setMonth(e.target.value)}
      aria-label="Period"
      className={`bg-[#13151f] border border-[#2d3148] text-[#e2e8f0] px-2.5 py-1.5 rounded-md text-sm focus:outline-none focus:border-[#6366f1] cursor-pointer ${className}`}
    >
      <option value={ALL_TIME}>All time</option>
      {months.map(m => (
        <option key={m} value={m}>{dayjs(m).format('MMM YYYY')}</option>
      ))}
    </select>
  )
}
