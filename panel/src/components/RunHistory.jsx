import { useState } from 'react'
import { REPO_OWNER, REPO_NAME } from '../config'
import ScriptModal from './ScriptModal'

function statusBadge(tag) {
  return <span className="badge-green">✅ Complete</span>
}

function formatDate(iso) {
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

export default function RunHistory({ releases, loading, error, onRefresh }) {
  const [expanded, setExpanded] = useState(null)
  const [viewScriptUrl, setViewScriptUrl] = useState(null)

  if (loading) return (
    <div className="card animate-pulse-slow text-center text-gray-500 py-10">
      Loading run history…
    </div>
  )

  if (error) return (
    <div className="card border-red-800 text-pika-red text-sm">
      ❌ Failed to load releases: {error}
    </div>
  )

  return (
    <div className="card animate-slide-up">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold flex items-center gap-2">
          <span>📦</span> Run History
        </h2>
        <button onClick={onRefresh} className="btn-ghost text-xs">↻ Refresh</button>
      </div>

      {releases.length === 0 ? (
        <p className="text-gray-500 text-sm text-center py-6">
          No runs yet — trigger your first pipeline above!
        </p>
      ) : (
        <ul className="space-y-2">
          {releases.map(r => {
            const isOpen = expanded === r.id
            const videoAsset = r.assets?.find(a => a.name.endsWith('.mp4'))
            const logAsset   = r.assets?.find(a => a.name === 'log.jsonl')
            const seoAsset   = r.assets?.find(a => a.name === 'seo_metadata.json')
            const specAsset  = r.assets?.find(a => a.name === 'run_spec.json')

            return (
              <li key={r.id} className="bg-pika-700 rounded-xl overflow-hidden">
                {/* Row header */}
                <button
                  onClick={() => setExpanded(isOpen ? null : r.id)}
                  className="w-full flex items-center justify-between px-4 py-3 hover:bg-pika-600 transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-pika-glow font-mono text-xs shrink-0">
                      {r.tag_name}
                    </span>
                    <span className="text-gray-300 text-sm truncate">{r.name}</span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    {statusBadge(r.tag_name)}
                    <span className="text-gray-500 text-xs">{formatDate(r.published_at)}</span>
                    <span className="text-gray-400">{isOpen ? '▲' : '▼'}</span>
                  </div>
                </button>

                {/* Expanded details */}
                {isOpen && (
                  <div className="px-4 pb-4 space-y-3 border-t border-pika-600 pt-3 animate-fade-in">
                    {r.body && (
                      <p className="text-gray-400 text-sm line-clamp-3">{r.body}</p>
                    )}
                    <div className="flex flex-wrap gap-2">
                      {videoAsset && (
                        <a
                          href={videoAsset.browser_download_url}
                          className="btn-primary text-sm"
                          target="_blank" rel="noreferrer"
                        >
                          ⬇️ Download Video
                        </a>
                      )}
                      {specAsset && (
                        <button
                          onClick={() => setViewScriptUrl({url: specAsset.browser_download_url, id: r.tag_name})}
                          className="btn-ghost text-sm flex items-center gap-1"
                        >
                          📝 View Script
                        </button>
                      )}
                      {seoAsset && (
                        <a
                          href={seoAsset.browser_download_url}
                          className="btn-ghost text-sm"
                          target="_blank" rel="noreferrer"
                        >
                          📄 SEO Metadata
                        </a>
                      )}
                      {logAsset && (
                        <a
                          href={logAsset.browser_download_url}
                          className="btn-ghost text-sm"
                          target="_blank" rel="noreferrer"
                        >
                          📋 Logs
                        </a>
                      )}
                      <a
                        href={r.html_url}
                        className="btn-ghost text-sm"
                        target="_blank" rel="noreferrer"
                      >
                        🔗 GitHub Release
                      </a>
                    </div>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
      
      {viewScriptUrl && (
        <ScriptModal 
          runId={viewScriptUrl.id}
          scriptUrl={viewScriptUrl.url} 
          onClose={() => setViewScriptUrl(null)} 
        />
      )}
    </div>
  )
}

