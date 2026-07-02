function pct(x) {
  return x == null ? '—' : `${Math.round(x * 100)}%`
}
function num(x, d = 3) {
  return x == null ? '—' : Number(x).toFixed(d)
}

// A measured value with the threshold it was gated against, e.g. "0.893 (≥ 0.92)".
function VsThreshold({ value, cmp, threshold, d = 3 }) {
  return (
    <span className="text-[#cbd5e1]">
      {num(value, d)}
      {threshold != null && (
        <span className="text-[#64748b]"> ({cmp} {num(threshold, d)})</span>
      )}
    </span>
  )
}

function SectionLabel({ children }) {
  return (
    <p className="text-[10px] font-semibold uppercase tracking-wider text-[#475569] mb-1.5">
      {children}
    </p>
  )
}

function Row({ label, children }) {
  return (
    <div className="flex justify-between gap-4">
      <span>{label}</span>
      {children}
    </div>
  )
}

// Dev-mode only: shows why the pipeline chose this category — the routing path,
// the RAG neighbours, the similarity/vote math with the thresholds each value was
// gated against (snapshotted at annotation time), the few-shot prompt content, the
// counterparty prior, and the LLM call telemetry. Traces are always captured; the
// backend only *returns* `reasoning` when dev mode is on.
export default function ReasoningPanel({ reasoning }) {
  // No trace captured for this annotation (row predates trace capture) → show
  // nothing rather than an empty panel.
  if (!reasoning) return null
  const r = reasoning
  const t = r.thresholds ?? {}
  return (
    <details className="bg-[#0f0f1a] border-t border-[#2d3148] group">
      <summary className="px-6 py-3 cursor-pointer text-[10px] font-semibold uppercase tracking-wider text-[#64748b] hover:text-[#94a3b8] select-none">
        Why this annotation?
        <span className="ml-2 normal-case font-normal text-[#475569]">
          dev · {r.stage}
        </span>
        {r.prompt_truncated && (
          <span className="ml-2 normal-case font-semibold text-[#f87171]">
            prompt truncated
          </span>
        )}
      </summary>
      <div className="px-6 pb-4 space-y-4 text-xs text-[#94a3b8]">
        {/* Routing path: why earlier stages fell through before this one decided */}
        {r.skips?.length > 0 && (
          <div>
            <SectionLabel>Path</SectionLabel>
            <ol className="space-y-0.5">
              {r.skips.map((s, i) => (
                <li key={i} className="text-[#64748b]">↳ {s}</li>
              ))}
              <li className="text-[#a5b4fc]">→ decided by {r.stage}</li>
            </ol>
          </div>
        )}

        {/* Neighbours (deduped vote donors) */}
        {r.neighbours?.length > 0 && (
          <div>
            <SectionLabel>Neighbours ({r.neighbours.length})</SectionLabel>
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

        {/* Math — each value next to the threshold it was gated against */}
        <div>
          <SectionLabel>Math</SectionLabel>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 tabular-nums">
            {r.best_similarity != null && (
              <Row label="best similarity">
                <VsThreshold
                  value={r.best_similarity}
                  cmp="direct ≥"
                  threshold={t.rag_direct_threshold ?? t.rag_knn_similarity_floor}
                />
              </Row>
            )}
            {r.agreement_factor != null && (
              <Row label="agreement factor"><span className="text-[#cbd5e1]">{num(r.agreement_factor, 3)}</span></Row>
            )}
            {r.margin_factor != null && (
              <Row label="margin factor"><span className="text-[#cbd5e1]">{num(r.margin_factor, 3)}</span></Row>
            )}
            {r.vote_category != null && (
              <Row label="vote">
                <span className="text-[#cbd5e1]">
                  {r.vote_category} @ {pct(r.vote_share)}
                  {t.rag_consensus_floor != null && (
                    <span className="text-[#64748b]"> (floor {pct(t.rag_consensus_floor)})</span>
                  )}
                </span>
              </Row>
            )}
            {r.trusted_weight != null && (
              <Row label="trusted weight">
                <VsThreshold value={r.trusted_weight} cmp="≥" threshold={t.rag_knn_min_trusted_weight} d={2} />
              </Row>
            )}
            {r.raw_confidence != null && (
              <Row label="raw → final conf">
                <span className="text-[#cbd5e1]">{pct(r.raw_confidence)} → {pct(r.final_confidence)}</span>
              </Row>
            )}
            {r.dampening_factor != null && (
              <Row label="dampening">
                <span className="text-[#cbd5e1]">
                  ×{num(r.dampening_factor, 3)}
                  {r.calibration_bucket && (
                    <span className="text-[#64748b]"> ({r.calibration_bucket})</span>
                  )}
                </span>
              </Row>
            )}
            {t.confidence_threshold != null && (
              <Row label="review threshold">
                <span className={r.final_confidence < t.confidence_threshold ? 'text-[#f59e0b]' : 'text-[#cbd5e1]'}>
                  {pct(r.final_confidence)} {r.final_confidence < t.confidence_threshold ? '<' : '≥'} {pct(t.confidence_threshold)}
                  {r.final_confidence < t.confidence_threshold && ' → review'}
                </span>
              </Row>
            )}
          </div>
          {r.caps_applied?.length > 0 && (
            <p className="mt-1.5 text-[#f59e0b]">caps applied: {r.caps_applied.join(', ')}</p>
          )}
        </div>

        {/* Counterparty recurrence prior */}
        {r.counterparty_prior_category != null && (
          <div>
            <SectionLabel>Counterparty prior</SectionLabel>
            <p className="text-[#cbd5e1] tabular-nums">
              {r.counterparty_prior_category}
              {r.counterparty_prior_probability != null && <> @ {pct(r.counterparty_prior_probability)}</>}
              {r.counterparty_prior_n != null && (
                <span className="text-[#64748b]">
                  {' '}· n={r.counterparty_prior_n}
                  {t.counterparty_min_observations != null && <> (min {t.counterparty_min_observations})</>}
                </span>
              )}
              {r.counterparty_prior_effect && r.counterparty_prior_effect !== 'neutral' && (
                <span className="ml-2 text-[#f59e0b]">→ {r.counterparty_prior_effect}</span>
              )}
            </p>
          </div>
        )}

        {/* Few-shot examples as the LLM saw them */}
        {r.prompt_examples?.length > 0 && (
          <div>
            <SectionLabel>
              Prompt examples ({r.prompt_examples.length})
              {r.majority_category && (
                <span className="ml-2 normal-case font-normal text-[#64748b]">
                  hint: {r.majority_count} × {r.majority_category}
                </span>
              )}
            </SectionLabel>
            <div className="space-y-1">
              {r.prompt_examples.map((e, i) => (
                <div key={i} className="flex items-center justify-between gap-3">
                  <span className="truncate text-[#64748b]">{e.raw_description?.slice(0, 48) ?? '—'}</span>
                  <span className="shrink-0">
                    <span className="text-[#a5b4fc]">{e.category}{e.subcategory ? ` > ${e.subcategory}` : ''}</span>
                    {e.source && <span className="text-[#475569]"> · {e.source}</span>}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* LLM why + call telemetry */}
        {r.llm_reasoning && (
          <div>
            <SectionLabel>LLM reasoning</SectionLabel>
            <p className="text-[#cbd5e1] italic">“{r.llm_reasoning}”</p>
          </div>
        )}
        {(r.llm_model || r.prompt_tokens != null || r.logprob_confidence != null) && (
          <div>
            <SectionLabel>LLM call</SectionLabel>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1 tabular-nums">
              {r.llm_model && (
                <Row label="model"><span className="text-[#cbd5e1]">{r.llm_model}</span></Row>
              )}
              {r.prompt_tokens != null && (
                <Row label="prompt tokens">
                  <span className={r.prompt_truncated ? 'text-[#f87171]' : 'text-[#cbd5e1]'}>
                    {r.prompt_tokens}{r.prompt_truncated && ' — likely truncated'}
                  </span>
                </Row>
              )}
              {r.verbalized_confidence != null && r.logprob_confidence != null && (
                <Row label="verbalized vs logprob">
                  <span className="text-[#cbd5e1]">{pct(r.verbalized_confidence)} vs {num(r.logprob_confidence, 3)}</span>
                </Row>
              )}
            </div>
          </div>
        )}

        {/* Embed text: the exact string that was embedded for retrieval */}
        {r.embed_text && (
          <div>
            <SectionLabel>Embed text</SectionLabel>
            <p className="font-mono text-[11px] text-[#64748b] break-all bg-[#161625] rounded px-2 py-1.5">
              {r.embed_text}
            </p>
          </div>
        )}

        {/* rule path */}
        {(r.stage === 'rule' || r.stage === 'learned_rule') && r.matched_rule && (
          <div>
            <SectionLabel>Matched rule</SectionLabel>
            <p className="text-[#cbd5e1]">{r.matched_rule}</p>
          </div>
        )}
      </div>
    </details>
  )
}
