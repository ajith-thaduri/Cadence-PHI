// Thin fetch wrapper for the console API (/api/v1). Same-origin only; every request carries the X-PHI-UI header
// that the server requires on state-changing requests (CSRF defence together with SameSite=Strict cookies).

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

interface ErrorBody {
  detail?: unknown;
  errors?: { loc: string[]; msg: string }[];
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const j = (await res.json()) as ErrorBody;
    let msg = typeof j.detail === 'string' ? j.detail : res.statusText;
    if (j.errors?.length) {
      msg += ': ' + j.errors.map((e) => `${e.loc.slice(1).join('.') || 'request'} - ${e.msg}`).join('; ');
    }
    return msg;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'X-PHI-UI': '1', Accept: 'application/json' };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers,
    body: payload,
    credentials: 'same-origin',
    cache: 'no-store',
  });
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new Event('phi-ui:unauthorized'));
    throw new ApiError(res.status, await errorMessage(res));
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  del: <T>(path: string) => request<T>('DELETE', path),
};

/** Download a binary response (e.g. a batch result zip) without exposing it in a URL. */
export async function download(path: string, filename: string): Promise<void> {
  const res = await fetch(`/api/v1${path}`, { headers: { 'X-PHI-UI': '1' }, credentials: 'same-origin' });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return 'Something went wrong';
}
