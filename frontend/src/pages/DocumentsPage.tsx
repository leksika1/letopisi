import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { HttpError } from '../api/client';
import { useDocuments, useUploadDocument } from '../api/hooks';
import type { Document } from '../api/types';

const STATUS: Record<Document['status'], { label: string; cls: string }> = {
  queued: { label: 'в очереди', cls: 'bg-stone-100 text-stone-700' },
  processing: { label: 'обрабатывается…', cls: 'bg-sky-100 text-sky-800 animate-pulse' },
  done: { label: 'обработан', cls: 'bg-emerald-100 text-emerald-800' },
  failed: { label: 'ошибка', cls: 'bg-red-100 text-red-800' },
  unsupported: { label: 'не поддерживается', cls: 'bg-amber-100 text-amber-800' },
};

function UploadForm() {
  const upload = useUploadDocument();
  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState('');
  const [year, setYear] = useState('');
  const [bookNo, setBookNo] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const message = upload.error instanceof HttpError
    ? ({ 409: 'Этот файл уже загружен.', 413: 'Файл слишком большой.', 415: 'Тип файла не поддерживается: PDF, DOCX или XLSX.' } as Record<number, string>)[upload.error.status] ?? upload.error.message
    : upload.error?.message;

  return (
    <form
      className="grid gap-3 sm:grid-cols-[1fr_7rem_7rem_auto] sm:items-end"
      onSubmit={(e) => {
        e.preventDefault();
        if (!file) return;
        const fd = new FormData();
        fd.set('file', file);
        fd.set('title', title.trim());
        fd.set('year', year);
        if (bookNo) fd.set('book_no', bookNo);
        upload.mutate(fd, {
          onSuccess: () => {
            setTitle(''); setYear(''); setBookNo(''); setFile(null);
            if (fileRef.current) fileRef.current.value = '';
          },
        });
      }}
    >
      <div className="sm:col-span-4">
        <span className="label">Файл (PDF, DOCX, XLSX)</span>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.xlsx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          className="block text-sm"
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            setFile(f);
            if (f && !title) setTitle(f.name.replace(/\.[^.]+$/, ''));
            const y = f?.name.match(/(18|19|20)\d{2}/)?.[0];
            if (y && !year) setYear(y);
          }}
        />
      </div>
      <label><span className="label">Название</span><input className="input" required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Летопись природы. Книга 4 (1997 год)" /></label>
      <label><span className="label">Год</span><input className="input" required inputMode="numeric" pattern="\d{4}" value={year} onChange={(e) => setYear(e.target.value)} /></label>
      <label><span className="label">№ книги</span><input className="input" inputMode="numeric" value={bookNo} onChange={(e) => setBookNo(e.target.value)} /></label>
      <button className="btn-primary" disabled={!file || !title.trim() || !/^\d{4}$/.test(year) || upload.isPending}>
        {upload.isPending ? 'Загрузка…' : 'Загрузить'}
      </button>
      {message && <p className="text-sm text-red-700 sm:col-span-4">{message}</p>}
      {upload.isSuccess && (
        <p className="text-sm text-emerald-700 sm:col-span-4">
          Документ «{upload.data.title}» добавлен: {STATUS[upload.data.status].label}.
        </p>
      )}
    </form>
  );
}

export default function DocumentsPage() {
  const { data, isLoading, isError, error } = useDocuments();
  return (
    <div className="space-y-4">
      <section className="card p-4">
        <h1 className="mb-3 text-xl">Документы</h1>
        {isLoading && <p className="text-stone-500">Загрузка…</p>}
        {isError && <p className="text-red-700">{error.message}</p>}
        {data && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-stone-200 text-left text-xs uppercase tracking-wide text-stone-500">
                  <th className="px-2 py-2">Документ</th><th className="px-2">Год</th><th className="px-2">Формат</th>
                  <th className="px-2">Страниц</th><th className="px-2">Текстовый слой</th><th className="px-2">Статус</th><th className="px-2" />
                </tr>
              </thead>
              <tbody>
                {data.map((d) => (
                  <tr key={d.id} className="border-b border-stone-100 align-top">
                    <td className="px-2 py-2">
                      <div>{d.title}</div>
                      <div className="text-xs text-stone-400">{d.filename}{d.book_no ? ` · книга ${d.book_no}` : ''}</div>
                    </td>
                    <td className="px-2 py-2 tabular-nums">{d.year ?? '—'}</td>
                    <td className="px-2 py-2 uppercase">{d.format}{d.layout === 'spread' ? <div className="text-xs normal-case text-stone-500">развороты</div> : null}</td>
                    <td className="px-2 py-2 tabular-nums">{d.pages ?? '—'}</td>
                    <td className="px-2 py-2">{d.has_text_layer ? 'есть' : <span className="text-stone-500">нет (нужен OCR)</span>}</td>
                    <td className="px-2 py-2">
                      <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS[d.status].cls}`}>{STATUS[d.status].label}</span>
                      {d.error && <div className="mt-1 text-xs text-red-700">{d.error}</div>}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2 text-right">
                      <Link className="text-moss-700 hover:underline" to={`/?document_id=${d.id}`}>записи</Link>
                      {' · '}
                      <a className="text-moss-700 hover:underline" href={`/api/documents/${d.id}/file`} target="_blank" rel="noreferrer">файл ↗</a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="card p-4">
        <h2 className="mb-3 text-lg">Загрузить новый документ</h2>
        <UploadForm />
        <p className="mt-2 text-xs text-stone-500">После загрузки документ ставится в очередь; статус обновляется автоматически.</p>
      </section>
    </div>
  );
}
