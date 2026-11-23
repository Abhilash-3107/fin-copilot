import { useEffect, useState } from 'react'
import { CheckCircle, ChevronDown, ChevronUp, SkipForward, Tag, Undo2 } from 'lucide-react'
import dayjs from 'dayjs'
import Amount from './Amount.jsx'
import CategoryTypeahead from './CategoryTypeahead.jsx'
import PayeeHeading, { groupName, payeeName } from './PayeeHeading.jsx'
import ReasoningPanel from './ReasoningPanel.jsx'
import TagInput from './TagInput.jsx'
import { labelOf } from '../lib/categoryOptions.js'
import { count } from '../lib/plural.js'
import { useLedger } from '../contexts/LedgerContext.jsx'
import { TEACH_ME_KEYS, keyLabel } from '../lib/shortcuts.js'

const KBD = 'bg-[#1e2235] px-1.5 py-0.5 rounded text-[10px] text-[#94a3b8]'

// A suggestion that pairs this payment with another (pair_with) is a different answer from the
// same category chosen plainly, so the two never look chosen together.
const sameLabel = (a, b) => a.category === b.category && (a.subcategory || null) === (b.subcategory || null)
const isChosen = (suggestion, chosen) =>
  !!chosen && sameLabel(suggestion, chosen) && (suggestion.pair_with ?? null) === (chosen.pairWith ?? null)

// One payment to sort: who and what it was, up to three suggestions with the
// reason for each, a filter over every other category, and the answer keys.
export default function SortCard({
  item, form, setForm, tree, chosen, needsPick, saving, canUndo, devMode,
  groupSize, applyToGroup, onApplyToGroup,
  onChoose, onCreateCategory, onConfirm, onSkip, onUndo,
  cardRef, typeaheadRef,
}) {
  const { multiple } = useLedger()
  const [showPrinted, setShowPrinted] = useState(false)
  const [showTags, setShowTags] = useState(false)
  useEffect(() => { setShowPrinted(false); setShowTags(false) }, [item.transaction_id])

  const suggestions = item.suggestions ?? []
  const pickedFromList = suggestions.some(s => isChosen(s, chosen))
  // With nothing suggested and nothing chosen yet, say what to do; once a pick is on screen it speaks for itself.
  const heading = suggestions.length ? 'Which is it?' : (chosen ? null : 'No suggestion for this one. Pick a category.')

  return (
    <div
      ref={cardRef}
      tabIndex={-1}
      role="group"
      aria-label={`Payment to ${payeeName(item)}`}
      className="max-w-3xl mx-auto w-full bg-[#13151f] border border-[#2d3148] rounded-xl focus:outline-none"
    >
      <div className="px-6 pt-5 pb-4 border-b border-[#2d3148]">
        <div className="flex items-start justify-between gap-4">
          <PayeeHeading item={item} />
          <Amount value={item.amount} debitCredit={item.debit_credit} className="text-xl font-bold tabular-nums whitespace-nowrap text-[#e2e8f0]" />
        </div>
        <div className="mt-2 flex items-center gap-3 text-xs text-[#94a3b8]">
          <span>{dayjs(item.txn_date).format('DD MMM YYYY')}</span>
          {multiple && item.account_label && <span>{item.account_label}</span>}
          {/* The printed line is the heading when no payee could be read from it. */}
          {item.payee && (
            <button
              onClick={() => setShowPrinted(v => !v)}
              aria-expanded={showPrinted}
              className="inline-flex items-center gap-1 hover:text-[#e2e8f0] transition-colors"
            >
              As printed on the statement {showPrinted ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            </button>
          )}
        </div>
        {showPrinted && item.payee && <p className="mt-2 text-xs text-[#94a3b8] break-all">{item.raw_description}</p>}
        {/* A pair answers for this payment alone: the group box has nothing to say then. */}
        {groupSize > 1 && !chosen?.pairWith && (
          <label className="mt-3 flex items-center gap-2 text-xs text-[#94a3b8] cursor-pointer select-none">
            <input
              type="checkbox"
              checked={applyToGroup}
              onChange={e => onApplyToGroup(e.target.checked)}
              className="accent-[#6366f1]"
            />
            <span>
              Do the same for all {count(groupSize, 'payment')} to <span className="text-[#cbd5e1]">{groupName(item)}</span>
            </span>
            <kbd className={KBD}>{keyLabel(TEACH_ME_KEYS.group)}</kbd>
          </label>
        )}
      </div>

      {devMode && <ReasoningPanel reasoning={item.reasoning} />}

      <div className="px-6 py-4 border-b border-[#2d3148]">
        {heading && <p className="text-xs text-[#94a3b8] mb-2">{heading}</p>}
        <div className="grid gap-2">
          {suggestions.map((s, i) => {
            const pressed = isChosen(s, chosen)
            return (
              <button
                key={s.pair_with ?? labelOf(s)}
                aria-pressed={pressed}
                onClick={e => onChoose(s, e)}
                className={`flex items-center gap-3 text-left rounded-lg border px-3 py-2 min-h-12 transition-colors ${
                  pressed ? 'border-[#e2e8f0] bg-[#1e2235]' : 'border-[#2d3148] hover:bg-[#1a1d2c]'
                }`}
              >
                <kbd className={KBD}>{keyLabel(TEACH_ME_KEYS.choose[i] ?? String(i + 1))}</kbd>
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-[#e2e8f0]">{s.title ?? labelOf(s)}</span>
                  <span className="block text-xs text-[#94a3b8]">{s.reason}</span>
                </span>
              </button>
            )
          })}
          {chosen && !pickedFromList && (
            <div className="flex items-center gap-3 rounded-lg border border-[#e2e8f0] bg-[#1e2235] px-3 py-2 min-h-12" aria-label="Your pick">
              <CheckCircle size={14} className="text-[#94a3b8]" />
              <span className="min-w-0">
                <span className="block text-sm font-medium text-[#e2e8f0]">{labelOf(chosen)}</span>
                <span className="block text-xs text-[#94a3b8]">Your pick</span>
              </span>
            </div>
          )}
        </div>

        <div className="mt-3">
          <CategoryTypeahead
            tree={tree}
            chosenCategory={form.category}
            onPick={onChoose}
            onCreate={onCreateCategory}
            onDone={() => cardRef.current?.focus({ preventScroll: true })}
            inputRef={typeaheadRef}
          />
        </div>

        <button
          onClick={() => setShowTags(v => !v)}
          aria-expanded={showTags}
          className="mt-3 flex items-center gap-1 text-[11px] text-[#94a3b8] hover:text-[#e2e8f0] transition-colors"
        >
          <Tag size={11} />
          {showTags ? 'Hide' : 'Edit'} tags
          {showTags ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        </button>
        {showTags && (
          <div className="mt-3">
            <div>
              <span className="block text-[10px] font-semibold uppercase tracking-wider text-[#94a3b8] mb-1.5">Tags</span>
              <TagInput tags={form.tags} onChange={tags => setForm(f => ({ ...f, tags }))} />
            </div>
          </div>
        )}
      </div>

      <div className="px-6 py-4 flex items-center gap-3">
        {needsPick ? (
          <button
            type="button"
            aria-disabled="true"
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-[#2d3148] bg-[#1e2235] text-sm font-medium text-[#94a3b8] cursor-not-allowed"
          >
            Pick a category
          </button>
        ) : (
          <button
            onClick={onConfirm}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-[#4f46e5] text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            <CheckCircle size={16} /> {saving ? 'Saving…' : 'Confirm'} <kbd className="text-[10px]">{keyLabel(TEACH_ME_KEYS.confirm)}</kbd>
          </button>
        )}
        <button
          onClick={onSkip}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-[#2d3148] text-sm text-[#94a3b8] hover:text-[#e2e8f0] transition-colors"
        >
          <SkipForward size={16} /> Skip <kbd className="text-[10px]">{keyLabel(TEACH_ME_KEYS.skip)}</kbd>
        </button>
        <span className="flex-1" />
        <button
          onClick={onUndo}
          disabled={!canUndo}
          className="flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm text-[#94a3b8] hover:text-[#e2e8f0] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          <Undo2 size={15} /> Undo <kbd className="text-[10px]">{keyLabel(TEACH_ME_KEYS.undo)}</kbd>
        </button>
      </div>
    </div>
  )
}
