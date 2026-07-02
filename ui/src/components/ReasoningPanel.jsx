function pct(x) {
  return x == null ? '—' : `${Math.round(x * 100)}%`
}
function num(x, d = 3) {
  return x == null ? '—' : Number(x).toFixed(d)
}

// Dev-mode only: shows why the pipeline chose this category — the RAG neighbours,
// the similarity/vote math, and the LLM's one-sentence reasoning. Gated upstream by
// DEV_MODE; the backend only attaches `reasoning` when that flag is on.
export default function ReasoningPanel({ reasoning }) {
  // No trace captured for this annotation (e.g. categorized while dev mode was
  // off) → show nothing rather than an empty panel.
  if (!reasoning) return null
  const r = reasoning
  return (
    <details className="bg-[#0f0f1a] border-t border-[#2d3148] group">
      <summary className="px-6 py-3 cursor-pointer text-[10px] font-semibold uppercase tracking-wider text-[#64748b] hover:text-[#94a3b8] select-none">
        Why this annotation?
        <span className="ml-2 normal-case font-normal text-[#475569]">
          dev · {r.stage}
        </span>
      </summary>
      <div className="px-6 pb-4 space-y-4 text-xs text-[#94a3b8]">
        {/* Neighbours */}
        {r.neighbours?.length > 0 && (
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-[#475569] mb-1.5">
              Neighbours ({r.neighbours.length})
            </p>
            <div className="space-y-1">
              {r.neighbours.map(n => (
                <div key={n.transaction_id} className="flex items-center justify-between gap-3">
                  <span className="truncate">
                    <span className="text-[#a5b4fc]">{n.category ?? '—'}</span>
                    {n.source && <span className="text-[#475569]"> · {n.source}</span>}
                    {n.raw_description && (
                      <span className="text-[#64748b]"> · {n.raw_description.slice(0, 40)}</span>
                    )}
                  </span>
                  <span className="tabular-nums text-[#cbd5e1] shrink-0">
                    sim {num(n.similarity, 3)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Math */}
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#475569] mb-1.5">Math</p>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 tabular-nums">
            {r.best_similarity != null && (
              <div className="flex justify-between"><span>best similarity</span><span className="text-[#cbd5e1]">{num(r.best_similarity, 3)}</span></div>
            )}
            {r.agreement_factor != null && (
              <div className="flex justify-between"><span>agreement factor</span><span className="text-[#cbd5e1]">{num(r.agreement_factor, 3)}</span></div>
            )}
            {r.margin_factor != null && (
              <div className="flex justify-between"><span>margin factor</span><span className="text-[#cbd5e1]">{num(r.margin_factor, 3)}</span></div>
            )}
            {r.vote_category != null && (
              <div className="flex justify-between"><span>vote</span><span className="text-[#cbd5e1]">{r.vote_category} @ {pct(r.vote_share)}</span></div>
            )}
            {r.trusted_weight != null && (
              <div className="flex justify-between"><span>trusted weight</span><span className="text-[#cbd5e1]">{num(r.trusted_weight, 2)}</span></div>
            )}
            {r.raw_confidence != null && (
              <div className="flex justify-between"><span>raw → final conf</span><span className="text-[#cbd5e1]">{pct(r.raw_confidence)} → {pct(r.final_confidence)}</span></div>
            )}
            {r.dampening_factor != null && (
              <div className="flex justify-between"><span>dampening</span><span className="text-[#cbd5e1]">×{num(r.dampening_factor, 3)}</span></div>
            )}
          </div>
          {r.caps_applied?.length > 0 && (
            <p className="mt-1.5 text-[#f59e0b]">caps applied: {r.caps_applied.join(', ')}</p>
          )}
        </div>

        {/* LLM why / rule */}
        {r.llm_reasoning && (
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-[#475569] mb-1.5">LLM reasoning</p>
            <p className="text-[#cbd5e1] italic">“{r.llm_reasoning}”</p>
          </div>
        )}
        {r.stage === 'rule' && r.matched_rule && (
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-[#475569] mb-1.5">Matched rule</p>
            <p className="text-[#cbd5e1]">{r.matched_rule}</p>
          </div>
        )}
      </div>
    </details>
  )
}
