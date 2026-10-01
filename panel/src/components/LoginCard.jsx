import { useState } from 'react'

export default function LoginCard({ onLogin }) {
  const [val, setVal] = useState('')
  const [err, setErr] = useState('')

  const submit = (e) => {
    e.preventDefault()
    if (!val.trim()) { setErr('Token cannot be empty'); return }
    onLogin(val.trim())
  }

  return (
    <div className="animate-fade-in max-w-sm mx-auto mt-24">
      <div className="card text-center">
        <div className="text-4xl mb-4">🔐</div>
        <h2 className="text-xl font-bold mb-1">Owner Access</h2>
        <p className="text-sm text-gray-400 mb-6">
          Enter your <code className="text-pika-glow">DEV_PANEL_TOKEN</code> to
          unlock privileged controls.
        </p>
        <form onSubmit={submit} className="space-y-3">
          <input
            type="password"
            className="input"
            placeholder="Paste your token…"
            value={val}
            onChange={e => { setVal(e.target.value); setErr('') }}
          />
          {err && <p className="text-pika-red text-sm">{err}</p>}
          <button type="submit" className="btn-primary w-full">
            Unlock Panel
          </button>
        </form>
        <p className="text-xs text-gray-500 mt-4">
          Token is stored in session memory only — cleared when you close the tab.
        </p>
      </div>
    </div>
  )
}

