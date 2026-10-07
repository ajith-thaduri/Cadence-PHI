import { ApiError, api } from '../api/client';

describe('api client', () => {
  afterEach(() => vi.restoreAllMocks());

  it('sends the CSRF header, same-origin credentials and JSON body', async () => {
    const f = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    await api.post('/analyze', { text: 'x' });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/analyze');
    expect((init.headers as Record<string, string>)['X-PHI-UI']).toBe('1');
    expect(init.credentials).toBe('same-origin');
    expect(init.body).toBe('{"text":"x"}');
  });

  it('turns error responses into readable ApiErrors and signals 401', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: 'invalid request',
          errors: [{ loc: ['body', 'modality'], msg: 'bad value' }],
        }),
        {
          status: 422,
        },
      ),
    );
    await expect(api.get('/x')).rejects.toMatchObject({
      status: 422,
      message: 'invalid request: modality - bad value',
    });
    const seen = vi.fn();
    window.addEventListener('phi-ui:unauthorized', seen);
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'sign in required' }), { status: 401 }),
    );
    await expect(api.get('/status')).rejects.toBeInstanceOf(ApiError);
    expect(seen).toHaveBeenCalled();
  });
});
