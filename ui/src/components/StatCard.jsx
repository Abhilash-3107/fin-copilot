export default function StatCard({ label, value, sub, accentClass }) {
  return (
    <div className="bg-[#13151f] border border-[#2d3148] rounded-xl p-5">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b] mb-2">{label}</p>
      <p className={`text-2xl font-bold tabular-nums ${accentClass ?? 'text-[#e2e8f0]'}`}>{value}</p>
      {sub && <p className="text-xs text-[#64748b] mt-1">{sub}</p>}
    </div>
  )
}
