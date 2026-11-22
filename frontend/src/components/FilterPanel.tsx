import { useEffect, useState, type ReactNode } from 'react';
import { useDocuments } from '../api/hooks';
import type { RecordType, SearchFilters, Status, TaxonGroup } from '../api/types';
import { parseYears } from '../lib/filters';
import { RECORD_TYPES, STATUS_LABELS, TAXON_GROUPS } from '../lib/labels';
import { useLabels } from '../lib/labelCache';
import Autocomplete from './Autocomplete';

export type FieldName =
  | 'taxon' | 'group' | 'person' | 'place' | 'phenophase' | 'indicator' | 'record_type' | 'dates'
  | 'years' | 'doc_years' | 'document' | 'status' | 'flagged' | 'q';

export const ALL_FIELDS: FieldName[] = [
  'taxon', 'group', 'person', 'place', 'phenophase', 'indicator', 'record_type', 'dates', 'years', 'doc_years',
  'document', 'status', 'flagged', 'q',
];

/** Текстовое поле, которое применяет значение по Enter или при уходе фокуса (не дёргает поиск на каждую букву). */
function CommitInput({ value, onCommit, placeholder, dataFilter, type = 'text' }: {
  value: string; onCommit: (v: string) => void; placeholder?: string; dataFilter?: string; type?: string;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <input
      className="input"
      type={type}
      data-filter={dataFilter}
      placeholder={placeholder}
      value={v}
      onChange={(e) => { setV(e.target.value); if (type === 'date') onCommit(e.target.value); }}
      onBlur={() => v !== value && onCommit(v)}
      onKeyDown={(e) => { if (e.key === 'Enter') onCommit(v); }}
    />
  );
}

function Field({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={wide ? 'sm:col-span-2' : undefined}>
      <span className="label">{label}</span>
      {children}
    </div>
  );
}

export default function FilterPanel({ filters, patch, fields = ALL_FIELDS }: {
  filters: SearchFilters;
  patch: (p: Partial<SearchFilters>) => void;
  fields?: FieldName[];
}) {
  const labels = useLabels();
  const { data: documents = [] } = useDocuments();
  useEffect(() => documents.forEach((d) => labels.put('document', d.id, d.title)), [documents, labels]);

  const render: Record<FieldName, ReactNode> = {
    taxon: (
      <Field label="Вид (рус., лат., род)" key="taxon">
        <Autocomplete
          kind="taxa"
          dataFilter="taxon_id"
          placeholder="лось, Alces, Salix…"
          value={filters.taxon_id ? (labels.get('taxon', filters.taxon_id) ?? '') : (filters.taxon ?? '')}
          onPick={(it) => {
            labels.put('taxon', it.id, it.label);
            patch({ taxon_id: it.id, taxon: undefined });
          }}
          onFreeText={(t) => patch({ taxon: t, taxon_id: undefined })}
        />
      </Field>
    ),
    group: (
      <Field label="Группа" key="group">
        <select
          className="input"
          data-filter="group"
          value={filters.group ?? ''}
          onChange={(e) => patch({ group: (e.target.value || undefined) as TaxonGroup | undefined })}
        >
          <option value="">все группы</option>
          {TAXON_GROUPS.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
      </Field>
    ),
    person: (
      <Field label="Наблюдатель (фамилия)" key="person">
        <Autocomplete
          kind="persons"
          dataFilter="person"
          placeholder="Макарова, Сметанников…"
          clearOnPick
          onPick={(it) => {
            const surname = it.label.split(' ')[0] ?? it.label;
            patch({ person: [...new Set([...(filters.person ?? []), surname])] });
          }}
          onFreeText={(t) => patch({ person: [...new Set([...(filters.person ?? []), t])] })}
        />
        {!!filters.person?.length && (
          <div className="mt-1 text-xs text-stone-500">Выбрано: {filters.person.join(', ')} (любой из них)</div>
        )}
      </Field>
    ),
    place: (
      <Field label="Место (топоним)" key="place">
        <Autocomplete
          kind="places"
          dataFilter="place_id"
          placeholder="Каскама, р. Паз…"
          value={filters.place_id ? (labels.get('place', filters.place_id) ?? '') : (filters.place ?? '')}
          onPick={(it) => {
            labels.put('place', it.id, it.label);
            patch({ place_id: it.id, place: undefined });
          }}
          onFreeText={(t) => patch({ place: t, place_id: undefined })}
        />
      </Field>
    ),
    phenophase: (
      <Field label="Фенофаза" key="phenophase">
        <Autocomplete
          kind="phenophases"
          dataFilter="phenophase_id"
          placeholder="цветение, прилёт, 5…"
          value={filters.phenophase_id ? (labels.get('phenophase', filters.phenophase_id) ?? '') : ''}
          onPick={(it) => {
            labels.put('phenophase', it.id, it.label);
            patch({ phenophase_id: it.id });
          }}
        />
      </Field>
    ),
    indicator: (
      <Field label="Показатель" key="indicator">
        <CommitInput
          dataFilter="indicator"
          placeholder="высота снега, следов на 10 км…"
          value={filters.indicator ?? ''}
          onCommit={(v) => patch({ indicator: v.trim() || undefined })}
        />
      </Field>
    ),
    record_type: (
      <Field label="Тип записи" key="record_type">
        <select
          className="input"
          data-filter="record_type"
          value={filters.record_type ?? ''}
          onChange={(e) => patch({ record_type: (e.target.value || undefined) as RecordType | undefined })}
        >
          <option value="">все</option>
          {Object.entries(RECORD_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>
    ),
    dates: (
      <Field label="Диапазон дат" key="dates" wide>
        <div className="flex items-center gap-2">
          <CommitInput type="date" dataFilter="date_from" value={filters.date_from ?? ''} onCommit={(v) => patch({ date_from: v || undefined })} />
          <span className="text-stone-400">—</span>
          <CommitInput type="date" dataFilter="date_to" value={filters.date_to ?? ''} onCommit={(v) => patch({ date_to: v || undefined })} />
        </div>
      </Field>
    ),
    years: (
      <Field label="Годы наблюдения" key="years">
        <CommitInput
          dataFilter="years"
          placeholder="1995, 1996 или 2018–2020"
          value={filters.years?.join(', ') ?? ''}
          onCommit={(v) => patch({ years: parseYears(v) })}
        />
      </Field>
    ),
    doc_years: (
      <Field label="Годы книг (тома)" key="doc_years">
        <CommitInput
          dataFilter="doc_years"
          placeholder="1995, 1996, 2020"
          value={filters.doc_years?.join(', ') ?? ''}
          onCommit={(v) => patch({ doc_years: parseYears(v) })}
        />
      </Field>
    ),
    document: (
      <Field label="Документ" key="document">
        <select
          className="input"
          data-filter="document_id"
          value={filters.document_id ?? ''}
          onChange={(e) => patch({ document_id: Number(e.target.value) || undefined })}
        >
          <option value="">все документы</option>
          {documents.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
        </select>
      </Field>
    ),
    status: (
      <Field label="Статус" key="status">
        <select
          className="input"
          data-filter="status"
          value={filters.status ?? ''}
          onChange={(e) => patch({ status: (e.target.value || undefined) as Status | undefined })}
        >
          <option value="">все, кроме отклонённых</option>
          {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>
    ),
    flagged: (
      <Field label="Достоверность" key="flagged">
        <label className="flex h-[34px] items-center gap-2 text-sm">
          <input
            type="checkbox"
            data-filter="flagged"
            checked={filters.flagged === true}
            onChange={(e) => patch({ flagged: e.target.checked ? true : undefined })}
          />
          только сомнительные (с флагами)
        </label>
      </Field>
    ),
    q: (
      <Field label="Текст (в цитате, объекте, месте)" key="q" wide>
        <CommitInput dataFilter="q" placeholder="слово из источника" value={filters.q ?? ''} onCommit={(v) => patch({ q: v.trim() || undefined })} />
      </Field>
    ),
  };

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {fields.map((f) => render[f])}
    </div>
  );
}
