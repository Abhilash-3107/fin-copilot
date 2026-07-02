import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { useToast } from '../contexts/ToastContext.jsx'

// Per-stage accent colours — one hue per pipeline stage, reused across the funnel
// and any stage tags so a stage reads the same everywhere.
const STAGE_COLOR = {
  rule: '#22d3ee',
  learned_rule: '#4ade80',
  rag_direct: '#a5b4fc',
  rag_knn: '#818cf8',
  rag_prompted: '#c084fc',
  llm: '#f59e0b',
}
const STAGE_LABEL = {
  rule: 'Rule',
  learned_rule: 'Learned rule',
  rag_direct: 'RAG direct',
  rag_knn: 'RAG kNN',
  rag_prompted: 'RAG prompted',
  llm: 'Plain LLM',
}

function pct(x) {
  return x == null ? '—' : `${Math.round(x * 100)}%`
}

// A stacked bar per stage: auto-accepted vs routed-to-review, widths proportional
// to the whole corpus so the row lengths read as coverage.
function StageFunnel({ funnel, total }) {
  if (!funnel.length) return null
  return (
    <div className="space-y-2">
      {funnel.map(s => {
        const color = STAGE_COLOR[s.stage] ?? '#64748b'
        const widthPct = total ? (s.count / total) * 100 : 0
        const reviewFrac = s.count ? s.review / s.count : 0
        return (
          <div key={s.stage} className="flex items-center gap-3 text-xs">
            <span className="w-28 shrink-0 text-[#94a3b8]">{STAGE_LABEL[s.stage] ?? s.stage}</span>
            <div className="flex-1 h-5 rounded bg-[#0f0f1a] overflow-hidden flex" title={`${s.count} total`}>
              <div
                className="h-full flex items-center"
                style={{ width: `${widthPct}%` }}
              >
                <div className="h-full" style={{ width: `${(1 - reviewFrac) * 100}%`, backgroundColor: color }} />
                <div className="h-full" style={{ width: `${reviewFrac * 100}%`, backgroundColor: color, opacity: 0.3 }} />
              </div>
            </div>
            <span className="w-24 shrink-0 text-right tabular-nums text-[#cbd5e1]">
              {s.count}
              <span className="text-[#475569]"> ({s.review} rev)</span>
            </span>
          </div>
        )
      })}
    </div>
  )
}

// A binned histogram over [0,1] with vertical threshold markers. Bars below any
// threshold line are dimmed so the "would auto-accept / would clear the gate"
// split is visible at a glance.
function ThresholdHistogram({ hist, markers, accent = '#a5b4fc' }) {
  if (!hist || hist.n === 0) return null
  const max = Math.max(...hist.counts, 1)
  const binW = 1 / hist.bins
  return (
    <div>
      <div className="relative flex items-end gap-px h-24">
        {hist.counts.map((c, i) => {
          const binMid = (i + 0.5) * binW
          // Dim a bar if it sits left of the *highest* marker (i.e. below the gate).
          const gate = Math.max(...markers.map(m => m.value))
          const belowGate = binMid < gate
          return (
            <div
              key={i}
              className="flex-1 rounded-t transition-colors"
              style={{
                height: `${(c / max) * 100}%`,
                minHeight: c > 0 ? '2px' : '0',
                backgroundColor: accent,
                opacity: belowGate ? 0.3 : 0.85,
              }}
              title={`${(i * binW).toFixed(2)}–${((i + 1) * binW).toFixed(2)}: ${c}`}
            />
          )
        })}
        {/* Threshold marker lines, positioned by fraction across the [0,1] axis */}
        {markers.map(m => (
          <div
            key={m.label}
            className="absolute top-0 bottom-0 border-l border-dashed pointer-events-none"
            style={{ left: `${m.value * 100}%`, borderColor: m.color }}
          />
        ))}
      </div>
      {/* Axis + marker legend */}
      <div className="flex justify-between text-[10px] text-[#475569] mt-1 tabular-nums">
        <span>0.0</span>
        <span>0.5</span>
        <span>1.0</span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1.5">
        {markers.map(m => (
          <span key={m.label} className="text-[10px] flex items-center gap-1">
            <span className="inline-block w-2 border-t border-dashed" style={{ borderColor: m.color }} />
            <span className="text-[#94a3b8]">{m.label}</span>
            <span className="tabular-nums text-[#64748b]">{m.value.toFixed(2)}</span>
          </span>
        ))}
      </div>
      <p className="text-[10px] text-[#475569] mt-1">{hist.n} annotations with this signal</p>
    </div>
  )
}

function NearMissList({ title, items, band, gate, valueKey, gateLabel }) {
  if (!items.length) return null
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wider text-[#475569] mb-1.5">
        {title} ({items.length})
      </p>
      <p className="text-[11px] text-[#64748b] mb-2 leading-relaxed">
        Within {band.toFixed(2)} of {gateLabel} ({gate.toFixed(2)}) — lowering that
        threshold slightly would flip these.
      </p>
      <div className="space-y-1">
        {items.map(m => (
          <div key={m.transaction_id} className="flex items-center justify-between gap-3 text-xs">
            <span className="truncate text-[#94a3b8]">
              <span className="text-[#a5b4fc]">{m.category}</span>
              <span className="text-[#475569]"> · {m.source}</span>
              {m.raw_description && <span className="text-[#64748b]"> · {m.raw_description.slice(0, 44)}</span>}
            </span>
            <span className="shrink-0 tabular-nums text-[#f59e0b]">{Number(m[valueKey]).toFixed(3)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// Dev-mode only: run-level aggregation over stored reasoning traces. Shows where
// the corpus sits relative to the pipeline's thresholds (stage mix, similarity /
// confidence distributions, near-miss candidates) — the view that informs how the
// knobs might be set, complementing the per-annotation "Why this annotation?" panel.
export default function RunSummary() {
  const toast = useToast()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/annotations/run-summary')
      .then(setData)
      .catch(e => {
        // 404 = dev mode off; the section just won't render (parent gates it too).
        if (e.status !== 404) toast(`Couldn't load run summary — ${e.message}`, 'error')
      })
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <p className="px-5 py-4 text-sm text-[#64748b]">Loading run summary…</p>
  }
  if (!data || data.total === 0) {
    return (
      <p className="px-5 py-4 text-sm text-[#64748b]">
        No annotated transactions yet. Run auto-annotate to populate this.
      </p>
    )
  }

  const simMarkers = [
    { label: 'novelty floor', value: data.similarity.thresholds.rag_similarity_floor, color: '#64748b' },
    { label: 'RAG-direct', value: data.similarity.thresholds.rag_direct_threshold, color: '#4ade80' },
  ]
  const confMarkers = [
    { label: 'review threshold', value: data.confidence.thresholds.confidence_threshold, color: '#f59e0b' },
  ]

  return (
    <div className="px-5 py-5 space-y-6">
      {/* Headline */}
      <div className="flex items-baseline gap-4 flex-wrap">
        <span className="text-2xl font-semibold text-[#e2e8f0] tabular-nums">{data.total}</span>
        <span className="text-xs text-[#64748b]">annotated</span>
        <span className="text-sm text-[#cbd5e1] tabular-nums">
          {data.auto_accepted_count} auto-accepted · {data.review_count} to review
        </span>
        <span className="text-xs text-[#f59e0b] tabular-nums ml-auto">
          {pct(data.review_rate)} review rate
        </span>
      </div>

      {/* Stage funnel */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-wider text-[#475569] mb-2.5">
          Stage funnel
          <span className="ml-2 normal-case font-normal text-[#475569]">solid = auto-accepted, faded = routed to review</span>
        </p>
        <StageFunnel funnel={data.stage_funnel} total={data.total} />
      </div>

      {/* Distributions */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#475569] mb-2">
            RAG best-similarity
          </p>
          <ThresholdHistogram hist={data.similarity} markers={simMarkers} accent="#a5b4fc" />
        </div>
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#475569] mb-2">
            Final confidence
          </p>
          <ThresholdHistogram hist={data.confidence} markers={confMarkers} accent="#818cf8" />
        </div>
      </div>

      {/* Near-miss candidates */}
      {(data.near_miss_confidence.length > 0 || data.near_miss_similarity.length > 0) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <NearMissList
            title="Near auto-accept"
            items={data.near_miss_confidence}
            band={data.near_miss_band}
            gate={data.confidence_threshold}
            valueKey="confidence"
            gateLabel="the review threshold"
          />
          <NearMissList
            title="Near RAG-direct"
            items={data.near_miss_similarity}
            band={data.near_miss_band}
            gate={data.similarity.thresholds.rag_direct_threshold}
            valueKey="best_similarity"
            gateLabel="the RAG-direct threshold"
          />
        </div>
      )}
    </div>
  )
}
