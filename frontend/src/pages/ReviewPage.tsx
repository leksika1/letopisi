import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { reviewPageSize, useReviewPage } from '../api/hooks';
import type { SearchFilters, Status } from '../api/types';
import ObservationPanel from '../components/ObservationPanel';
import { FlagIcon, StatusBadge } from '../components/StatusBadge';
import { FLAG_LABELS, formatObsDate } from '../lib/labels';

const isTyping = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName));

function positive(raw: string | null): number | null {
  const value = Number(raw);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

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
  const requestedPage = positive(sp.get('page'));
  const requestedId = positive(sp.get('obs'));
  const { data, isLoading, isError } = useReviewPage(filters, requestedPage ?? 1, requestedId);
  const [closed, setClosed] = useState(false);
  const items = data?.items ?? [];
  const page = data?.page ?? requestedPage ?? 1;
  const pages = Math.ceil((data?.total ?? 0) / reviewPageSize);
  const requestedIndex = items.findIndex((o) => o.id === requestedId);
  const idx = requestedIndex >= 0 ? requestedIndex : Math.min((positive(sp.get('row')) ?? 1) - 1, items.length - 1);
  const current = closed ? null : items[idx]?.id ?? null;
  const position = (page - 1) * reviewPageSize + idx;

  const select = (targetPage: number, row: number, id?: number) => {
    setClosed(false);
    setSp((previous) => {
      const next = new URLSearchParams(previous);
      next.set('page', String(targetPage));
      if (id) next.set('obs', String(id));
      else next.delete('obs');
      if (row > 0) next.set('row', String(row + 1));
      else next.delete('row');
      return next;
    }, { replace: true });
  };

  const go = (delta: number) => {
    if (!data?.total) return;
    const next = Math.max(0, Math.min(data.total - 1, position + delta));
    const targetPage = Math.floor(next / reviewPageSize) + 1;
    const row = next % reviewPageSize;
    select(targetPage, row, targetPage === page ? items[row]?.id : undefined);
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
            onChange={(e) => { setSp({ status: e.target.value }); setClosed(false); }}
          >
            <option value="auto">только непроверенные (авто)</option>
            <option value="edited">исправленные</option>
            <option value="verified">подтверждённые</option>
            <option value="rejected">отклонённые</option>
            <option value="all">все, кроме отклонённых</option>
          </select>
          <div className="mt-1 text-xs text-stone-500">{data ? `${data.total} записей` : ''}{current ? ` · ${position + 1}-я` : ''}</div>
          {pages > 1 && (
            <nav className="mt-2 flex items-center gap-2 text-xs" aria-label="Страницы очереди">
              <button type="button" className="btn" disabled={page <= 1} onClick={() => select(page - 1, 0)}>Назад</button>
              <span>{page} / {pages}</span>
              <button type="button" className="btn" disabled={page >= pages} onClick={() => select(page + 1, 0)}>Вперёд</button>
            </nav>
          )}
        </div>
        <ul className="flex-1 divide-y divide-stone-100 overflow-auto">
          {isLoading && <li className="p-3 text-stone-500">Загрузка…</li>}
          {isError && <li className="p-3">Не удалось загрузить очередь.</li>}
          {data && !items.length && <li className="p-3 text-stone-500">Очередь пуста 🎉</li>}
          {items.map((o, i) => (
            <li key={o.id} data-review-id={o.id}>
              <button
                type="button"
                onClick={() => select(page, i, o.id)}
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
            onClose={() => setClosed(true)}
            onDone={({ observation }) => {
              const remains = status === 'all' ? observation.status !== 'rejected' : observation.status === status;
              if (remains) go(1);
              else select(page, idx, items[idx + 1]?.id);
            }}
            nav={
              <div className="flex gap-1">
                <button type="button" className="btn px-2" onClick={() => go(-1)} disabled={position <= 0} title="Предыдущая (K)">↑ K</button>
                <button type="button" className="btn px-2" onClick={() => go(1)} disabled={position >= (data?.total ?? 0) - 1} title="Следующая (J)">↓ J</button>
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
