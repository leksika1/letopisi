import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { SearchFilters } from '../api/types';
import { clean, filtersFromParams, writeFilters } from './filters';

/** Фильтры живут в URL: ссылкой на страницу можно поделиться. Смена фильтров сбрасывает страницу. */
export function useUrlFilters() {
  const [sp, setSp] = useSearchParams();
  const filters = useMemo(() => filtersFromParams(sp), [sp]);
  const page = Math.max(1, Number(sp.get('page')) || 1);

  const setFilters = useCallback(
    (next: SearchFilters | ((f: SearchFilters) => SearchFilters)) => {
      setSp((prev) => {
        const cur = filtersFromParams(prev);
        const value = clean(typeof next === 'function' ? next(cur) : next);
        const out = writeFilters(prev, value);
        out.delete('page');
        return out;
      });
    },
    [setSp],
  );

  const patch = useCallback(
    (p: Partial<SearchFilters>) => setFilters((f) => ({ ...f, ...p })),
    [setFilters],
  );

  /**
   * Одно обновление URL: прочие параметры (tab, page, page_size, obs) и, при необходимости, фильтры.
   * Несколько отдельных вызовов setSearchParams в одном обработчике затирали бы друг друга.
   */
  const update = useCallback(
    (params: Record<string, string | null>, filterPatch?: Partial<SearchFilters>) => {
      setSp((prev) => {
        let out = new URLSearchParams(prev);
        if (filterPatch) {
          out = writeFilters(out, clean({ ...filtersFromParams(prev), ...filterPatch }));
          out.delete('page');
        }
        for (const [k, v] of Object.entries(params)) {
          if (v === null || v === '') out.delete(k);
          else out.set(k, v);
        }
        return out;
      });
    },
    [setSp],
  );

  const setParam = useCallback((key: string, value: string | null) => update({ [key]: value }), [update]);

  return { filters, setFilters, patch, page, setParam, update, searchParams: sp };
}
