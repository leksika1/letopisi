import { useState, type ReactNode } from 'react';
import type { EditableFields, Flag, ObservationListItem, ObservationPatch, RecordType } from '../api/types';
import { DATE_PRECISION, FIELD_LABELS, FLAG_LABELS, RECORD_TYPES } from '../lib/labels';
import { useLabels } from '../lib/labelCache';
import Autocomplete from './Autocomplete';

type Draft = Required<EditableFields>;

const TEXT_FIELDS = [
  'object_raw', 'date_raw', 'toponym_raw', 'coords_raw', 'habitat_raw', 'event_raw', 'phenophase_raw', 'indicator',
  'value_raw', 'unit', 'observers_raw', 'printed_pages', 'note',
] as const;
const PUB_FIELDS = ['pub_authors', 'pub_title', 'pub_venue'] as const;
const ALL_FLAGS = Object.keys(FLAG_LABELS) as Flag[];

function draftOf(o: ObservationListItem): Draft {
  return {
    record_type: o.record_type, object_type: o.object_type, object_raw: o.object_raw, taxon_id: o.taxon_id,
    date_raw: o.date_raw, date_from: o.date_from, date_to: o.date_to, date_precision: o.date_precision, obs_year: o.obs_year,
    toponym_raw: o.toponym_raw, place_id: o.place_id, coords_raw: o.coords_raw, habitat_raw: o.habitat_raw,
    event_raw: o.event_raw, phenophase_id: o.phenophase_id, phenophase_raw: o.phenophase_raw, indicator: o.indicator,
    value_num: o.value_num, value_raw: o.value_raw, unit: o.unit, observers_raw: o.observers_raw, person_ids: o.person_ids,
    pub_authors: o.pub_authors, pub_year: o.pub_year, pub_title: o.pub_title, pub_venue: o.pub_venue,
    printed_pages: o.printed_pages, note: o.note, flags: o.flags,
  };
}

/** Только изменённые поля — их и отправляем в PATCH. */
export function changedFields(before: Draft, after: Draft): EditableFields {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(after) as (keyof Draft)[]) {
    if (JSON.stringify(before[k]) !== JSON.stringify(after[k])) out[k] = after[k];
  }
  return out as EditableFields;
}

const nullable = (s: string) => (s.trim() === '' ? null : s);
const numOrNull = (s: string) => (s.trim() === '' || Number.isNaN(Number(s.replace(',', '.'))) ? null : Number(s.replace(',', '.')));

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid grid-cols-[10rem_1fr] items-start gap-2 py-1">
      <span className="pt-1.5 text-xs text-stone-500">{label}</span>
      <div>{children}</div>
    </label>
  );
}

export default function EditForm({ observation: o, editor, pending, error, onSubmit, onCancel }: {
  observation: ObservationListItem;
  editor: string;
  pending: boolean;
  error: string | null;
  onSubmit: (p: ObservationPatch) => void;
  onCancel: () => void;
}) {
  const labels = useLabels();
  const [initial] = useState(() => draftOf(o));
  const [d, setD] = useState<Draft>(initial);
  const [comment, setComment] = useState('');
  const [personLabels, setPersonLabels] = useState<Record<number, string>>(
    () => Object.fromEntries(o.person_ids.map((id, i) => [id, o.labels.persons[i] ?? `#${id}`])),
  );
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));
  const changes = changedFields(initial, d);
  const nChanges = Object.keys(changes).length;

  const idField = (key: 'taxon_id' | 'place_id' | 'phenophase_id', kind: 'taxa' | 'places' | 'phenophases', lk: 'taxon' | 'place' | 'phenophase', current: string | null) => (
    <div className="flex gap-1">
      <div className="flex-1">
        <Autocomplete
          kind={kind}
          value={d[key] ? (labels.get(lk, d[key] as number) ?? current ?? `#${d[key]}`) : ''}
          placeholder="не привязано"
          onPick={(it) => { labels.put(lk, it.id, it.label); set(key, it.id); }}
        />
      </div>
      {d[key] !== null && <button type="button" className="btn px-2" onClick={() => set(key, null)} title="Отвязать">×</button>}
    </div>
  );

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!nChanges) return;
        onSubmit({ action: 'edit', editor, fields: changes, comment: comment.trim() || null });
      }}
    >
      <div className="divide-y divide-stone-100">
        <Row label={FIELD_LABELS.record_type ?? ''}>
          <select className="input" value={d.record_type} onChange={(e) => set('record_type', e.target.value as RecordType)}>
            {Object.entries(RECORD_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Row>
        <Row label="Вид (справочник)">{idField('taxon_id', 'taxa', 'taxon', o.labels.taxon_name_ru ?? o.labels.taxon_name_lat)}</Row>
        <Row label="Место (справочник)">{idField('place_id', 'places', 'place', o.labels.place_name)}</Row>
        <Row label="Фенофаза (справочник)">
          {idField('phenophase_id', 'phenophases', 'phenophase', o.labels.phenophase_code ? `${o.labels.phenophase_code} — ${o.labels.phenophase_name ?? ''}` : null)}
        </Row>
        <Row label="Даты">
          <div className="flex flex-wrap items-center gap-1">
            <input type="date" className="input w-auto" value={d.date_from ?? ''} onChange={(e) => set('date_from', e.target.value || null)} />
            <span>—</span>
            <input type="date" className="input w-auto" value={d.date_to ?? ''} onChange={(e) => set('date_to', e.target.value || null)} />
            <select className="input w-auto" value={d.date_precision ?? ''} onChange={(e) => set('date_precision', (e.target.value || null) as Draft['date_precision'])}>
              <option value="">нет даты</option>
              {Object.entries(DATE_PRECISION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
        </Row>
        <Row label={FIELD_LABELS.obs_year ?? ''}>
          <input className="input w-28" inputMode="numeric" value={d.obs_year ?? ''} onChange={(e) => set('obs_year', numOrNull(e.target.value))} />
        </Row>
        <Row label={FIELD_LABELS.value_num ?? ''}>
          <input className="input w-28" inputMode="decimal" value={d.value_num ?? ''} onChange={(e) => set('value_num', numOrNull(e.target.value))} />
        </Row>
        {TEXT_FIELDS.map((k) => (
          <Row key={k} label={FIELD_LABELS[k] ?? k}>
            {k === 'note' || k === 'event_raw' ? (
              <textarea className="input" rows={2} value={d[k] ?? ''} onChange={(e) => set(k, nullable(e.target.value))} />
            ) : (
              <input className="input" value={d[k] ?? ''} onChange={(e) => set(k, k === 'object_raw' ? e.target.value : nullable(e.target.value))} />
            )}
          </Row>
        ))}
        <Row label={FIELD_LABELS.person_ids ?? ''}>
          <div className="mb-1 flex flex-wrap gap-1">
            {d.person_ids.map((id) => (
              <span key={id} className="chip">
                {personLabels[id] ?? `#${id}`}
                <button type="button" aria-label="Убрать" onClick={() => set('person_ids', d.person_ids.filter((x) => x !== id))}>×</button>
              </span>
            ))}
          </div>
          <Autocomplete
            kind="persons"
            clearOnPick
            placeholder="добавить из каталога сотрудников"
            onPick={(it) => {
              setPersonLabels((m) => ({ ...m, [it.id]: it.label }));
              if (!d.person_ids.includes(it.id)) set('person_ids', [...d.person_ids, it.id]);
            }}
          />
        </Row>
        {d.record_type === 'publication' && (
          <>
            {PUB_FIELDS.map((k) => (
              <Row key={k} label={FIELD_LABELS[k] ?? k}>
                <input className="input" value={d[k] ?? ''} onChange={(e) => set(k, nullable(e.target.value))} />
              </Row>
            ))}
            <Row label={FIELD_LABELS.pub_year ?? ''}>
              <input className="input w-28" value={d.pub_year ?? ''} onChange={(e) => set('pub_year', numOrNull(e.target.value))} />
            </Row>
          </>
        )}
        <Row label={FIELD_LABELS.flags ?? ''}>
          <div className="grid grid-cols-1 gap-0.5">
            {ALL_FLAGS.map((f) => (
              <label key={f} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={d.flags.includes(f)}
                  onChange={(e) => set('flags', e.target.checked ? [...d.flags, f] : d.flags.filter((x) => x !== f))}
                />
                {FLAG_LABELS[f]}
              </label>
            ))}
          </div>
        </Row>
        <Row label="Комментарий к правке">
          <textarea className="input" rows={2} placeholder="что и почему исправлено (сверено со сканом стр. …)" value={comment} onChange={(e) => setComment(e.target.value)} />
        </Row>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      <div className="flex items-center gap-2">
        <button className="btn-primary" disabled={pending || !nChanges || !editor.trim()}>
          {pending ? 'Сохраняю…' : `Сохранить правку${nChanges ? ` (${nChanges})` : ''}`}
        </button>
        <button type="button" className="btn" onClick={onCancel}>Отмена</button>
        {!nChanges && <span className="text-xs text-stone-500">Изменений нет</span>}
      </div>
    </form>
  );
}
