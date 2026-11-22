import type { SearchFilters } from '../api/types';
import { formatDate, RECORD_TYPES, STATUS_LABELS } from '../lib/labels';
import { useLabels } from '../lib/labelCache';
import type { FilterKey } from '../lib/filters';

const SORT_LABELS: Record<string, string> = {
  date_from: 'дате', obs_year: 'году', object_raw: 'объекту', toponym_raw: 'месту', confidence: 'достоверности',
  status: 'статусу', id: 'номеру', created_at: 'созданию', updated_at: 'изменению',
};

interface Chip { key: FilterKey; label: string; remove: Partial<SearchFilters>; }

export function useChips(f: SearchFilters): Chip[] {
  const { get } = useLabels();
  const chips: Chip[] = [];
  const add = (key: FilterKey, label: string, remove: Partial<SearchFilters> = { [key]: undefined }) =>
    chips.push({ key, label, remove });
  if (f.q) add('q', `текст: «${f.q}»`);
  if (f.taxon_id) add('taxon_id', `вид: ${get('taxon', f.taxon_id) ?? `#${f.taxon_id}`}`);
  if (f.taxon) add('taxon', `вид (строкой): ${f.taxon}`);
  if (f.group) add('group', `группа: ${f.group}`);
  f.person?.forEach((p) =>
    add('person', `наблюдатель: ${p}`, { person: f.person?.filter((x) => x !== p) }));
  if (f.place_id) add('place_id', `место: ${get('place', f.place_id) ?? `#${f.place_id}`}`);
  if (f.place) add('place', `место (строкой): ${f.place}`);
  if (f.phenophase_id) add('phenophase_id', `фенофаза: ${get('phenophase', f.phenophase_id) ?? `#${f.phenophase_id}`}`);
  if (f.indicator) add('indicator', `показатель: ${f.indicator}`);
  if (f.record_type) add('record_type', `тип: ${RECORD_TYPES[f.record_type]}`);
  if (f.document_id) add('document_id', `документ: ${get('document', f.document_id) ?? `#${f.document_id}`}`);
  if (f.date_from) add('date_from', `с ${formatDate(f.date_from)}`);
  if (f.date_to) add('date_to', `по ${formatDate(f.date_to)}`);
  if (f.years?.length) add('years', `годы наблюдения: ${f.years.join(', ')}`);
  if (f.doc_years?.length) add('doc_years', `годы книг: ${f.doc_years.join(', ')}`);
  if (f.status) add('status', `статус: ${STATUS_LABELS[f.status]}`);
  if (f.flagged !== undefined) add('flagged', f.flagged ? 'только сомнительные' : 'только без флагов');
  if (f.sort) add('sort', `сортировка по ${SORT_LABELS[f.sort] ?? f.sort}${f.order === 'desc' ? ' ↓' : ' ↑'}`, { sort: undefined, order: undefined });
  return chips;
}

export default function FilterChips({ filters, onChange, onEdit }: {
  filters: SearchFilters;
  onChange: (patch: Partial<SearchFilters>) => void;
  onEdit: (key: FilterKey) => void;
}) {
  const chips = useChips(filters);
  if (!chips.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-label="Активные фильтры">
      {chips.map((c, i) => (
        <span key={`${c.key}-${i}`} className="chip">
          <button type="button" className="hover:underline" title="Поправить" onClick={() => onEdit(c.key)}>
            {c.label}
          </button>
          <button
            type="button"
            className="ml-0.5 rounded-full px-1 text-moss-600 hover:bg-moss-200"
            aria-label={`Удалить фильтр «${c.label}»`}
            onClick={() => onChange(c.remove)}
          >
            ×
          </button>
        </span>
      ))}
      {chips.length > 1 && (
        <button
          type="button"
          className="text-xs text-stone-500 underline"
          onClick={() => onChange({ ...(Object.fromEntries(chips.map((c) => [c.key, undefined])) as Partial<SearchFilters>), order: undefined })}
        >
          сбросить всё
        </button>
      )}
    </div>
  );
}
