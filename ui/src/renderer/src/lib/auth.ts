/** The engine session token for the opt-in single-user login (docs/API.md →
 *  "Authentication"). Held as a module variable so every request reads it
 *  without a round-trip, and mirrored to localStorage so a reload stays logged
 *  in. A `401` from the engine clears it (see request() in api.ts) and flips
 *  the app back to the login page. */

const KEY = 'auth-token'

let token: string | null = localStorage.getItem(KEY)

export function getToken(): string | null {
  return token
}

export function setToken(value: string): void {
  token = value
  localStorage.setItem(KEY, value)
}

export function clearToken(): void {
  token = null
  localStorage.removeItem(KEY)
}