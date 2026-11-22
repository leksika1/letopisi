import type { ApiError, SearchFilters } from './types';

export class HttpError extends Error {
  constructor(public status: number, public body: ApiError | null) {
    super(body?.error.message ?? `Ошибка HTTP ${status}`);
  }
}

type Scalar = string | number | boolean;
export type QueryValue = Scalar | Scalar[] | null | undefined;

/** Массивы — через запятую (style: form, explode: false), пустые значения пропускаются. */
export function toQuery(params: Record<string, QueryValue>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) {
      if (v.length) sp.set(k, v.join(','));
    } else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  });
  if (!res.ok) {
    let body: ApiError | null = null;
    try { body = (await res.json()) as ApiError; } catch { /* тело не JSON */ }
    throw new HttpError(res.status, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function filtersQuery(f: SearchFilters, extra: Record<string, QueryValue> = {}): string {
  return toQuery({ ...(f as Record<string, QueryValue>), ...extra });
}

export function exportUrl(f: SearchFilters, format: 'xlsx' | 'csv'): string {
  return `/api/export${filtersQuery(f, { format })}`;
}

export function pageImageUrl(documentId: number, page: number): string {
  return `/api/documents/${documentId}/pages/${page}.png`;
}

export function documentFileUrl(documentId: number, page?: number | null): string {
  return `/api/documents/${documentId}/file${page ? `#page=${page}` : ''}`;
}
