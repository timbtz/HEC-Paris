import { useState } from 'react'
import { api } from '@/api'
import { useRunProgress } from '@/store/runProgress'
import { RunProgressOverlay } from './RunProgressOverlay'

export function UploadZone() {
  const [hover, setHover] = useState(false)
  const [employeeId, setEmployeeId] = useState<number | undefined>(1)
  const setActiveRun = useRunProgress((s) => s.setActiveRun)

  const submit = async (file: File) => {
    try {
      const res = await api.uploadDocument(file, employeeId)
      setActiveRun(res.run_id)
    } catch (err) {
      console.error('[UploadZone]', err)
      alert(`Upload failed: ${(err as Error).message}`)
    }
  }

  return (
    <div className="p-6">
      <div className="flex gap-2 items-center mb-3 text-sm">
        <span className="text-zinc-600">Bill to:</span>
        <select
          value={employeeId ?? ''}
          onChange={(e) =>
            setEmployeeId(e.target.value ? Number(e.target.value) : undefined)
          }
          className="border border-zinc-300 rounded px-2 py-1"
        >
          <option value="">Company</option>
          <option value="1">Tim</option>
          <option value="2">Marie</option>
          <option value="3">Paul</option>
        </select>
      </div>
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setHover(true)
        }}
        onDragLeave={() => setHover(false)}
        onDrop={(e) => {
          e.preventDefault()
          setHover(false)
          const f = e.dataTransfer.files?.[0]
          if (f) submit(f)
        }}
        className={`border-2 border-dashed rounded-xl p-12 text-center transition cursor-pointer
          ${hover ? 'border-emerald-500 bg-emerald-50' : 'border-zinc-300'}`}
      >
        <p className="text-zinc-600">Drop a PDF invoice here</p>
        <input
          type="file"
          accept="application/pdf"
          className="mt-3 mx-auto block text-xs"
          onChange={(e) => e.target.files?.[0] && submit(e.target.files[0])}
        />
      </div>
      <RunProgressOverlay />
    </div>
  )
}
