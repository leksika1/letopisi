import { useEffect, useState, type ReactNode } from 'react';
import { usePatchObservation, useObservation } from '../api/hooks';
import type { ObservationDetail, ObservationEdit, ObservationListItem, ObservationPatch } from '../api/types';
import { useEditorName } from '../lib/editor';
import { DATE_PRECISION, FIELD_LABELS, RECORD_TYPES, STATUS_LABELS, formatObsDate } from '../lib/labels';
import EditForm from './EditForm';
import SourceView from './SourceView';
import { FlagList, StatusBadge } from './StatusBadge';

const ACTION_LABEL: Record<ObservationEdit['action'], string> = { edit: 'Исправлено', verify: 'Подтверждено', reject: 'Отклонено' };

function fmt(v: unknown): string {
  if (v === null || v === undefined || v === '') return '∅';
  if (Array.isArray(v)) return v.length ? v.join(', ') : '∅';
  return String(v);
}

export function EditHistory({ edits }: { edits: ObservationEdit[] }) {
  if (!edits.length) return <p className="text-sm text-stone-400">Правок ещё не было.</p>;
  return (
    <ol className="space-y-2" data-testid="edit-history">
      {[...edits].reverse().map((e) => (
        <li key={e.id} className="rounded border border-stone-200 bg-stone-50 p-2 text-sm">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <b>{ACTION_LABEL[e.action]}</b>
            <span>{e.editor}</span>
            <span className="text-xs text-stone-500">{new Date(e.created_at).toLocaleString('ru-RU')}</span>
            <span className="text-xs text-stone-500">{STATUS_LABELS[e.status_before]} → {STATUS_LABELS[e.status_after]}</span>
          </div>
          {Object.keys(e.changes).length > 0 && (
            <table className="mt-1 w-full text-xs">
              <tbody>
                {Object.entries(e.changes as Record<string, { old: unknown; new: unknown }>).map(([k, c]) => (
                  <tr key={k}>
                    <td className="pr-2 align-top text-stone-500">{FIELD_LABELS[k] ?? k}</td>
                    <td className="align-top"><del className="text-red-700">{fmt(c.old)}</del> → <ins className="text-emerald-800 no-underline">{fmt(c.new)}</ins></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {e.comment && <p className="mt-1 italic text-stone-600">«{e.comment}»</p>}
        </li>
      ))}
    </ol>
  );
}

function Field({ label, value, raw }: { label: string; value: ReactNode; raw?: string | null }) {
  const empty = value === null || value === undefined || value === '';
  if (empty && !raw) return null;
  return (
    <div className="grid grid-cols-[9.5rem_1fr] gap-2 py-1 text-sm">
      <dt className="text-stone-500">{label}</dt>
      <dd>
        {empty ? <span className="text-stone-400">—</span> : value}
        {raw && raw !== value && (
          <div className="text-xs text-stone-500">в тексте: <q className="font-serif">{raw}</q></div>
        )}
      </dd>
    </div>
  );
}

function Fields({ o }: { o: ObservationListItem }) {
  const l = o.labels;
  const value = o.value_num ?? null;
  return (
    <dl className="divide-y divide-stone-100">
      <Field label="Тип записи" value={RECORD_TYPES[o.record_type]} />
      <Field
        label="Объект"
        value={l.taxon_name_ru || l.taxon_name_lat ? <>{l.taxon_name_ru} <i className="text-stone-600">{l.taxon_name_lat}</i>{l.taxon_group && <span className="text-xs text-stone-400"> · {l.taxon_group}</span>}</> : null}
        raw={o.object_raw}
      />
      <Field
        label="Дата"
        value={formatObsDate(o) ? `${formatObsDate(o)}${o.date_precision ? ` (${DATE_PRECISION[o.date_precision] ?? o.date_precision})` : ''}` : null}
        raw={o.date_raw}
      />
      <Field label="Год наблюдения" value={o.obs_year} />
      <Field label="Место" value={l.place_name} raw={o.toponym_raw} />
      <Field label="Координаты" value={o.coords_raw} />
      <Field label="Местообитание" value={o.habitat_raw} />
      <Field label="Характер явления" value={o.event_raw} />
      <Field label="Фенофаза" value={l.phenophase_code ? `${l.phenophase_code} — ${l.phenophase_name ?? 'название не найдено'}` : null} raw={o.phenophase_raw} />
      <Field label="Показатель" value={o.indicator} />
      <Field label="Значение" value={value !== null ? `${value}${o.unit ? ` ${o.unit}` : ''}` : null} raw={o.value_raw} />
      <Field label="Наблюдатели" value={l.persons.join(', ')} raw={o.observers_raw} />
      {o.record_type === 'publication' && (
        <>
          <Field label="Авторы" value={o.pub_authors} />
          <Field label="Название" value={o.pub_title} />
          <Field label="Издание" value={o.pub_venue} />
          <Field label="Год публикации" value={o.pub_year} />
        </>
      )}
      <Field label="Страницы" value={o.printed_pages ? `${o.printed_pages}${l.book_no ? `, том ${l.book_no}` : ''}` : null} />
      <Field label="Примечание" value={o.note} />
      <Field label="Уверенность" value={`${Math.round(o.confidence * 100)}% · ${o.extractor_version}`} />
    </dl>
  );
}

export default function ObservationPanel({ id, onClose, onDone, nav }: {
  id: number;
  onClose: () => void;
  /** Вызывается после verify/reject (страница «На проверку» переходит к следующей записи). */
  onDone?: (d: ObservationDetail) => void;
  nav?: ReactNode;
}) {
  const { data, isLoading, isError, error } = useObservation(id);
  const patch = usePatchObservation(id);
  const [editor, setEditor] = useEditorName();
  const [mode, setMode] = useState<'view' | 'edit' | 'reject'>('view');
  const [rejectComment, setRejectComment] = useState('');

  useEffect(() => { setMode('view'); setRejectComment(''); patch.reset(); }, [id]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && mode === 'view') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mode, onClose]);

  const send = (p: ObservationPatch) =>
    patch.mutate(p, {
      onSuccess: (d) => {
        setMode('view');
        if (p.action !== 'edit') onDone?.(d);
      },
    });

  return (
    <aside className="card flex h-full flex-col overflow-hidden" aria-label="Карточка записи">
      <div className="flex items-start gap-2 border-b border-stone-200 p-3">
        <div className="min-w-0 flex-1">
          <div className="text-xs text-stone-500">Запись №{id}</div>
          <h2 className="truncate text-lg">{data ? (data.observation.labels.taxon_name_ru ?? data.observation.object_raw) : '…'}</h2>
        </div>
        {nav}
        <button type="button" className="btn px-2" onClick={onClose} aria-label="Закрыть">✕</button>
      </div>
      <div className="flex-1 space-y-4 overflow-auto p-3">
        {isLoading && <p className="text-stone-500">Загрузка…</p>}
        {isError && <p className="text-red-700">Не удалось загрузить запись: {error.message}</p>}
        {data && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={data.observation.status} />
              <div className="ml-auto flex flex-wrap gap-1.5">
                <button type="button" className="btn border-emerald-300 text-emerald-800 hover:bg-emerald-50" disabled={patch.isPending || !editor.trim()}
                  onClick={() => send({ action: 'verify', editor })}>✓ Верно</button>
                <button type="button" className="btn" disabled={!editor.trim()} onClick={() => setMode(mode === 'edit' ? 'view' : 'edit')}>✎ Исправить</button>
                <button type="button" className="btn-danger" disabled={patch.isPending || !editor.trim()} onClick={() => setMode('reject')}>✕ Отклонить</button>
              </div>
            </div>
            {!editor.trim() && (
              <div className="flex items-center gap-2 rounded border border-amber-200 bg-amber-50 p-2 text-sm">
                Чтобы подтверждать и править, укажите проверяющего:
                <input className="input w-48" placeholder="Фамилия И.О." onBlur={(e) => setEditor(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') setEditor(e.currentTarget.value); }} />
              </div>
            )}
            {patch.isError && mode !== 'edit' && <p className="text-sm text-red-700">{patch.error.message}</p>}
            {mode === 'reject' && (
              <div className="space-y-2 rounded border border-red-200 bg-red-50 p-2">
                <textarea className="input" rows={2} placeholder="Почему запись неверна (необязательно)" value={rejectComment} onChange={(e) => setRejectComment(e.target.value)} autoFocus />
                <div className="flex gap-2">
                  <button type="button" className="btn-danger" disabled={patch.isPending}
                    onClick={() => send({ action: 'reject', editor, comment: rejectComment.trim() || null })}>Отклонить запись</button>
                  <button type="button" className="btn" onClick={() => setMode('view')}>Отмена</button>
                </div>
              </div>
            )}
            {mode === 'edit' ? (
              <section>
                <h3 className="mb-2 text-base">Исправление</h3>
                <EditForm
                  key={data.observation.updated_at ?? id}
                  observation={data.observation}
                  editor={editor}
                  pending={patch.isPending}
                  error={patch.isError ? patch.error.message : null}
                  onSubmit={send}
                  onCancel={() => setMode('view')}
                />
              </section>
            ) : (
              <>
                <section>
                  <Fields o={data.observation} />
                </section>
                <section>
                  <h3 className="mb-1 text-base">Флаги</h3>
                  <FlagList flags={data.observation.flags} />
                </section>
              </>
            )}
            <section>
              <h3 className="mb-1 text-base">История правок</h3>
              <EditHistory edits={data.edits} />
            </section>
            <section>
              <h3 className="mb-1 text-base">Источник</h3>
              <div className="mb-2 text-sm text-stone-600">
                {data.document.title}
                {data.section && <div className="text-xs text-stone-500">Раздел {data.section.code ? `${data.section.code}. ` : ''}{data.section.title}</div>}
                {data.section?.authors_raw && <div className="text-xs text-stone-500">Авторы раздела: {data.section.authors_raw}</div>}
              </div>
              <SourceView observation={data.observation} chunk={data.chunk} document={data.document} />
            </section>
          </>
        )}
      </div>
    </aside>
  );
}
