import { Link } from 'react-router-dom';
import { useDeleteSavedQuery, useSavedQueries } from '../api/hooks';
import { useChips } from '../components/FilterChips';
import type { SavedQuery } from '../api/types';
import { writeFilters } from '../lib/filters';

function Row({ q, onDelete, deleting }: { q: SavedQuery; onDelete: () => void; deleting: boolean }) {
  const chips = useChips(q.filters);
  const href = `/?${writeFilters(new URLSearchParams(), q.filters).toString()}`;
  return (
    <li className="flex flex-wrap items-start gap-3 py-3">
      <div className="min-w-0 flex-1">
        <Link to={href} className="font-medium text-moss-800 hover:underline">{q.name}</Link>
        {q.text && <p className="text-sm italic text-stone-500">«{q.text}»</p>}
        <div className="mt-1 flex flex-wrap gap-1">
          {chips.map((c, i) => <span key={i} className="chip text-xs">{c.label}</span>)}
        </div>
        {q.created_at && <div className="mt-1 text-xs text-stone-400">{new Date(q.created_at).toLocaleString('ru-RU')}</div>}
      </div>
      <Link to={href} className="btn-primary">Открыть</Link>
      <button type="button" className="btn-danger" disabled={deleting} onClick={onDelete}>Удалить</button>
    </li>
  );
}

export default function SavedQueriesPage() {
  const { data, isLoading, isError, error } = useSavedQueries();
  const del = useDeleteSavedQuery();
  return (
    <section className="card p-4">
      <h1 className="mb-2 text-xl">Сохранённые запросы</h1>
      {isLoading && <p className="text-stone-500">Загрузка…</p>}
      {isError && <p className="text-red-700">{error.message}</p>}
      {data && !data.length && <p className="text-stone-500">Пока ничего не сохранено. Кнопка «Сохранить запрос» — над таблицей результатов.</p>}
      <ul className="divide-y divide-stone-100">
        {data?.map((q) => (
          <Row key={q.id} q={q} deleting={del.isPending && del.variables === q.id}
            onDelete={() => { if (confirm(`Удалить «${q.name}»?`)) del.mutate(q.id); }} />
        ))}
      </ul>
    </section>
  );
}
