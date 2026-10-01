import { useState } from 'react'
import { GH_API } from '../config'
import CryptoJS from 'crypto-js'

const ENCRYPTED_PAT = import.meta.env.VITE_ENCRYPTED_PAT || ''

export default function LoginCard({ onLogin }) {
  const [val, setVal] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    const password = val.trim()
    if (!password) { setErr('Password cannot be empty'); return }
    if (!ENCRYPTED_PAT) { setErr('Panel is not configured (missing encrypted token in build)'); return }
    
    setLoading(true)
    setErr('')
    
    try {
      // 1. Decrypt the PAT using the provided password
      const bytes = CryptoJS.AES.decrypt(ENCRYPTED_PAT, password)
      const token = bytes.toString(CryptoJS.enc.Utf8)
      
      if (!token) {
        setErr('Incorrect password.')
        setLoading(false)
        return
      }

      // 2. Verify the token by calling the GitHub API
      const res = await fetch(`${GH_API}/user`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      
      if (res.ok) {
        onLogin(token) // Store the *decrypted* token in session memory
      } else {
        setErr('Decryption succeeded, but token is invalid/expired on GitHub.')
      }
    } catch (error) {
      setErr('Incorrect password or decryption error.')
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
          Enter your <code className="text-pika-glow">Password</code> to unlock the panel.
        </p>
        <form onSubmit={submit} className="space-y-3">
          <input
            type="password"
            className="input text-center tracking-widest"
            placeholder="••••••••"
            value={val}
            onChange={e => { setVal(e.target.value); setErr('') }}
          />
          {err && <p className="text-pika-red text-sm">{err}</p>}
          <button type="submit" disabled={loading} className="btn-primary w-full">
            {loading ? 'Verifying...' : 'Unlock Panel'}
          </button>
        </form>
        <p className="text-xs text-gray-500 mt-4">
          Unlocks the Dev Panel for this session.
        </p>
      </div>
    </div>
  )
}

