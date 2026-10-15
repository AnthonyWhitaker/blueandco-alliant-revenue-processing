import { json, stubFetch } from '@/test/render';

import { api, ApiError, setAuthTokenProvider, unwrap } from './client';

afterEach(() => {
  vi.unstubAllGlobals();
  setAuthTokenProvider(() => null);
});

const problem = {
  type: 'about:blank',
  title: 'Unprocessable Content',
  status: 422,
  detail: 'The request did not pass validation.',
  instance: '/api/suppliers/1/extraction-rules',
  errors: [
    {
      loc: ['body', 'cell_address'],
      msg: 'Cell Address is required for named_cell.',
      type: 'value_error',
    },
    { loc: ['body', 'cell_address'], msg: 'second message is ignored', type: 'value_error' },
    { loc: ['query', 'limit'], msg: 'Input should be at least 1', type: 'greater_than_equal' },
  ],
};

describe('unwrap', () => {
  it('returns data on success', async () => {
    stubFetch({ 'GET /api/health': () => json({ status: 'ok' }) });
    await expect(unwrap(api.GET('/api/health'))).resolves.toEqual({ status: 'ok' });
  });

  it('turns a problem+json response into an ApiError with field errors', async () => {
    stubFetch({
      'POST /api/suppliers/1/extraction-rules': () =>
        json(problem, 422, 'application/problem+json'),
    });
    const call = unwrap(
      api.POST('/api/suppliers/{supplier_id}/extraction-rules', {
        params: { path: { supplier_id: 1 } },
        body: { extraction_method: 'named_cell', rule_name: 'Total' },
      }),
    );
    const error = await call.catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(422);
    expect(apiError.message).toBe('The request did not pass validation.');
    expect(apiError.fieldErrors()).toEqual({
      cell_address: 'Cell Address is required for named_cell.',
      limit: 'Input should be at least 1',
    });
  });

  it('handles a non-problem error body (e.g. a proxy error page)', async () => {
    stubFetch({
      'GET /api/health': () =>
        new Response('<html>Bad Gateway</html>', { status: 502, statusText: 'Bad Gateway' }),
    });
    const error = (await unwrap(api.GET('/api/health')).catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(502);
    expect(error.problem).toBeNull();
    expect(error.message).toBe('Request failed (502 Bad Gateway)');
  });

  it('reports network failures as status 0', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')));
    const error = (await unwrap(api.GET('/api/health')).catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(0);
    expect(error.message).toMatch(/Could not reach the server/);
    expect(error.cause).toBeInstanceOf(TypeError);
  });
});

describe('auth header injection', () => {
  it('adds a bearer token when a provider supplies one', async () => {
    const fetchMock = stubFetch({ 'GET /api/health': () => json({ status: 'ok' }) });
    setAuthTokenProvider(() => 'token-123');
    await unwrap(api.GET('/api/health'));
    const request = fetchMock.mock.calls[0]?.[0];
    expect(request?.headers.get('Authorization')).toBe('Bearer token-123');
  });

  it('sends no Authorization header by default', async () => {
    const fetchMock = stubFetch({ 'GET /api/health': () => json({ status: 'ok' }) });
    await unwrap(api.GET('/api/health'));
    expect(fetchMock.mock.calls[0]?.[0].headers.has('Authorization')).toBe(false);
  });
});
