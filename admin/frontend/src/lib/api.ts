import type { TokenPair } from '@/types'

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api'
const REFRESH_KEY = 'aivoice.refresh'

/**
 * The access token lives in memory only - a page reload re-derives it from the
 * refresh token. The refresh token is the one thing that has to survive a
 * reload, so it sits in localStorage.
 *
 * That trade is deliberate: an httpOnly cookie would resist XSS better but
 * needs CSRF protection and a same-site deployment, neither of which this panel
 * has yet. Rotation on every use (see the backend) is what limits the blast
 * radius in the meantime - a stolen refresh token works at most once, and the
 * real client's next refresh fails loudly.
 */
let accessToken: string | null = null
let onAuthLost: (() => void) | null = null

export function setAuthLostHandler(fn: (() => void) | null) {
  onAuthLost = fn
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}

export function storeTokens(t: TokenPair) {
  accessToken = t.access_token
  localStorage.setItem(REFRESH_KEY, t.refresh_token)
}

export function clearTokens() {
  accessToken = null
  localStorage.removeItem(REFRESH_KEY)
}

export function hasSession(): boolean {
  return getRefreshToken() !== null
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /**
     * Field name -> what is wrong with it, from a 422's `detail`.
     *
     * Kept structured as well as flattened into `message` so a form can put
     * each message against its own input. A single banner is unusable on a long
     * dialog: the field it names is usually scrolled off the screen.
     *
     * Empty for errors that belong to no single field.
     */
    readonly fields: Record<string, string> = {},
    /**
     * The part of the message that belongs to no field — a whole-object rule, a
     * 409, a plain string detail. Still needs a banner somewhere; a form that
     * only renders `fields` would drop it silently.
     */
    readonly general: string = '',
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** Split a FastAPI 422 into per-field messages and everything else. */
function splitDetail(body: any): { fields: Record<string, string>; general: string } {
  if (!Array.isArray(body?.detail)) return { fields: {}, general: '' }
  const fields: Record<string, string> = {}
  const loose: string[] = []
  for (const d of body.detail) {
    if (!d.msg) continue
    const msg = cleanMsg(d.msg)
    const loc = (Array.isArray(d.loc) ? d.loc : []).filter(
      (p: unknown) => typeof p === 'string' && p !== 'body' && p !== 'query',
    )
    // Only the first segment: "headers.X-Key" belongs to the "headers" input.
    // A model-level validator reports an empty loc and has no field to sit by.
    const field = loc[0]
    if (!field) loose.push(msg)
    else fields[field] = fields[field] ? `${fields[field]} ${msg}` : msg
  }
  return { fields, general: loose.join('; ') }
}

/** Pydantic prefixes custom validator messages with "Value error, ". */
function cleanMsg(msg: unknown): string {
  return String(msg).replace(/^Value error,\s*/, '')
}

async function parse(res: Response): Promise<any> {
  const text = await res.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function messageOf(status: number, body: any): string {
  if (typeof body?.detail === 'string') return body.detail
  // FastAPI validation errors come back as a list of {loc, msg, type}.
  //
  // `loc` has to be kept. Dropping it produced messages like "String should
  // match pattern '^[a-z][a-z0-9_]{2,47}$'" on a form with fourteen fields —
  // technically accurate and impossible to act on, because it never said which
  // field it meant.
  if (Array.isArray(body?.detail)) {
    return (
      body.detail
        .map((d: any) => {
          // loc is ["body", "name"] or ["body", "headers", "X-Key"]; the
          // leading "body"/"query" is noise to anyone reading a form.
          const field = (Array.isArray(d.loc) ? d.loc : [])
            .filter((p: unknown) => typeof p === 'string' && p !== 'body' && p !== 'query')
            .join('.')
          if (!d.msg) return null
          return field ? `${field}: ${cleanMsg(d.msg)}` : cleanMsg(d.msg)
        })
        .filter(Boolean)
        .join('; ') || 'invalid request'
    )
  }
  // Statuses the reverse proxy can produce itself. Those responses are HTML, so
  // there is no detail to show and the bare code tells the user nothing.
  if (status === 413) return 'The file is too large for the server to accept.'
  if (status === 502 || status === 504) {
    return 'The server took too long to respond. A long document can exceed the limit.'
  }
  return `request failed (${status})`
}

/**
 * Refresh rotates the token, so two concurrent refreshes would race and one
 * would revoke the other's session. Everyone waits on the same promise.
 */
let inFlightRefresh: Promise<boolean> | null = null

async function refreshAccessToken(): Promise<boolean> {
  if (inFlightRefresh) return inFlightRefresh

  inFlightRefresh = (async () => {
    const refresh_token = getRefreshToken()
    if (!refresh_token) return false
    try {
      const res = await fetch(`${BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token }),
      })
      if (!res.ok) return false
      storeTokens((await res.json()) as TokenPair)
      return true
    } catch {
      return false
    } finally {
      // release on the next tick so late callers still see this result
      setTimeout(() => {
        inFlightRefresh = null
      }, 0)
    }
  })()

  return inFlightRefresh
}

interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
  /** internal - stops a refresh loop if the retried request 401s again */
  _retried?: boolean
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {}
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`

  let res: Response
  try {
    res = await fetch(BASE + path, {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, 'cannot reach the API - is the SSH tunnel up?')
  }

  if (res.status === 401 && !opts._retried && hasSession()) {
    if (await refreshAccessToken()) {
      return api<T>(path, { ...opts, _retried: true })
    }
    clearTokens()
    onAuthLost?.()
    throw new ApiError(401, 'session expired - please sign in again')
  }

  if (!res.ok) {
    // parse() reads the body, so it can only be called once.
    const body = await parse(res)
    const { fields, general } = splitDetail(body)
    throw new ApiError(res.status, messageOf(res.status, body), fields,
                       general || (Object.keys(fields).length ? '' : messageOf(res.status, body)))
  }
  return (await parse(res)) as T
}

// ── auth calls, which deliberately bypass the interceptor ────────────────────

export async function login(email: string, password: string): Promise<TokenPair> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  }).catch(() => {
    throw new ApiError(0, 'cannot reach the API - is the SSH tunnel up?')
  })

  if (!res.ok) {
    const body = await parse(res)
    throw new ApiError(
      res.status,
      res.status === 401 ? 'Incorrect email or password' : messageOf(res.status, body),
    )
  }
  const tokens = (await res.json()) as TokenPair
  storeTokens(tokens)
  return tokens
}

export async function logout(): Promise<void> {
  const refresh_token = getRefreshToken()
  if (refresh_token) {
    // best effort: the local session goes away whether or not the server agrees
    await api('/auth/logout', { method: 'POST', body: { refresh_token } }).catch(() => {})
  }
  clearTokens()
}

/** Called on boot to turn a stored refresh token back into a usable session. */
export async function restoreSession(): Promise<boolean> {
  if (!hasSession()) return false
  return refreshAccessToken()
}

export interface IngestEvent {
  stage: 'hashing' | 'extracting' | 'chunking' | 'embedding' | 'saving' | 'working' | 'done' | 'error'
  pages?: number
  chunks?: number
  done?: number
  total?: number
  message?: string
  [k: string]: unknown
}

/**
 * Multipart upload with progress in both directions.
 *
 * XHR rather than fetch for two reasons: fetch cannot report *upload* progress
 * at all, and XHR exposes the partial response body as it arrives, which is how
 * the server's newline-delimited progress events are read.
 *
 * Refresh-on-401 is deliberately not retried here: the file stream has already
 * been consumed, so a retry would send an empty body. The caller sees the 401
 * and can try again once the interceptor has renewed the token.
 */
/**
 * Drive an XHR that answers with newline-delimited JSON, and resolve on the
 * final `done` line.
 *
 * Shared by the file upload and the URL import. They differ only in what they
 * send; the awkward parts - responseText only ever grows, the tail is usually
 * a partial line, and a failure after the stream opens arrives as a line
 * rather than a status - are the same either way and are easy to get subtly
 * wrong twice.
 */
function ndjson<T>(
  xhr: XMLHttpRequest,
  send: () => void,
  onEvent?: (event: IngestEvent) => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let consumed = 0
    let last: IngestEvent | null = null

    const drain = () => {
      const text = xhr.responseText
      let nl: number
      while ((nl = text.indexOf('\n', consumed)) !== -1) {
        const line = text.slice(consumed, nl).trim()
        consumed = nl + 1
        if (!line) continue
        try {
          last = JSON.parse(line) as IngestEvent
          onEvent?.(last)
        } catch {
          /* a malformed line is not worth failing the whole job over */
        }
      }
    }

    xhr.onprogress = drain
    xhr.onload = () => {
      drain()
      if (xhr.status < 200 || xhr.status >= 300) {
        // Errors are raised before the stream starts, so the body is ordinary JSON
        let body: unknown = null
        try {
          body = xhr.responseText ? JSON.parse(xhr.responseText) : null
        } catch {
          body = xhr.responseText
        }
        reject(new ApiError(xhr.status, messageOf(xhr.status, body)))
        return
      }
      // Once the stream is open the status is already 200, so a failure can
      // only arrive as a line - it must not be mistaken for success.
      if (last?.stage === 'error') {
        reject(new ApiError(502, String(last.message ?? 'the job failed')))
        return
      }
      if (last?.stage === 'done') {
        resolve(last as T)
        return
      }
      reject(new ApiError(0, 'the server closed the connection before finishing'))
    }
    xhr.onerror = () => reject(new ApiError(0, 'the request could not reach the API'))
    xhr.ontimeout = () => reject(new ApiError(0, 'the request timed out'))
    // Extraction, chunking and embedding all happen before the final line, and
    // a workbook of 47 sheets takes minutes.
    xhr.timeout = 900_000
    send()
  })
}

/**
 * Import a URL into the knowledge base, streaming progress as it goes.
 *
 * Not `api()`: that reads the whole body before resolving, which would leave
 * the console on a spinner for the length of a 47-sheet embed.
 */
export async function postStream<T = unknown>(
  path: string,
  body: unknown,
  onEvent?: (event: IngestEvent) => void,
): Promise<T> {
  const xhr = new XMLHttpRequest()
  xhr.open('POST', BASE + path)
  xhr.setRequestHeader('Content-Type', 'application/json')
  if (accessToken) xhr.setRequestHeader('Authorization', `Bearer ${accessToken}`)
  return ndjson<T>(xhr, () => xhr.send(body === undefined ? null : JSON.stringify(body)), onEvent)
}

export async function upload<T = unknown>(
  path: string,
  file: File,
  onProgress?: (percent: number) => void,
  onEvent?: (event: IngestEvent) => void,
): Promise<T> {
  const form = new FormData()
  form.append('file', file)

  const xhr = new XMLHttpRequest()
  xhr.open('POST', BASE + path)
  if (accessToken) xhr.setRequestHeader('Authorization', `Bearer ${accessToken}`)

  // A 30 MB PDF over a slow link with no feedback reads as a hung page.
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable && onProgress) onProgress((e.loaded / e.total) * 100)
  }

  return ndjson<T>(xhr, () => xhr.send(form), onEvent)
}

/**
 * Fetch a protected binary resource and hand back an object URL.
 *
 * <audio src> cannot send an Authorization header, and the recording endpoint is
 * not public. Downloading it once with the token and playing from a blob is the
 * only way to have both. The caller must revokeObjectURL when it is done.
 */
export async function authedBlob(path: string): Promise<Blob> {
  const headers: Record<string, string> = {}
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`

  // no-store, and not only because the server says so. A recording came back
  // as a zero-length blob with no request reaching the server at all - Chrome
  // was serving a broken cache entry - and Ctrl+Shift+R did not clear it,
  // because a hard reload governs the page and its assets while a fetch()
  // started by script afterwards still uses the default cache mode.
  //
  // Nothing fetched this way is worth caching: it is a file a person opened
  // once, on a LAN.
  const init: RequestInit = { headers, cache: 'no-store' }

  let res = await fetch(BASE + path, init)
  if (res.status === 401 && hasSession() && (await refreshAccessToken())) {
    // safe to retry: nothing was consumed on the first attempt
    res = await fetch(BASE + path, {
      ...init,
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    })
  }
  if (!res.ok) throw new ApiError(res.status, messageOf(res.status, await parse(res)))
  return res.blob()
}

/**
 * The Blob itself, not just a URL, so a caller can check what actually arrived.
 *
 * The recording player needs that: a file the server reports as 543 KB that
 * plays perfectly from a plain HTTP server, and not through this API, is a
 * delivery problem — and a blob URL alone gives nothing to compare against.
 */
export async function authedBlobUrl(path: string): Promise<string> {
  return URL.createObjectURL(await authedBlob(path))
}

export function buildQuery(params: Record<string, unknown>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    q.set(k, String(v))
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}
