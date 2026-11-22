import { useEffect, useState } from 'react';

const KEY = 'letopisi.editor';

/** Имя проверяющего для PATCH (поле editor обязательно). Хранится в браузере. */
export function useEditorName(): [string, (v: string) => void] {
  const [name, setName] = useState(() => localStorage.getItem(KEY) ?? '');
  useEffect(() => {
    const onStorage = (e: StorageEvent) => { if (e.key === KEY) setName(e.newValue ?? ''); };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);
  return [name, (v: string) => {
    setName(v);
    localStorage.setItem(KEY, v);
    window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue: v }));
  }];
}
