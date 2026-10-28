import { useEffect, useRef } from 'react'
import type { StudioEvent } from './types'
import { getToken } from './auth'

const WS_URL = (() => {
  const base = import.meta.env.VITE_API_BASE
  if (base === undefined) return 'ws://127.0.0.1:8765/ws'
  // Same-origin build (nginx proxies to the engine): derive from the page.
  if (base === '') return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
  return `${base.replace(/^http/, 'ws')}/ws`
})()

/** Subscribe to live backend events; reconnects automatically. `enabled=false`
 *  (the login wall is up) means no connection is opened until the app is
 *  authenticated — the same false-y default keeps every existing caller
 *  unchanged. */
export function useEvents(onEvent: (event: StudioEvent) => void, enabled = true): void {
  const handler = useRef(onEvent)
  handler.current = onEvent

  useEffect(() => {
    if (!enabled) return
    let socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | null = null
    let closed = false

    const connect = (): void => {
      // Browsers can't set headers on a WebSocket, so the auth token rides the
      // query string when one is present (see docs/API.md, Authentication).
      const token = getToken()
      const url = token ? `${WS_URL}?token=${encodeURIComponent(token)}` : WS_URL
      socket = new WebSocket(url)
      socket.onmessage = (msg) => {
        try {
          handler.current(JSON.parse(msg.data as string) as StudioEvent)
        } catch {
          /* malformed event: ignore */
        }
      }
      socket.onclose = () => {
        if (!closed) retry = setTimeout(connect, 2000)
      }
    }
    connect()

    return () => {
      closed = true
      if (retry) clearTimeout(retry)
      socket?.close()
    }
  }, [enabled])
}
