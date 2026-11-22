import { useCallback, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import savedQueriesFixture from '@contracts/fixtures/saved_queries.json';
import { exportUrl } from '../api/client';
import { useCreateSavedQuery, useObservations } from '../api/hooks';
import type { SavedQuery, SearchFilters } from '../api/types';
import FilterChips from '../components/FilterChips';
import FilterPanel, { ALL_FIELDS, type FieldName } from '../components/FilterPanel';
import QueryBar from '../components/QueryBar';
import ResultsTable from '../components/ResultsTable';
import type { FilterKey } from '../lib/filters';
import { useUrlFilters } from '../lib/useUrlFilters';

/** 4 приёмочных запроса жюри (лист «примеры запросов» → contracts/fixtures/saved_queries.json). */
const EXAMPLES = savedQueriesFixture as unknown as SavedQuery[];

type Tab = 'all' | 'taxon' | 'author' | 'phenology' | 'place';
const TABS: { id: Tab; label: string; hint: string; fields: FieldName[]; preset: Partial<SearchFilters> }[] = [
  { id: 'all', label: 'Все фильтры', hint: 'Любая комбинация условий', fields: ALL_FIELDS, preset: {} },
  { id: 'taxon', label: 'Вид', hint: 'Все записи о виде (поиск по рус., лат., синониму или роду) по хронологии',
    fields: ['taxon', 'group', 'years', 'doc_years', 'record_type', 'dates'], preset: { sort: 'date_from', order: 'asc' } },
  { id: 'author', label: 'Автор', hint: 'Что зарегистрировал наблюдатель (несколько фамилий — любой из них)',
    fields: ['person', 'years', 'doc_years', 'document', 'record_type'], preset: { sort: 'date_from', order: 'asc' } },
  { id: 'phenology', label: 'Фенология', hint: 'Даты фенофаз вида по годам; клик по дате открывает запись',
    fields: ['taxon', 'phenophase', 'years', 'doc_years'], preset: { record_type: 'observation', sort: 'date_from', order: 'asc' } },
  { id: 'place', label: 'Место', hint: 'Что отмечено в месте; карта мест с координатами',
    fields: ['place', 'group', 'taxon', 'years'], preset: { sort: 'date_from', order: 'asc' } },
];
const PAGE_SIZES = [25, 50, 100, 200];

/** Какое поле панели отвечает за ключ фильтра (для «поправить» в чипе). */
const FIELD_FOR_KEY: Partial<Record<FilterKey, FieldName>> = {
  q: 'q', taxon_id: 'taxon', taxon: 'taxon', group: 'group', person: 'person', place_id: 'place', place: 'place',
  phenophase_id: 'phenophase', indicator: 'indicator', record_type: 'record_type', document_id: 'document',
  date_from: 'dates', date_to: 'dates', years: 'years', doc_years: 'doc_years', status: 'status', flagged: 'flagged',
};
const DATA_ATTR: Partial<Record<FilterKey, string>> = { taxon: 'taxon_id', place: 'place_id' };

export default function SearchPage() {
  const { filters, setFilters, patch, page, setParam, update, searchParams } = useUrlFilters();
  const params = useParams();
  const navigate = useNavigate();
  const tab = (TABS.find((t) => t.id === searchParams.get('tab'))?.id ?? 'all') as Tab;
  const tabDef = TABS.find((t) => t.id === tab) ?? TABS[0]!;
  const pageSize = PAGE_SIZES.includes(Number(searchParams.get('page_size'))) ? Number(searchParams.get('page_size')) : 50;
  const selectedId = Number(params.id ?? searchParams.get('obs')) || null;

  const [queryText, setQueryText] = useState<string | undefined>(undefined);
  const [lastText, setLastText] = useState<string | null>(null);
  const [saveName, setSaveName] = useState<string | null>(null);
  const save = useCreateSavedQuery();

  const { data, isFetching, isError, error } = useObservations(filters, page, pageSize);
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  // карточка записи появится в V-2; пока строка только выделяется (obs в URL)
  const openRecord = useCallback((id: number) => {
    const sp = new URLSearchParams(searchParams);
    sp.set('obs', String(id));
    navigate({ pathname: '/', search: sp.toString() });
  }, [navigate, searchParams]);

  const switchTab = (t: Tab) => {
    const def = TABS.find((x) => x.id === t);
    // пресет не перетирает то, что пользователь уже задал
    const preset = Object.fromEntries(
      Object.entries(def?.preset ?? {}).filter(([k]) => filters[k as keyof SearchFilters] === undefined),
    ) as Partial<SearchFilters>;
    if (preset.order && filters.sort) delete preset.order;
    update({ tab: t === 'all' ? null : t }, Object.keys(preset).length ? preset : undefined);
  };

  const editChip = (key: FilterKey) => {
    const field = FIELD_FOR_KEY[key];
    if (!field) {
      document.querySelector('table')?.scrollIntoView({ behavior: 'smooth' });
      return;
    }
    if (!tabDef.fields.includes(field)) setParam('tab', null);
    setTimeout(() => {
      const el = document.querySelector<HTMLElement>(`[data-filter="${DATA_ATTR[key] ?? key}"]`);
      el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      el?.focus();
    }, 50);
  };

  const applyExample = (q: SavedQuery) => {
    setQueryText(q.text ?? '');
    setLastText(q.text);
    setFilters(q.filters);
  };

  return (
    <div className="grid gap-4">
      <div className="min-w-0 space-y-4">
        <section className="card space-y-3 p-4">
          <QueryBar
            initialText={queryText}
            onFilters={(f, text) => { setLastText(text); setFilters(f); }}
          />
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs uppercase tracking-wide text-stone-500">Примеры запросов жюри:</span>
            {EXAMPLES.map((q, i) => (
              <button key={q.id} type="button" className="btn py-1 text-xs" title={q.text ?? q.name} onClick={() => applyExample(q)}>
                {i + 1}. {q.name.replace(/^Запрос \d+:\s*/, '')}
              </button>
            ))}
          </div>
          <FilterChips filters={filters} onChange={patch} onEdit={editChip} />
        </section>

        <section className="card p-4">
          <div className="mb-3 flex flex-wrap gap-1 border-b border-stone-200" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                type="button"
                onClick={() => switchTab(t.id)}
                className={`-mb-px border-b-2 px-3 py-1.5 text-sm ${tab === t.id ? 'border-moss-600 text-moss-900' : 'border-transparent text-stone-500 hover:text-stone-800'}`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <p className="mb-3 text-sm text-stone-500">{tabDef.hint}</p>
          <FilterPanel filters={filters} patch={patch} fields={tabDef.fields} />
        </section>

        <section className="card p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h2 className="text-lg">Результаты</h2>
            <span className="text-sm text-stone-500" data-testid="total">
              {data ? `найдено: ${data.total}` : ''}{isFetching ? ' · обновление…' : ''}
            </span>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <a className="btn" href={exportUrl(filters, 'xlsx')} download>⤓ Скачать Excel</a>
              <a className="btn" href={exportUrl(filters, 'csv')} download>⤓ CSV</a>
              {saveName === null ? (
                <button type="button" className="btn" onClick={() => setSaveName(lastText ?? '')}>☆ Сохранить запрос</button>
              ) : (
                <form
                  className="flex items-center gap-1"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (!saveName.trim()) return;
                    save.mutate({ name: saveName.trim(), text: lastText, filters }, { onSuccess: () => setSaveName(null) });
                  }}
                >
                  <input className="input w-56" autoFocus placeholder="Название запроса" value={saveName} onChange={(e) => setSaveName(e.target.value)} />
                  <button className="btn-primary" disabled={save.isPending || !saveName.trim()}>Сохранить</button>
                  <button type="button" className="btn" onClick={() => setSaveName(null)}>✕</button>
                </form>
              )}
              {save.isSuccess && saveName === null && <span className="text-xs text-emerald-700">Сохранено</span>}
            </div>
          </div>
          {isError && <p className="text-red-700">Ошибка поиска: {error.message}</p>}
          {!data && !isError && <p className="py-6 text-center text-stone-500">Загрузка…</p>}
          {data && data.items.length === 0 && <p className="py-6 text-center text-stone-500">Ничего не найдено. Ослабьте фильтры.</p>}
          {data && data.items.length > 0 && (
            <ResultsTable
              items={data.items}
              filters={filters}
              selectedId={selectedId}
              onOpen={openRecord}
              onSort={(sort, order) => patch({ sort, order })}
            />
          )}
          {data && data.total > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
              <button type="button" className="btn" disabled={page <= 1} onClick={() => setParam('page', String(page - 1))}>← Назад</button>
              <span className="tabular-nums">стр. {page} из {totalPages}</span>
              <button type="button" className="btn" disabled={page >= totalPages} onClick={() => setParam('page', String(page + 1))}>Вперёд →</button>
              <label className="ml-auto flex items-center gap-1 text-stone-500">
                по
                <select className="input w-auto" value={pageSize} onChange={(e) => update({ page: null, page_size: e.target.value === '50' ? null : e.target.value })}>
                  {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
                на странице
              </label>
            </div>
          )}
        </section>
      </div>

    </div>
  );
}
