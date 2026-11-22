import { Link } from 'react-router-dom';
import { useDocuments, useQuality } from '../api/hooks';
import type { FieldMetric } from '../api/types';
import { FIELD_LABELS } from '../lib/labels';

const pct = (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}%`);

const KIND: Record<string, string> = { missed: 'пропуск', spurious: 'лишняя запись', wrong_value: 'неверное значение' };
const REASON: Record<string, string> = {
  no_text_layer: 'нет текстового слоя', low_ocr: 'низкое качество OCR', image_only: 'только изображение',
  handwritten: 'рукописный текст', table_too_complex: 'слишком сложная таблица', unsupported_format: 'неподдерживаемый формат', other: 'другое',
};

function MetricRow({ m, bold }: { m: FieldMetric; bold?: boolean }) {
  return (
    <tr className={`border-b border-stone-100 ${bold ? 'font-semibold' : ''}`}>
      <td className="px-2 py-1.5">{FIELD_LABELS[m.field] ?? (m.field === 'record' ? 'Запись целиком' : m.field)} <code className="text-xs font-normal text-stone-400">{m.field}</code></td>
      <td className="px-2 text-right tabular-nums">{pct(m.precision)}</td>
      <td className="px-2 text-right tabular-nums">{pct(m.recall)}</td>
      <td className="px-2 text-right tabular-nums">{pct(m.f1)}</td>
      <td className="px-2 text-right tabular-nums">{m.n}</td>
    </tr>
  );
}

export default function QualityPage() {
  const { data: q, isLoading, isError, error } = useQuality();
  const { data: docs = [] } = useDocuments();
  const docTitle = (id: number) => docs.find((d) => d.id === id)?.title ?? `Документ ${id}`;
  if (isLoading) return <p className="text-stone-500">Загрузка…</p>;
  if (isError || !q) return <p className="text-red-700">Не удалось загрузить метрики: {error?.message}</p>;

  return (
    <div className="space-y-4">
      {q.is_placeholder && (
        <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          Числа ненастоящие: эталонная разметка ещё не готова, показана только структура отчёта (<code>is_placeholder: true</code>).
        </div>
      )}
      <section className="card p-4">
        <h1 className="text-xl">Качество извлечения</h1>
        <p className="mt-1 text-sm text-stone-500">
          Эталон: {q.gold_version} ({q.n_gold_records} записей) · извлечено: {q.n_extracted_records} · экстрактор {q.extractor_version} ·
          {' '}{new Date(q.generated_at).toLocaleString('ru-RU')}
        </p>
        <table className="mt-3 w-full max-w-3xl text-sm">
          <thead>
            <tr className="border-b border-stone-200 text-left text-xs uppercase tracking-wide text-stone-500">
              <th className="px-2 py-2">Поле</th><th className="px-2 text-right">Precision</th><th className="px-2 text-right">Recall</th>
              <th className="px-2 text-right">F1</th><th className="px-2 text-right">n</th>
            </tr>
          </thead>
          <tbody>
            <MetricRow m={q.record_level} bold />
            {q.fields.map((m) => <MetricRow key={m.field} m={m} />)}
          </tbody>
        </table>
        {q.by_document.length > 0 && (
          <>
            <h2 className="mt-4 text-base">По документам (уровень записи)</h2>
            <table className="mt-1 w-full max-w-3xl text-sm">
              <tbody>
                {q.by_document.map((d) => (
                  <tr key={d.document_id} className="border-b border-stone-100">
                    <td className="px-2 py-1.5">{docTitle(d.document_id)}</td>
                    <td className="px-2 text-right tabular-nums">P {pct(d.record_level.precision)}</td>
                    <td className="px-2 text-right tabular-nums">R {pct(d.record_level.recall)}</td>
                    <td className="px-2 text-right tabular-nums">F1 {pct(d.record_level.f1)}</td>
                    <td className="px-2 text-right tabular-nums">n {d.record_level.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>
      <section className="card p-4">
        <h2 className="mb-2 text-lg">Примеры ошибок</h2>
        {!q.error_examples.length && <p className="text-sm text-stone-500">Нет.</p>}
        <ul className="space-y-2">
          {q.error_examples.map((e, i) => (
            <li key={i} className="rounded border border-stone-200 p-2 text-sm">
              <div className="flex flex-wrap gap-x-3">
                <b>{KIND[e.kind] ?? e.kind}</b>
                {e.field && <span>поле: {FIELD_LABELS[e.field] ?? e.field}</span>}
                <span className="text-stone-500">{docTitle(e.document_id)}{e.pdf_page ? `, PDF стр. ${e.pdf_page}` : ''}</span>
                {e.observation_id && <Link className="text-moss-700 hover:underline" to={`/observations/${e.observation_id}`}>запись №{e.observation_id} →</Link>}
              </div>
              <div className="mt-1">ожидалось: <code className="text-emerald-800">{e.expected ?? '∅'}</code> · получено: <code className="text-red-700">{e.actual ?? '∅'}</code></div>
              {e.quote && <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap rounded bg-paper p-1.5 font-serif text-xs">{e.quote}</pre>}
              {e.comment && <p className="mt-1 text-xs italic text-stone-500">{e.comment}</p>}
            </li>
          ))}
        </ul>
      </section>
      <section className="card p-4">
        <h2 className="mb-2 text-lg">Неподдерживаемые страницы и документы</h2>
        {!q.unsupported.length && <p className="text-sm text-stone-500">Нет.</p>}
        <table className="w-full text-sm">
          <tbody>
            {q.unsupported.map((u, i) => (
              <tr key={i} className="border-b border-stone-100">
                <td className="px-2 py-1.5">{docTitle(u.document_id)}</td>
                <td className="px-2">{u.pdf_page ? `PDF стр. ${u.pdf_page}` : 'весь документ'}</td>
                <td className="px-2">{REASON[u.reason] ?? u.reason}</td>
                <td className="px-2 text-stone-500">{u.comment}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
