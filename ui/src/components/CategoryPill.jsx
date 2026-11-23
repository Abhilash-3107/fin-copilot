// The category of a payment as a list row shows it. A payment whose guess the
// server is holding back (waiting_on_pair: it could be a move between your own
// accounts, so the money figures count it as not sorted) says so in plain words,
// with the old guess as small text, so a row never claims a category the figures
// do not use.
export const WAITING_WORDS = 'Not sorted - maybe a move between your accounts'

export function categoryText(ann) {
  return ann.subcategory ? `${ann.category} › ${ann.subcategory}` : ann.category
}

export default function CategoryPill({ txn, ann }) {
  if (txn.waiting_on_pair) {
    return (
      <span className="inline-flex flex-col items-start gap-0.5">
        <span className="bg-[#78350f] text-[#fcd34d] text-xs px-2 py-0.5 rounded-full">{WAITING_WORDS}</span>
        <span className="text-[11px] text-[#94a3b8]">Guess was {categoryText(ann)}</span>
      </span>
    )
  }
  return (
    <span className="bg-[#1e1b4b] text-[#a5b4fc] text-xs px-2 py-0.5 rounded-full whitespace-nowrap">
      {categoryText(ann)}
    </span>
  )
}
