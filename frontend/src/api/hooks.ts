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
  placeSpecies: (id: number, year?: number) => ['placeSpecies', id, year] as const,
  documents: ['documents'] as const,
  savedQueries: ['savedQueries'] as const,
  quality: ['quality'] as const,
};

export function useObservations(f: SearchFilters, page: number, pageSize: number, enabled = true) {
  return useQuery({
    queryKey: keys.observations(f, page, pageSize),
    queryFn: () => api<ObservationPage>(`/observations${filtersQuery(f, { page, page_size: pageSize })}`),
    placeholderData: keepPreviousData,
    enabled,
  });
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

export function usePlaceSpecies(placeId: number | null, year?: number) {
  return useQuery({
    queryKey: keys.placeSpecies(placeId ?? 0, year),
    queryFn: () => api<PlaceSpecies>(`/places/${placeId}/species${toQuery({ year })}`),
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
