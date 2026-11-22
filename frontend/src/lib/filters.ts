import type { RecordType, SearchFilters, SortField, Status, TaxonGroup } from '../api/types';
import { TAXON_GROUPS } from './labels';

const SORT_FIELDS: SortField[] = ['date_from', 'obs_year', 'object_raw', 'toponym_raw', 'confidence', 'status', 'id', 'created_at', 'updated_at'];
const RECORD_TYPES: RecordType[] = ['observation', 'env_measurement', 'publication'];
const STATUSES: Status[] = ['auto', 'verified', 'edited', 'rejected'];
const ISO = /^\d{4}-\d{2}-\d{2}$/;

export const FILTER_KEYS = [
  'q', 'taxon_id', 'taxon', 'group', 'person', 'place_id', 'place', 'phenophase_id', 'indicator',
  'record_type', 'document_id', 'date_from', 'date_to', 'years', 'doc_years', 'status', 'flagged', 'sort', 'order',
] as const satisfies readonly (keyof SearchFilters)[];

export type FilterKey = (typeof FILTER_KEYS)[number];

function int(v: string | null): number | undefined {
  if (v === null || !/^\d+$/.test(v)) return undefined;
  const n = Number(v);
  return n >= 1 ? n : undefined;
}
function ints(v: string | null): number[] | undefined {
  if (!v) return undefined;
  const arr = v.split(',').map((s) => s.trim()).filter((s) => /^\d+$/.test(s)).map(Number);
  return arr.length ? arr : undefined;
}
function strs(v: string | null): string[] | undefined {
  if (!v) return undefined;
  const arr = v.split(',').map((s) => s.trim()).filter(Boolean);
  return arr.length ? arr : undefined;
}
function oneOf<T extends string>(v: string | null, allowed: readonly T[]): T | undefined {
  return v !== null && (allowed as readonly string[]).includes(v) ? (v as T) : undefined;
}
function text(v: string | null): string | undefined {
  return v && v.trim() ? v : undefined;
}

/** Только известные ключи; некорректные значения отбрасываются. */
export function filtersFromParams(sp: URLSearchParams): SearchFilters {
  const f: SearchFilters = {
    q: text(sp.get('q')),
    taxon_id: int(sp.get('taxon_id')),
    taxon: text(sp.get('taxon')),
    group: oneOf<TaxonGroup>(sp.get('group'), TAXON_GROUPS),
    person: strs(sp.get('person')),
    place_id: int(sp.get('place_id')),
    place: text(sp.get('place')),
    phenophase_id: int(sp.get('phenophase_id')),
    indicator: text(sp.get('indicator')),
    record_type: oneOf(sp.get('record_type'), RECORD_TYPES),
    document_id: int(sp.get('document_id')),
    date_from: ISO.test(sp.get('date_from') ?? '') ? (sp.get('date_from') as string) : undefined,
    date_to: ISO.test(sp.get('date_to') ?? '') ? (sp.get('date_to') as string) : undefined,
    years: ints(sp.get('years')),
    doc_years: ints(sp.get('doc_years')),
    status: oneOf(sp.get('status'), STATUSES),
    flagged: sp.get('flagged') === 'true' ? true : sp.get('flagged') === 'false' ? false : undefined,
    sort: oneOf(sp.get('sort'), SORT_FIELDS),
    order: oneOf(sp.get('order'), ['asc', 'desc'] as const),
  };
  return clean(f);
}

/** Удаляет пустые значения (undefined, '', []). */
export function clean(f: SearchFilters): SearchFilters {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v) && v.length === 0) continue;
    out[k] = v;
  }
  return out as SearchFilters;
}

/** Записывает фильтры в URL, сохраняя прочие параметры (page, tab). */
export function writeFilters(sp: URLSearchParams, f: SearchFilters): URLSearchParams {
  const next = new URLSearchParams(sp);
  for (const k of FILTER_KEYS) next.delete(k);
  for (const [k, v] of Object.entries(clean(f))) {
    next.set(k, Array.isArray(v) ? v.join(',') : String(v));
  }
  return next;
}

export function parseYears(s: string): number[] | undefined {
  const out = new Set<number>();
  for (const part of s.split(/[,\s]+/).filter(Boolean)) {
    const m = /^(\d{4})\s*[-–]\s*(\d{4})$/.exec(part);
    if (m) {
      const a = Number(m[1]); const b = Number(m[2]);
      for (let y = Math.min(a, b); y <= Math.max(a, b) && out.size < 300; y++) out.add(y);
    } else if (/^\d{4}$/.test(part)) out.add(Number(part));
  }
  return out.size ? [...out].sort((a, b) => a - b) : undefined;
}
