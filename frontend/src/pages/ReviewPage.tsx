import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useObservations } from '../api/hooks';
import type { SearchFilters, Status } from '../api/types';
import ObservationPanel from '../components/ObservationPanel';
import { FlagIcon, StatusBadge } from '../components/StatusBadge';
import { FLAG_LABELS, formatObsDate } from '../lib/labels';

const isTyping = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName));

/** Очередь записей с флагами. J — следующая, K — предыдущая. */
export default function ReviewPage() {
  const [sp, setSp] = useSearchParams();
  const STATUSES: Status[] = ['auto', 'edited', 'verified', 'rejected'];
  const raw = sp.get('status') ?? 'auto';
  const status = raw === 'all' ? 'all' : (STATUSES.find((s) => s === raw) ?? 'auto');
  const filters: SearchFilters = useMemo(
    () => ({ flagged: true, status: status === 'all' ? undefined : status, sort: 'id', order: 'asc' }),
    [status],
  );
  const { data, isLoading } = useObservations(filters, 1, 500);
  const items = useMemo(() => data?.items ?? [], [data]);
  const [current, setCurrent] = useState<number | null>(Number(sp.get('obs')) || null);

  useEffect(() => {
    if (current === null && items[0]) setCurrent(items[0].id);
  }, [items, current]);

  const idx = items.findIndex((o) => o.id === current);
  const go = (delta: number) => {
    const next = items[Math.min(items.length - 1, Math.max(0, (idx < 0 ? 0 : idx) + delta))];
    if (next) setCurrent(next.id);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'j' || e.key === 'о') { e.preventDefault(); go(1); }
      if (e.key === 'k' || e.key === 'л') { e.preventDefault(); go(-1); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  useEffect(() => {
    document.querySelector(`[data-review-id="${current}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [current]);

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
      <section className="card flex max-h-[calc(100vh-7rem)] flex-col">
        <div className="border-b border-stone-200 p-3">
          <h1 className="text-lg">На проверку</h1>
          <p className="text-xs text-stone-500">
            Записи с флагами. <kbd className="rounded border px-1">J</kbd> — следующая, <kbd className="rounded border px-1">K</kbd> — предыдущая.
          </p>
          <select
            className="input mt-2"
            value={status}
            onChange={(e) => { setSp({ status: e.target.value }); setCurrent(null); }}
          >
            <option value="auto">только непроверенные (авто)</option>
            <option value="edited">исправленные</option>
            <option value="verified">подтверждённые</option>
            <option value="rejected">отклонённые</option>
            <option value="all">все, кроме отклонённых</option>
          </select>
          <div className="mt-1 text-xs text-stone-500">{data ? `${items.length} записей` : ''}{idx >= 0 ? ` · ${idx + 1}-я` : ''}</div>
        </div>
        <ul className="flex-1 divide-y divide-stone-100 overflow-auto">
          {isLoading && <li className="p-3 text-stone-500">Загрузка…</li>}
          {data && !items.length && <li className="p-3 text-stone-500">Очередь пуста 🎉</li>}
          {items.map((o) => (
            <li key={o.id} data-review-id={o.id}>
              <button
                type="button"
                onClick={() => setCurrent(o.id)}
                className={`w-full px-3 py-2 text-left text-sm hover:bg-moss-50 ${o.id === current ? 'bg-moss-100' : ''}`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs text-stone-400">№{o.id}</span>
                  <span className="flex-1 truncate">{o.labels.taxon_name_ru ?? o.object_raw}</span>
                  <StatusBadge status={o.status} />
                  <FlagIcon flags={o.flags} />
                </div>
                <div className="truncate text-xs text-stone-500">
                  {formatObsDate(o)} · {o.flags.map((f) => FLAG_LABELS[f]).join('; ')}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <div className="lg:sticky lg:top-4 lg:h-[calc(100vh-7rem)]">
        {current ? (
          <ObservationPanel
            id={current}
            onClose={() => setCurrent(null)}
            onDone={() => go(1)}
            nav={
              <div className="flex gap-1">
                <button type="button" className="btn px-2" onClick={() => go(-1)} disabled={idx <= 0} title="Предыдущая (K)">↑ K</button>
                <button type="button" className="btn px-2" onClick={() => go(1)} disabled={idx >= items.length - 1} title="Следующая (J)">↓ J</button>
              </div>
            }
          />
        ) : (
          <div className="card p-6 text-stone-500">Выберите запись слева.</div>
        )}
      </div>
    </div>
  );
}
