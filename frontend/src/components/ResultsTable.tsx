import { useEffect, useMemo } from 'react';
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import type { ObservationListItem, SearchFilters, SortField } from '../api/types';
import { formatObsDate } from '../lib/labels';
import { useLabels } from '../lib/labelCache';
import { FlagIcon, StatusBadge } from './StatusBadge';

const col = createColumnHelper<ObservationListItem>();

/** Колонки таблицы → поле сортировки API (SortField). */
const SORTABLE: Record<string, SortField> = {
  date: 'date_from', object: 'object_raw', place: 'toponym_raw', source: 'obs_year', status: 'status',
};

function valueText(o: ObservationListItem): string {
  const v = o.value_num ?? o.value_raw;
  if (v === null || v === undefined) return '';
  return [v, o.unit].filter((x) => x !== null && x !== '').join(' ');
}

function sourceText(o: ObservationListItem): string {
  const page = o.printed_pages ?? ('pdf_page' in o.locator ? `PDF ${o.locator.pdf_page ?? '?'}` : '');
  const book = o.labels.book_year ? `${o.labels.book_year}` : o.labels.document_title;
  return `${book}${page ? `, стр. ${page}` : ''}`;
}

export default function ResultsTable({ items, filters, onSort, onOpen, selectedId }: {
  items: ObservationListItem[];
  filters: SearchFilters;
  onSort: (sort: SortField, order: 'asc' | 'desc') => void;
  onOpen: (id: number) => void;
  selectedId: number | null;
}) {
  const labels = useLabels();
  // запоминаем подписи для чипов фильтров
  useEffect(() => {
    for (const o of items) {
      if (o.taxon_id && o.labels.taxon_name_ru) labels.put('taxon', o.taxon_id, o.labels.taxon_name_ru);
      if (o.place_id && o.labels.place_name) labels.put('place', o.place_id, o.labels.place_name);
      if (o.phenophase_id && o.labels.phenophase_code) {
        labels.put('phenophase', o.phenophase_id, `${o.labels.phenophase_code} — ${o.labels.phenophase_name ?? ''}`);
      }
      labels.put('document', o.document_id, o.labels.document_title);
    }
  }, [items, labels]);

  const columns = useMemo(() => [
    col.display({ id: 'date', header: 'Дата', cell: ({ row: { original: o } }) => (
      <span className="whitespace-nowrap">{formatObsDate(o) || <span className="text-stone-400">{o.obs_year ?? '—'}</span>}</span>
    ) }),
    col.display({ id: 'object', header: 'Объект', cell: ({ row: { original: o } }) => (
      <div>
        <div>{o.record_type === 'publication' ? (o.pub_title ?? o.object_raw) : o.object_raw}</div>
        {(o.labels.taxon_name_lat || o.labels.taxon_name_ru) && !(o.labels.taxon_name_lat && o.object_raw.includes(o.labels.taxon_name_lat) && (!o.labels.taxon_name_ru || o.object_raw.includes(o.labels.taxon_name_ru))) && (
          <div className="text-xs text-stone-500">
            {o.labels.taxon_name_ru && o.labels.taxon_name_ru !== o.object_raw ? `${o.labels.taxon_name_ru} · ` : ''}
            <i>{o.labels.taxon_name_lat}</i>
          </div>
        )}
      </div>
    ) }),
    col.display({ id: 'event', header: 'Событие', cell: ({ row: { original: o } }) => (
      <div>
        <div>{o.record_type === 'publication' ? [o.pub_authors, o.pub_venue].filter(Boolean).join(' — ') : (o.event_raw ?? o.indicator ?? '')}</div>
        {o.labels.phenophase_code && (
          <div className="text-xs text-moss-700">фаза {o.labels.phenophase_code}{o.labels.phenophase_name ? ` — ${o.labels.phenophase_name}` : ''}</div>
        )}
      </div>
    ) }),
    col.display({ id: 'place', header: 'Место', cell: ({ row: { original: o } }) => (
      <div>
        <div>{o.toponym_raw ?? o.labels.place_name ?? ''}</div>
        {o.toponym_raw && o.labels.place_name && o.labels.place_name !== o.toponym_raw && (
          <div className="text-xs text-stone-500">→ {o.labels.place_name}</div>
        )}
      </div>
    ) }),
    col.display({ id: 'value', header: 'Значение', cell: ({ row: { original: o } }) => (
      <span className="whitespace-nowrap">{valueText(o)}</span>
    ) }),
    col.display({ id: 'author', header: 'Автор', cell: ({ row: { original: o } }) => (
      <span>{o.observers_raw ?? (o.labels.persons.join(', ') || o.pub_authors || '')}</span>
    ) }),
    col.display({ id: 'source', header: 'Источник', cell: ({ row: { original: o } }) => (
      <span className="whitespace-nowrap text-stone-600">{sourceText(o)}</span>
    ) }),
    col.display({ id: 'status', header: 'Статус', cell: ({ row: { original: o } }) => (
      <span className="inline-flex items-center"><StatusBadge status={o.status} /><FlagIcon flags={o.flags} /></span>
    ) }),
  ], []);

  const table = useReactTable({ data: items, columns, getCoreRowModel: getCoreRowModel(), manualSorting: true, manualPagination: true });

  const sortIcon = (id: string) => {
    const field = SORTABLE[id];
    if (!field) return null;
    if (filters.sort !== field) return <span className="text-[10px] tracking-tighter text-stone-300">↑↓</span>;
    return <span className="text-moss-600">{filters.order === 'desc' ? '↓' : '↑'}</span>;
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-stone-200 text-left text-xs uppercase tracking-wide text-stone-500">
              {hg.headers.map((h) => {
                const field = SORTABLE[h.column.id];
                return (
                  <th key={h.id} className="px-2 py-2 font-medium">
                    {field ? (
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 uppercase hover:text-stone-800"
                        onClick={() => onSort(field, filters.sort === field && filters.order !== 'desc' ? 'desc' : 'asc')}
                      >
                        {flexRender(h.column.columnDef.header, h.getContext())} {sortIcon(h.column.id)}
                      </button>
                    ) : flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((r) => (
            <tr
              key={r.id}
              tabIndex={0}
              data-observation-id={r.original.id}
              onClick={() => onOpen(r.original.id)}
              onKeyDown={(e) => { if (e.key === 'Enter') onOpen(r.original.id); }}
              className={`cursor-pointer border-b border-stone-100 align-top hover:bg-moss-50 ${
                selectedId === r.original.id ? 'bg-moss-100/70' : ''} ${r.original.status === 'rejected' ? 'text-stone-400 line-through' : ''}`}
            >
              {r.getVisibleCells().map((c) => (
                <td key={c.id} className="px-2 py-1.5">{flexRender(c.column.columnDef.cell, c.getContext())}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
