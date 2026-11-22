import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, filtersQuery, toQuery } from './client';
import type {
  Document, ObservationDetail, ObservationPage, ObservationPatch, PhenologyRow, PlaceSpecies, Quality,
  QueryParseResponse, SavedQuery, SavedQueryCreate, SearchFilters, SuggestItem, SuggestKind,
} from './types';

export const keys = {
  observations: (f: SearchFilters, page: number, pageSize: number) => ['observations', f, page, pageSize] as const,
  observation: (id: number) => ['observation', id] as const,
  suggest: (kind: SuggestKind, q: string) => ['suggest', kind, q] as const,
  phenology: (taxonId: number, phenophaseId?: number, years?: number[]) => ['phenology', taxonId, phenophaseId, years] as const,
  placeSpecies: (id: number, year?: number, group?: SearchFilters['group']) => ['placeSpecies', id, year, group] as const,
  documents: ['documents'] as const,
  savedQueries: ['savedQueries'] as const,
  quality: ['quality'] as const,
};

export function useObservations(f: SearchFilters, page: number, pageSize: number, enabled = true) {
  return useQuery({
    queryKey: keys.observations(f, page, pageSize),
    queryFn: ({ signal }) => observationPage(f, page, pageSize, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

function observationPage(filters: SearchFilters, page: number, pageSize: number, signal: AbortSignal) {
  return api<ObservationPage>(`/observations${filtersQuery(filters, { page, page_size: pageSize })}`, { signal });
}

export function useMapPlaceIds(filters: SearchFilters) {
  // Stable ordering across pages; sorting the results table does not change the map.
  const ordered: SearchFilters = { ...filters, sort: 'id', order: 'asc' };
  return useQuery({
    queryKey: ['observations', 'map-place-ids', ordered],
    queryFn: async ({ signal }) => {
      const pageSize = 500;
      const ids = new Set<number>();
      async function read(page: number) {
        const result = await observationPage(ordered, page, pageSize, signal);
        for (const row of result.items) if (row.place_id !== null) ids.add(row.place_id);
        return result.total;
      }
      const pages = Math.ceil(await read(1) / pageSize);
      for (let page = 2; page <= pages; page += 4) {
        await Promise.all(Array.from({ length: Math.min(4, pages - page + 1) }, (_, i) => read(page + i)));
      }
      return [...ids].sort((a, b) => a - b);
    },
  });
}

export const reviewPageSize = 100;

export function useReviewPage(filters: SearchFilters, page: number, findId: number | null) {
  const initial = useQuery({
    queryKey: ['observations', 'review-page', filters, page],
    queryFn: async ({ signal }) => {
      const read = (n: number) => observationPage(filters, n, reviewPageSize, signal);
      const result = await read(page);
      const last = Math.max(1, Math.ceil(result.total / reviewPageSize));
      return page > last ? read(last) : result;
    },
  });

  // A saved record can move to another page after earlier records leave the queue.
  const visible = initial.data;
  const first = visible?.items[0], last = visible?.items.at(-1);
  const needsLookup = !!first && !!last && findId !== null && (findId < first.id || findId > last.id);
  const located = useQuery({
    queryKey: ['observations', 'review-record-page', filters, page, findId],
    enabled: needsLookup,
    queryFn: async ({ signal }) => {
      if (!visible || !first || !last || findId === null) throw new Error('Нет исходной страницы очереди');
      let low = findId > last.id ? visible.page + 1 : 1;
      let high = findId < first.id ? visible.page - 1 : Math.ceil(visible.total / reviewPageSize);
      while (low <= high) {
        const candidate = await observationPage(filters, Math.floor((low + high) / 2), reviewPageSize, signal);
        if (!candidate.items.length) break;
        const firstId = candidate.items[0]!.id;
        const lastId = candidate.items[candidate.items.length - 1]!.id;
        if (findId < firstId) high = candidate.page - 1;
        else if (findId > lastId) low = candidate.page + 1;
        else if (candidate.items.some((row) => row.id === findId)) return candidate;
        else break;
      }
      return visible;
    },
  });
  return needsLookup ? located : initial;
}

export function useObservation(id: number | null) {
  return useQuery({
    queryKey: keys.observation(id ?? 0),
    queryFn: () => api<ObservationDetail>(`/observations/${id}`),
    enabled: id !== null,
  });
}

export function usePatchObservation(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ObservationPatch) =>
      api<ObservationDetail>(`/observations/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: (data) => {
      qc.setQueryData(keys.observation(id), data);
      void qc.invalidateQueries({ queryKey: ['observations'] });
      void qc.invalidateQueries({ queryKey: ['phenology'] });
      void qc.invalidateQueries({ queryKey: ['placeSpecies'] });
    },
  });
}

export function useParseQuery() {
  return useMutation({
    mutationFn: (text: string) =>
      api<QueryParseResponse>('/query/parse', { method: 'POST', body: JSON.stringify({ text }) }),
  });
}

export function useSuggest(kind: SuggestKind, q: string) {
  const term = q.trim();
  return useQuery({
    queryKey: keys.suggest(kind, term),
    queryFn: () => api<SuggestItem[]>(`/suggest/${kind}${toQuery({ q: term, limit: 10 })}`),
    enabled: term.length > 0,
    staleTime: 60_000,
  });
}

export function usePhenology(taxonId: number | undefined, phenophaseId?: number, years?: number[]) {
  return useQuery({
    queryKey: keys.phenology(taxonId ?? 0, phenophaseId, years),
    queryFn: () => api<PhenologyRow[]>(`/phenology${toQuery({ taxon_id: taxonId, phenophase_id: phenophaseId, years })}`),
    enabled: !!taxonId,
  });
}

export function useMapPlaces(ids: number[] | undefined) {
  return useQuery({
    queryKey: ['placeSpecies', 'map-locations', ids],
    enabled: !!ids?.length,
    queryFn: async ({ signal }) => {
      const places: PlaceSpecies['place'][] = [];
      if (!ids) return places;
      for (let i = 0; i < ids.length; i += 6) {
        const batch = await Promise.all(ids.slice(i, i + 6).map(async (id) => {
          const result = await api<PlaceSpecies>(`/places/${id}/species`, { signal });
          return result.place;
        }));
        places.push(...batch);
      }
      return places;
    },
  });
}

export function usePlaceSpecies(placeId: number | null, year?: number, group?: SearchFilters['group']) {
  return useQuery({
    queryKey: keys.placeSpecies(placeId ?? 0, year, group),
    queryFn: ({ signal }) => api<PlaceSpecies>(`/places/${placeId}/species${toQuery({ year, group })}`, { signal }),
    enabled: placeId !== null,
  });
}

export function useDocuments(poll = false) {
  return useQuery({
    queryKey: keys.documents,
    queryFn: () => api<Document[]>('/documents'),
    refetchInterval: (q) =>
      poll || q.state.data?.some((d) => d.status === 'queued' || d.status === 'processing') ? 2000 : false,
    staleTime: 30_000,
  });
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (form: FormData) => api<Document>('/documents', { method: 'POST', body: form }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.documents }),
  });
}

export function useSavedQueries() {
  return useQuery({ queryKey: keys.savedQueries, queryFn: () => api<SavedQuery[]>('/saved-queries') });
}

export function useCreateSavedQuery() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SavedQueryCreate) =>
      api<SavedQuery>('/saved-queries', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.savedQueries }),
  });
}

export function useDeleteSavedQuery() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`/saved-queries/${id}`, { method: 'DELETE' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.savedQueries }),
  });
}

export function useQuality() {
  return useQuery({ queryKey: keys.quality, queryFn: () => api<Quality>('/quality') });
}
