import { useMemo, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { CircleMarker, MapContainer, Rectangle, TileLayer, Tooltip } from 'react-leaflet';
import { api, toQuery } from '../api/client';
import { keys, useObservations, usePlaceSpecies } from '../api/hooks';
import type { PlaceSpecies, SearchFilters } from '../api/types';
import { formatDate } from '../lib/labels';

/** Примерные границы заповедника «Пасвик» (как в задании B-4). */
const RESERVE: [[number, number], [number, number]] = [[68.9, 28.6], [69.5, 30.2]];

/**
 * Карта мест. В контракте нет списка всех мест (GET /places), поэтому точки берутся из мест,
 * встречающихся в текущей выдаче; координаты и виды — из GET /places/{id}/species.
 */
export default function PlaceMap({ filters, year, onPickTaxon }: {
  filters: SearchFilters;
  year?: number;
  onPickTaxon: (taxonId: number, label: string) => void;
}) {
  const rest: SearchFilters = { ...filters, place_id: undefined, place: undefined };
  const { data: page } = useObservations(rest, 1, 500);
  const placeIds = useMemo(
    () => [...new Set((page?.items ?? []).map((o) => o.place_id).filter((x): x is number => !!x))],
    [page],
  );
  const results = useQueries({
    queries: placeIds.map((id) => ({
      queryKey: keys.placeSpecies(id, year),
      queryFn: () => api<PlaceSpecies>(`/places/${id}/species${toQuery({ year })}`),
      staleTime: 60_000,
    })),
  });
  const places = results.map((r) => r.data?.place).filter((p): p is NonNullable<typeof p> => !!p);
  const withCoords = places.filter((p) => p.lat !== null && p.lon !== null);
  const without = places.filter((p) => p.lat === null || p.lon === null);
  const [selected, setSelected] = useState<number | null>(filters.place_id ?? null);
  const species = usePlaceSpecies(selected, year);

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      <div>
        <div className="h-[420px] overflow-hidden rounded border border-stone-200">
          <MapContainer bounds={RESERVE} scrollWheelZoom className="h-full w-full">
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <Rectangle bounds={RESERVE} pathOptions={{ color: '#5b7a4c', weight: 1, fillOpacity: 0.04, dashArray: '4 4' }} />
            {withCoords.map((p) => (
              <CircleMarker
                key={p.id}
                center={[p.lat as number, p.lon as number]}
                radius={selected === p.id ? 10 : 7}
                pathOptions={{ color: '#3a4f31', fillColor: selected === p.id ? '#d97706' : '#5b7a4c', fillOpacity: 0.85 }}
                eventHandlers={{ click: () => setSelected(p.id) }}
              >
                <Tooltip>{p.name}{p.coords_source === 'manual_approx' ? ' (координаты приблизительные)' : ''}</Tooltip>
              </CircleMarker>
            ))}
          </MapContainer>
        </div>
        <p className="mt-1 text-xs text-stone-500">
          На карте — места из текущей выдачи, у которых в справочнике есть координаты ({withCoords.length} из {places.length}).
        </p>
        {without.length > 0 && (
          <div className="mt-2 text-sm">
            <span className="text-stone-500">Без координат: </span>
            {without.map((p, i) => (
              <span key={p.id}>
                {i > 0 && ', '}
                <button type="button" className={`hover:underline ${selected === p.id ? 'font-semibold text-amber-700' : 'text-moss-700'}`} onClick={() => setSelected(p.id)}>
                  {p.name}
                </button>
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="card p-3">
        {!selected && <p className="text-sm text-stone-500">Выберите место на карте или в списке.</p>}
        {species.isLoading && <p className="text-sm text-stone-500">Загрузка…</p>}
        {species.data && (
          <>
            <h3 className="text-base">{species.data.place.name}</h3>
            <div className="mb-2 text-xs text-stone-500">
              {species.data.place.category}
              {species.data.place.lat !== null && ` · ${species.data.place.lat}, ${species.data.place.lon} (${species.data.place.coords_source})`}
              {year ? ` · ${year} г.` : ''}
            </div>
            {species.data.items.length === 0 ? (
              <p className="text-sm text-stone-500">Видов не отмечено.</p>
            ) : (
              <ul className="divide-y divide-stone-100 text-sm" data-testid="place-species">
                {species.data.items.map((s) => (
                  <li key={s.taxon_id} className="py-1.5">
                    <button type="button" className="text-left hover:underline" onClick={() => onPickTaxon(s.taxon_id, s.name_ru ?? s.name_lat)}>
                      {s.name_ru} <i className="text-stone-500">{s.name_lat}</i>
                    </button>
                    <div className="text-xs text-stone-500">
                      {s.group_ru} · записей: {s.n_records}
                      {s.first_date && ` · ${formatDate(s.first_date)}${s.last_date && s.last_date !== s.first_date ? `–${formatDate(s.last_date)}` : ''}`}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  );
}
