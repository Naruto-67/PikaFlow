import { useState } from 'react'

export default function ScriptModal({ runId, scriptUrl, onClose }) {
  const [scriptText, setScriptText] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Fetch the script when modal opens
  useState(() => {
    fetch(scriptUrl)
      .then(res => res.json())
      .then(data => {
        if (!data.scenes) throw new Error("No scenes found in run spec")
        // Combine all scene descriptions into one script
        const text = data.scenes.map((s, i) => `Scene ${i+1}:\n${s.description}\n`).join('\n')
        setScriptText(text || "Script is empty.")
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [scriptUrl])

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-pika-900/80 backdrop-blur-sm animate-fade-in">
      <div className="bg-pika-800 border border-pika-700 rounded-2xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[85vh]">
        <div className="flex items-center justify-between p-4 border-b border-pika-700">
          <h3 className="font-semibold text-lg flex items-center gap-2">
            <span>📝</span> Script for {runId}
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white p-1">
            ✕
          </button>
        </div>
        
        <div className="p-4 overflow-y-auto flex-1 custom-scrollbar">
          {loading ? (
            <p className="text-gray-500 text-center py-10 animate-pulse">Loading script...</p>
          ) : error ? (
            <p className="text-pika-red text-center py-10">❌ Failed to load script: {error}</p>
          ) : (
            <pre className="whitespace-pre-wrap font-sans text-gray-200 text-sm leading-relaxed">
              {scriptText}
            </pre>
          )}
        </div>
        
        <div className="p-4 border-t border-pika-700 text-right bg-pika-900/30 rounded-b-2xl">
          <button onClick={onClose} className="btn-ghost text-sm">Close</button>
        </div>
      </div>
    </div>
  )
}

