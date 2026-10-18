/**
 * Typed API client. Request/response types come from the generated schema
 * (src/api/schema.d.ts — regenerate with `npm run api:sync`), never by hand.
 *
 * Usage in a query:  queryFn: () => unwrap(api.GET('/api/jobs/{job_id}', { params: { path: { job_id } } }))
 */
import createClient, { type Middleware } from 'openapi-fetch';

import type { components, paths } from './schema';

export type Schemas = components['schemas'];
export type Problem = Schemas['Problem'];

/** Same-origin unless VITE_API_BASE_URL is set (it is public: baked into the bundle). */
function resolveBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  if (configured) return configured;
  return window.location.origin;
}

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem | null;

  constructor(status: number, problem: Problem | null, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'ApiError';
    this.status = status;
    this.problem = problem;
  }

  static fromResponse(response: Response, body: unknown): ApiError {
    const problem = isProblem(body) ? body : null;
    const message =
      problem?.detail ??
      problem?.title ??
      `Request failed (${response.status} ${response.statusText})`;
    return new ApiError(response.status, problem, message);
  }

  /** Validation messages keyed by request field name, for inline form errors. */
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const e of this.problem?.errors ?? []) {
      const field = e.loc.slice(1).join('.');
      if (field && !(field in out)) out[field] = e.msg;
    }
    return out;
  }
}

export function isProblem(body: unknown): body is Problem {
  return (
    typeof body === 'object' &&
    body !== null &&
    'status' in body &&
    typeof body.status === 'number' &&
    'title' in body &&
    typeof body.title === 'string'
  );
}

type TokenProvider = () => string | null | Promise<string | null>;
let tokenProvider: TokenProvider = () => null;

/**
 * Hook for bearer-token auth. With Azure Easy Auth (cookie-based, same origin)
 * nothing needs to be set; this exists for a token-based setup.
 */
export function setAuthTokenProvider(provider: TokenProvider): void {
  tokenProvider = provider;
}

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = await tokenProvider();
    if (token) request.headers.set('Authorization', `Bearer ${token}`);
    return request;
  },
};

export const api = createClient<paths>({
  baseUrl: resolveBaseUrl(),
  // Late-bound so tests can stub globalThis.fetch after this module loads.
  fetch: (input: Request) => globalThis.fetch(input),
});
api.use(authMiddleware);

interface ClientResult<D> {
  data?: D;
  error?: unknown;
  response: Response;
}

/** Await an openapi-fetch call: return its data, or throw a normalized ApiError. */
export async function unwrap<D>(request: Promise<ClientResult<D>>): Promise<D> {
  let result: ClientResult<D>;
  try {
    result = await request;
  } catch (cause) {
    throw new ApiError(
      0,
      null,
      'Could not reach the server. Check your connection and try again.',
      {
        cause,
      },
    );
  }
  if (!result.response.ok) throw ApiError.fromResponse(result.response, result.error);
  // openapi-fetch only omits `data` for empty successful bodies (204), whose D is never/undefined.
  return result.data as D;
}

type FormValue = string | number | boolean | Blob | null | undefined;

/**
 * Body serializer for multipart/form-data endpoints (file uploads):
 * `api.POST(path, { body, bodySerializer: formData })`. Null/undefined fields are omitted.
 */
export function formData(body: Record<string, FormValue>): FormData {
  const data = new FormData();
  for (const [name, value] of Object.entries(body)) {
    if (value === null || value === undefined) continue;
    if (value instanceof File) data.append(name, value, value.name);
    else if (value instanceof Blob) data.append(name, value);
    else data.append(name, String(value));
  }
  return data;
}
