import { useState, useCallback } from 'react'

const SESSION_KEY = 'pika_token'

export function useAuth() {
  const [token, setTokenState] = useState(
    () => sessionStorage.getItem(SESSION_KEY) || ''
  )
  const [isOwner, setIsOwner] = useState(!!sessionStorage.getItem(SESSION_KEY))

  const login = useCallback((t) => {
    sessionStorage.setItem(SESSION_KEY, t)
    setTokenState(t)
    setIsOwner(true)
  }, [])

  const logout = useCallback(() => {
    sessionStorage.removeItem(SESSION_KEY)
    setTokenState('')
    setIsOwner(false)
  }, [])

  return { token, isOwner, login, logout }
}

