import { useState } from 'react'

export default function TagInput({ tags = [], onChange, placeholder = 'type and press Enter' }) {
  const [input, setInput] = useState('')

  function addTag(raw) {
    const tag = raw.trim().replace(/,/g, '')
    if (tag && !tags.includes(tag)) onChange([...tags, tag])
    setInput('')
  }

  function removeTag(tag) {
    onChange(tags.filter(t => t !== tag))
  }

  return (
    <div className="flex flex-wrap gap-1.5 bg-[#13151f] border border-[#2d3148] rounded-md px-2 py-1.5 min-h-[36px] items-center focus-within:border-[#6366f1] transition-colors">
      {tags.map(tag => (
        <span
          key={tag}
          className="flex items-center gap-1 bg-[#1e1b4b] text-[#a5b4fc] text-xs px-2 py-0.5 rounded-full"
        >
          {tag}
          <button
            type="button"
            onClick={() => removeTag(tag)}
            className="text-[#6366f1] hover:text-white leading-none text-base"
          >
            &times;
          </button>
        </span>
      ))}
      <input
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={e => {
          if ((e.key === 'Enter' || e.key === ',') && input.trim()) {
            e.preventDefault()
            addTag(input)
          } else if (e.key === 'Backspace' && !input && tags.length) {
            onChange(tags.slice(0, -1))
          }
        }}
        className="bg-transparent border-none outline-none text-sm text-[#e2e8f0] flex-1 min-w-[80px] placeholder:text-[#475569]"
        placeholder={tags.length === 0 ? placeholder : ''}
      />
    </div>
  )
}
