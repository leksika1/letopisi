import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

/**
 * Подписи для id в фильтрах (taxon_id → «Лось», place_id → «Каскама» …).
 * Отдельного эндпоинта «справочник по id» в контракте нет, поэтому подписи собираются
 * из выбранных подсказок, из labels в результатах поиска и из /documents.
 */
export type LabelKind = 'taxon' | 'place' | 'phenophase' | 'document' | 'person';
type Cache = Record<string, string>;

const STORAGE_KEY = 'letopisi.labels';
const Ctx = createContext<{ get: (k: LabelKind, id: number) => string | undefined; put: (k: LabelKind, id: number, label: string) => void } | null>(null);

function load(): Cache {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as Cache;
  } catch {
    return {};
  }
}

export function LabelCacheProvider({ children }: { children: ReactNode }) {
  const [cache, setCache] = useState<Cache>(load);
  const put = useCallback((k: LabelKind, id: number, label: string) => {
    setCache((c) => {
      const key = `${k}:${id}`;
      if (c[key] === label) return c;
      const next = { ...c, [key]: label };
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* приватный режим */ }
      return next;
    });
  }, []);
  const value = useMemo(() => ({ get: (k: LabelKind, id: number) => cache[`${k}:${id}`], put }), [cache, put]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLabels() {
  const v = useContext(Ctx);
  if (!v) throw new Error('LabelCacheProvider отсутствует');
  return v;
}
