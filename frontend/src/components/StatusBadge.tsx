import type { Flag, Status } from '../api/types';
import { FLAG_LABELS, INFO_FLAGS, STATUS_LABELS } from '../lib/labels';

const STYLE: Record<Status, string> = {
  auto: 'bg-stone-100 text-stone-600 border-stone-200',
  verified: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  edited: 'bg-sky-50 text-sky-800 border-sky-200',
  rejected: 'bg-red-50 text-red-700 border-red-200',
};
const ICON: Record<Status, string> = { auto: '○', verified: '✓', edited: '✎', rejected: '✕' };

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs ${STYLE[status]}`}>
      <span aria-hidden>{ICON[status]}</span>
      {STATUS_LABELS[status]}
    </span>
  );
}

export function FlagIcon({ flags }: { flags: Flag[] }) {
  if (!flags.length) return null;
  const serious = flags.filter((f) => !INFO_FLAGS.includes(f));
  return (
    <span
      className={`ml-1 cursor-help text-sm ${serious.length ? 'text-amber-600' : 'text-stone-400'}`}
      title={flags.map((f) => FLAG_LABELS[f]).join('\n')}
      aria-label={`Флаги: ${flags.map((f) => FLAG_LABELS[f]).join('; ')}`}
    >
      {serious.length ? '⚠' : 'ⓘ'}
      {flags.length > 1 && <sup>{flags.length}</sup>}
    </span>
  );
}

export function FlagList({ flags }: { flags: Flag[] }) {
  if (!flags.length) return <span className="text-sm text-stone-400">нет</span>;
  return (
    <ul className="space-y-1">
      {flags.map((f) => (
        <li key={f} className={`text-sm ${INFO_FLAGS.includes(f) ? 'text-stone-500' : 'text-amber-800'}`}>
          {INFO_FLAGS.includes(f) ? 'ⓘ' : '⚠'} {FLAG_LABELS[f]} <code className="text-xs text-stone-400">{f}</code>
        </li>
      ))}
    </ul>
  );
}
