export class ApiError extends Error {
  constructor(message: string, public status: number, public data?: unknown) { super(message); }
}

export async function api<T>(path: string, method = 'GET', body?: unknown, csrf?: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'support-pilot', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = response.status === 204 ? undefined : await response.json();
  if (!response.ok) throw new ApiError(typeof data?.detail === 'string' ? data.detail : data?.message || 'The request could not be completed. You can retry it.', response.status, data);
  return data as T;
}
