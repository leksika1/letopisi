import { usePhenology } from '../api/hooks';
import type { PhenologyRow } from '../api/types';
import { formatDate } from '../lib/labels';

function cellText(r: PhenologyRow): string {
  if (!r.date_from) return '—';
  const a = formatDate(r.date_from).slice(0, 5);
  if (!r.date_to || r.date_to === r.date_from) return a;
  return `${a}–${formatDate(r.date_to).slice(0, 5)}`;
}

/** Таблица «фенофаза × год» по GET /api/phenology. */
export default function PhenologyGrid({ taxonId, phenophaseId, years, onOpen }: {
  taxonId: number | undefined; phenophaseId?: number; years?: number[]; onOpen: (id: number) => void;
}) {
  const { data, isLoading, isError, error } = usePhenology(taxonId, phenophaseId, years);
  if (!taxonId) return <p className="text-sm text-stone-500">Выберите вид, чтобы увидеть даты фенофаз по годам.</p>;
  if (isLoading) return <p className="text-sm text-stone-500">Загрузка…</p>;
  if (isError) return <p className="text-sm text-red-700">{error.message}</p>;
  if (!data?.length) return <p className="text-sm text-stone-500">Для этого вида фенологических записей нет.</p>;

  const allYears = [...new Set(data.map((r) => r.year))].sort((a, b) => a - b);
  const phases: { key: string; code: string | null; name: string | null }[] = [];
  for (const r of data) {
    const key = String(r.phenophase_id ?? 'null');
    if (!phases.some((p) => p.key === key)) phases.push({ key, code: r.phenophase_code, name: r.phenophase_name });
  }
  const cell = (key: string, year: number) => data.filter((r) => String(r.phenophase_id ?? 'null') === key && r.year === year);

  return (
    <div className="overflow-x-auto">
      <table className="border-collapse text-sm" data-testid="phenology-grid">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-stone-500">
            <th className="px-2 py-1.5">Фенофаза</th>
            {allYears.map((y) => <th key={y} className="px-3 py-1.5 text-center tabular-nums">{y}</th>)}
          </tr>
        </thead>
        <tbody>
          {phases.map((p) => (
            <tr key={p.key} className="border-t border-stone-100">
              <td className="max-w-xs px-2 py-1.5">
                <b>{p.code ?? '?'}</b> <span className="text-stone-600">{p.name ?? 'название не найдено'}</span>
              </td>
              {allYears.map((y) => (
                <td key={y} className="px-3 py-1.5 text-center">
                  {cell(p.key, y).map((r) => (
                    <button key={r.observation_id} type="button" onClick={() => onOpen(r.observation_id)}
                      className="block w-full rounded px-1 tabular-nums text-moss-700 hover:bg-moss-100 hover:underline"
                      title={`Открыть запись №${r.observation_id}`}>
                      {cellText(r)}
                    </button>
                  ))}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
