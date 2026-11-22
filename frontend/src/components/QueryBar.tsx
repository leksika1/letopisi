import { useEffect, useState } from 'react';
import { useParseQuery } from '../api/hooks';
import type { QueryParseResponse, SearchFilters } from '../api/types';

export default function QueryBar({ initialText, onFilters }: {
  initialText?: string;
  onFilters: (f: SearchFilters, text: string) => void;
}) {
  const [text, setText] = useState(initialText ?? '');
  const [result, setResult] = useState<QueryParseResponse | null>(null);
  const parse = useParseQuery();
  useEffect(() => { if (initialText !== undefined) { setText(initialText); setResult(null); } }, [initialText]);

  const submit = () => {
    const t = text.trim();
    if (!t) return;
    parse.mutate(t, {
      onSuccess: (r) => { setResult(r); onFilters(r.filters, t); },
    });
  };

  return (
    <div>
      <form
        className="flex gap-2"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        <input
          className="input flex-1 py-2 text-base"
          placeholder="Спросите обычными словами: «все наблюдения за лосем в 1995, 1996 и 2020 годах по хронологии»"
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Запрос обычными словами"
        />
        <button className="btn-primary px-4" disabled={parse.isPending || !text.trim()}>
          {parse.isPending ? 'Разбираю…' : 'Найти'}
        </button>
      </form>
      {parse.isError && <p className="mt-1 text-sm text-red-700">Не удалось разобрать запрос: {parse.error.message}</p>}
      {result && (
        <div className="mt-1.5 text-sm text-stone-600">
          <span className="text-stone-400">Понято как:</span> {result.explanation}
          {result.warnings.map((w) => (
            <div key={w} className="text-amber-700">⚠ {w}</div>
          ))}
        </div>
      )}
    </div>
  );
}
