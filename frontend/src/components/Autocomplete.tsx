import { useEffect, useId, useRef, useState } from 'react';
import { useSuggest } from '../api/hooks';
import type { SuggestItem, SuggestKind } from '../api/types';

interface Props {
  kind: SuggestKind;
  placeholder?: string;
  /** Текст в поле, когда пользователь ничего не вводит (например, подпись выбранного значения). */
  value?: string;
  onPick: (item: SuggestItem) => void;
  /** Enter без выбора подсказки — свободный текст (например, род или вариант названия). */
  onFreeText?: (text: string) => void;
  dataFilter?: string;
  clearOnPick?: boolean;
  autoFocus?: boolean;
}

export default function Autocomplete({ kind, placeholder, value = '', onPick, onFreeText, dataFilter, clearOnPick, autoFocus }: Props) {
  const [text, setText] = useState(value);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [debounced, setDebounced] = useState('');
  const listId = useId();
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => setText(value), [value]);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text), 180);
    return () => clearTimeout(t);
  }, [text]);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const { data: items = [], isFetching } = useSuggest(kind, open && debounced !== value ? debounced : '');

  const pick = (it: SuggestItem) => {
    onPick(it);
    setText(clearOnPick ? '' : it.label);
    setOpen(false);
  };

  return (
    <div ref={wrap} className="relative">
      <input
        className="input"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        data-filter={dataFilter}
        placeholder={placeholder}
        value={text}
        autoFocus={autoFocus}
        onChange={(e) => { setText(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => text && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === 'Escape') setOpen(false);
          else if (e.key === 'Enter') {
            e.preventDefault();
            const it = open ? items[active] : undefined;
            if (it) pick(it);
            else if (onFreeText && text.trim()) { onFreeText(text.trim()); setOpen(false); }
          }
        }}
      />
      {open && debounced && (items.length > 0 || isFetching) && (
        <ul id={listId} role="listbox" className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded border border-stone-200 bg-white shadow-lg">
          {items.map((it, i) => (
            <li
              key={it.id}
              role="option"
              aria-selected={i === active}
              className={`cursor-pointer px-2 py-1.5 text-sm ${i === active ? 'bg-moss-50' : ''}`}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => { e.preventDefault(); pick(it); }}
            >
              <div>{it.label}</div>
              {it.sublabel && <div className="text-xs italic text-stone-500">{it.sublabel}</div>}
            </li>
          ))}
          {isFetching && items.length === 0 && <li className="px-2 py-1.5 text-sm text-stone-400">Поиск…</li>}
        </ul>
      )}
      {onFreeText && open && text.trim() && !isFetching && debounced === text && items.length === 0 && (
        <div className="absolute z-30 mt-1 w-full rounded border border-stone-200 bg-white px-2 py-1.5 text-xs text-stone-500 shadow">
          Нет в справочнике. Enter — искать как текст «{text.trim()}»
        </div>
      )}
    </div>
  );
}
