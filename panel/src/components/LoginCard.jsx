import { useState } from 'react'
import { GH_API } from '../config'

export default function LoginCard({ onLogin }) {
  const [val, setVal] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    const token = val.trim()
    if (!token) { setErr('Token cannot be empty'); return }
    
    setLoading(true)
    setErr('')
    
    try {
      // Verify the token by calling the GitHub API
      const res = await fetch(`${GH_API}/user`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      
      if (res.ok) {
        onLogin(token)
      } else {
        setErr('Invalid GitHub token. Access denied.')
      }
    } catch (error) {
      setErr('Network error checking token.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="animate-fade-in max-w-sm mx-auto mt-24">
      <div className="card text-center">
        <div className="text-4xl mb-4">🔐</div>
        <h2 className="text-xl font-bold mb-1">Owner Access</h2>
        <p className="text-sm text-gray-400 mb-6">
          Enter a <code className="text-pika-glow">GitHub PAT</code> to
          unlock privileged controls.
        </p>
        <form onSubmit={submit} className="space-y-3">
          <input
            type="password"
            className="input"
            placeholder="ghp_xxxxxxxxxxxx..."
            value={val}
            onChange={e => { setVal(e.target.value); setErr('') }}
          />
          {err && <p className="text-pika-red text-sm">{err}</p>}
          <button type="submit" disabled={loading} className="btn-primary w-full">
            {loading ? 'Verifying...' : 'Unlock Panel'}
          </button>
        </form>
        <p className="text-xs text-gray-500 mt-4">
          Needs a GitHub Personal Access Token with <b>repo</b> access. Stored in session only.
        </p>
      </div>
    </div>
  )
}

