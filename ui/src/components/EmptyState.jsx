export default function EmptyState({ title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center px-4">
      <p className="text-[#475569] font-medium">{title}</p>
      {description && <p className="text-[#475569] text-sm mt-2 max-w-sm">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
