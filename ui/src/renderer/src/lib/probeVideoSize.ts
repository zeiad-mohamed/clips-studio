/** A picked file's size and shape, straight from the browser.
 *
 *  The object-URL + `loadedmetadata` technique from web/lib/vertical.ts,
 *  copied rather than imported: that page has its own copy to serve, and the
 *  numbers here must stay in step with it (and with the desktop app's
 *  core/modes.py, which both were written against — see that file's note and
 *  tests/test_web_vertical_sync.py).
 *
 *  Why this exists: the Vertical Live suggestion wants a video's shape, and
 *  a browser-picked File can never take this app's engine round-trip
 *  (`GET /videos/local/shape` asks for a path). Reading it client-side is
 *  exactly what a picked path already got, so the suggestion and the
 *  "not 9:16" warning behave the same in both homes.
 *
 *  Instant: only the container header is read, never the file.
 */

/** Width / height counted as 9:16 (0.5625), with room for a slightly cropped
 *  canvas — the same constants web/lib/vertical.ts carries. */
const VERTICAL_MIN = 0.5
const VERTICAL_MAX = 0.6

export type VideoShape = {
  width: number
  height: number
  orientation: 'vertical' | 'horizontal' | 'other'
}

export function orientationOf(width: number, height: number): VideoShape['orientation'] {
  if (width <= 0 || height <= 0) return 'other'
  const ratio = width / height
  if (ratio >= VERTICAL_MIN && ratio <= VERTICAL_MAX) return 'vertical'
  const inverse = 1 / ratio
  if (inverse >= VERTICAL_MIN && inverse <= VERTICAL_MAX) return 'horizontal'
  return 'other'
}

/** 0×0 and "other" when the browser cannot read the file, the same answer
 *  the web page's probeSize(): the caller treats it as "no suggestion". */
export function probeVideoSize(file: File): Promise<VideoShape> {
  return new Promise((resolve) => {
    const el = document.createElement('video')
    const url = URL.createObjectURL(file)
    const done = (size: VideoShape) => {
      URL.revokeObjectURL(url)
      el.removeAttribute('src')
      resolve(size)
    }
    el.preload = 'metadata'
    el.onloadedmetadata = () =>
      done({
        width: el.videoWidth,
        height: el.videoHeight,
        orientation: orientationOf(el.videoWidth, el.videoHeight)
      })
    el.onerror = () => done({ width: 0, height: 0, orientation: 'other' })
    el.src = url
  })
}
