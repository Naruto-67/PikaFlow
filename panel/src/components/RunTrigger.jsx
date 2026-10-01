import { useState } from 'react'
import { GH_API, REPO_PATH } from '../config'

const FORMATS = ['short', 'medium', 'long']

export default function RunTrigger({ token }) {
  const [prompt,  setPrompt]  = useState('')
  const [format,  setFormat]  = useState('medium')
  const [status,  setStatus]  = useState('idle')   // idle | loading | success | error
  const [message, setMessage] = useState('')

  const trigger = async (e) => {
    e.preventDefault()
    if (!prompt.trim()) return
    setStatus('loading')
    setMessage('')

    try {
      const res = await fetch(`${GH_API}/repos/${REPO_PATH}/dispatches`, {
        method: 'POST',
        headers: {
          Authorization:  `Bearer ${token}`,
          Accept:         'application/vnd.github+json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          event_type:     'run_pipeline',
          client_payload: { prompt: prompt.trim(), format },
        }),
      })

      if (res.status === 204) {
        setStatus('success')
        setMessage('Pipeline triggered! Check the Actions tab for progress.')
        setPrompt('')
      } else {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.message || `HTTP ${res.status}`)
      }
    } catch (err) {
      setStatus('error')
      setMessage(err.message)
    }
  }

  return (
    <div className="card animate-slide-up">
      <h2 className="text-base font-semibold mb-4 flex items-center gap-2">
        <span>🎬</span> Trigger New Run
      </h2>
      <form onSubmit={trigger} className="space-y-4">
        <div>
          <label className="text-xs text-gray-400 mb-1.5 block">Video Prompt</label>
          <textarea
            className="input resize-none h-24"
            placeholder="e.g. Top 5 free AI art tools – anime style, fast-cut listicle"
            value={prompt}
            onChange={e => { setPrompt(e.target.value); setStatus('idle') }}
          />
        </div>

        <div>
          <label className="text-xs text-gray-400 mb-1.5 block">Format</label>
          <div className="flex gap-2">
            {FORMATS.map(f => (
              <button
                key={f}
                type="button"
                onClick={() => setFormat(f)}
                className={`px-4 py-2 rounded-xl text-sm font-medium transition-all ${
                  format === f
                    ? 'bg-pika-accent text-white'
                    : 'bg-pika-700 text-gray-400 hover:text-white'
                }`}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
        </div>

        <button
          type="submit"
          disabled={!prompt.trim() || status === 'loading'}
          className="btn-primary w-full"
        >
          {status === 'loading' ? '⏳ Triggering…' : '🚀 Run Pipeline'}
        </button>

        {message && (
          <p className={`text-sm rounded-xl p-3 ${
            status === 'success'
              ? 'bg-green-950 text-pika-green border border-green-800'
              : 'bg-red-950 text-pika-red border border-red-800'
          }`}>
            {status === 'success' ? '✅ ' : '❌ '}{message}
          </p>
        )}
      </form>
    </div>
  )
}

