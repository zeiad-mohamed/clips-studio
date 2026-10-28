import { useState } from 'react'
import { api, errorText } from '../lib/api'
import { setToken } from '../lib/auth'
import { t } from '../lib/i18n'

/** The login wall shown when the engine reports auth enabled and there is no
 *  session token yet (App's auth state 'login'). Submits to api.login, stores
 *  the token, and hands control back to App on success. */
export default function Login({ onSuccess }: { onSuccess: () => void }): JSX.Element {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (): Promise<void> => {
    if (!username.trim() || !password) return
    setBusy(true)
    setError(null)
    try {
      const token = (await api.login(username.trim(), password)).token
      // A null token means auth was off by the time we asked (login is a
      // no-op) — nothing to store, but the app can proceed regardless.
      if (token) setToken(token)
      onSuccess()
    } catch (e) {
      // A wrong password is a 401 whose detail is "invalid credentials"; show
      // it and stay on the page.
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="h-full grid place-items-center p-6">
      <div className="w-full max-w-sm card p-7">
        <h1 className="text-2xl font-bold">{t('Log in')}</h1>
        <p className="text-sm text-muted mt-2 leading-relaxed">
          {t('This Clips Kitty server requires a login. Enter the username and password set by its operator.')}
        </p>
        <form
          className="mt-5 space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <label className="block">
            <span className="label mb-1">{t('Username')}</span>
            <input
              className="input"
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="label mb-1">{t('Password')}</span>
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && (
            <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded-lg px-4 py-2.5">
              {error}
            </div>
          )}
          <button className="btn-accent w-full" type="submit" disabled={busy}>
            {busy ? t('Logging in…') : t('Log in')}
          </button>
        </form>
      </div>
    </div>
  )
}