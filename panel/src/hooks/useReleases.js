import { useState, useEffect, useCallback } from 'react'
import { GH_API, REPO_PATH } from '../config'

export function useReleases() {
  const [releases, setReleases] = useState([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState(null)

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res  = await fetch(`${GH_API}/repos/${REPO_PATH}/releases?per_page=20`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.message || 'GitHub API error')
      setReleases(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetch_() }, [fetch_])

  return { releases, loading, error, refresh: fetch_ }
}

