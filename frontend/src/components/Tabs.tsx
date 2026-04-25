export type TabId = 'dashboard' | 'review' | 'reports' | 'infra'

export function Tabs({
  value,
  onChange,
}: {
  value: TabId
  onChange: (t: TabId) => void
}) {
  const tabs: { id: TabId; label: string }[] = [
    { id: 'dashboard', label: 'Dashboard' },
    { id: 'review', label: 'Review' },
    { id: 'reports', label: 'Reports' },
    { id: 'infra', label: 'Infra' },
  ]
  return (
    <nav className="flex gap-1 border-b border-zinc-200 px-6 bg-white">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`px-4 py-3 text-sm font-medium border-b-2 -mb-px transition
            ${
              value === t.id
                ? 'border-zinc-900 text-zinc-900'
                : 'border-transparent text-zinc-500 hover:text-zinc-800'
            }`}
        >
          {t.label}
        </button>
      ))}
    </nav>
  )
}
