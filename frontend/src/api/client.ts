// Thin fetch wrapper for the campaign-run API.
//
// Every request goes through here so no React component ever calls `fetch`
// directly. Non-2xx responses are turned into a typed `ApiError` carrying the
// backend's stable `{ code, message, details }` contract (see
// backend/api/errors.py).

const BASE_PATH = '/api/v1'

// A demo actor id; there is deliberately no auth in this pass, but the backend
// requires the X-Actor-Id header to attribute writes.
const ACTOR_ID = 'web-user'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

interface ErrorBody {
  code?: string
  message?: string
  details?: Record<string, unknown>
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ErrorBody = {}
  try {
    body = (await response.json()) as ErrorBody
  } catch {
    // Non-JSON error (e.g. a proxy failure); fall back to the status text.
  }
  return new ApiError(
    response.status,
    body.code ?? 'UNKNOWN',
    body.message ?? response.statusText ?? 'Request failed.',
    body.details ?? {},
  )
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { 'X-Actor-Id': ACTOR_ID }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const response = await fetch(`${BASE_PATH}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (!response.ok) throw await parseError(response)
  return (await response.json()) as T
}

export const httpGet = <T>(path: string): Promise<T> => request<T>('GET', path)

export const httpPost = <T>(path: string, body?: unknown): Promise<T> =>
  request<T>('POST', path, body)

export const httpPut = <T>(path: string, body?: unknown): Promise<T> =>
  request<T>('PUT', path, body)
