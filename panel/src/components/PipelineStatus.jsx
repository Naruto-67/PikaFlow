import { GH_API, REPO_PATH } from '../config'
import { useState, useEffect } from 'react'

function statusIcon(conclusion) {
  return { success: '✅', failure: '❌', cancelled: '⛔', in_progress: '⏳', queued: '🕐' }[conclusion] || '❓'
}

export default function PipelineStatus() {
  const [runs,    setRuns]    = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      try {
        const res  = await fetch(`${GH_API}/repos/${REPO_PATH}/actions/runs?per_page=5`)
        const data = await res.json()
        setRuns(data.workflow_runs || [])
      } catch { /* non-fatal */ }
      setLoading(false)
    }
    load()
    const id = setInterval(load, 30_000)  // auto-refresh every 30 s
    return () => clearInterval(id)
  }, [])

  return (
    <div className="card animate-slide-up">
      <h2 className="text-base font-semibold mb-4 flex items-center gap-2">
        <span>⚡</span> Recent Actions
        <span className="ml-auto text-xs text-gray-500">auto-refresh 30s</span>
      </h2>

      {loading ? (
        <p className="text-gray-500 text-sm animate-pulse-slow">Loading…</p>
      ) : runs.length === 0 ? (
        <p className="text-gray-500 text-sm">No workflow runs found.</p>
      ) : (
        <ul className="space-y-2">
          {runs.map(r => (
            <li key={r.id}
              className="flex items-center justify-between px-3 py-2.5 bg-pika-700 rounded-xl text-sm"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span>{statusIcon(r.conclusion || r.status)}</span>
                <span className="font-medium text-gray-200 truncate">{r.name}</span>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <span className="text-gray-500 text-xs">
                  {new Date(r.created_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
                </span>
                <a
                  href={r.html_url}
                  target="_blank" rel="noreferrer"
                  className="text-pika-glow hover:underline text-xs"
                >
                  View →
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

