export function Skeleton() {
  return (
    <div className="p-6 space-y-4 animate-pulse">
      <div className="grid grid-cols-3 gap-6">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-40 bg-zinc-100 rounded-xl" />
        ))}
      </div>
      <div className="h-64 bg-zinc-100 rounded-xl" />
    </div>
  )
}
