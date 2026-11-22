import { http, HttpResponse, delay } from 'msw';
import type { Document, ObservationPatch, SavedQuery, SavedQueryCreate, SuggestKind } from '../api/types';
import { filtersFromParams } from '../lib/filters';
import { createDb, nextId } from './db';
import {
  EXPORT_HEADERS, exportRow, observationDetail, parseQuery, patchObservation, phenology, placeSpecies,
  searchObservations, suggest, toCsv, withLabels,
} from './engine';
import { pageSvg } from './pageImage';
import { buildXlsx } from './xlsx';

export const db = createDb();

const err = (status: number, code: string, message: string) =>
  HttpResponse.json({ error: { code, message, details: null } }, { status });

const intParam = (v: string | readonly string[] | undefined) => {
  const n = Number(Array.isArray(v) ? v[0] : v);
  return Number.isInteger(n) && n > 0 ? n : null;
};

const SUGGEST_KINDS: SuggestKind[] = ['taxa', 'places', 'persons', 'phenophases'];

function stamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

async function sha256(buf: ArrayBuffer): Promise<string> {
  const h = await crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Имитация обработки загруженного документа: queued → processing → done. */
function simulateProcessing(doc: Document) {
  setTimeout(() => { doc.status = 'processing'; }, 2500);
  setTimeout(() => { doc.status = 'done'; }, 7000);
}

export const handlers = [
  http.get('/api/health', () => HttpResponse.json({ status: 'ok', db: true, version: 'mock' })),

  http.get('/api/observations', async ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const f = filtersFromParams(sp);
    const page = Math.max(1, Number(sp.get('page')) || 1);
    const pageSize = Math.min(500, Math.max(1, Number(sp.get('page_size')) || 50));
    const rows = searchObservations(db, f);
    await delay(120);
    return HttpResponse.json({
      items: rows.slice((page - 1) * pageSize, page * pageSize).map((o) => withLabels(db, o)),
      total: rows.length,
      page,
      page_size: pageSize,
    });
  }),

  http.get('/api/observations/:id', ({ params }) => {
    const id = intParam(params.id);
    const d = id ? observationDetail(db, id) : null;
    return d ? HttpResponse.json(d) : err(404, 'not_found', 'Запись не найдена');
  }),

  http.patch('/api/observations/:id', async ({ params, request }) => {
    const id = intParam(params.id);
    const body = (await request.json()) as ObservationPatch;
    if (!body?.action || !body.editor?.trim()) return err(422, 'validation_error', 'Нужны action и editor');
    const d = id ? patchObservation(db, id, body) : null;
    await delay(150);
    return d ? HttpResponse.json(d) : err(404, 'not_found', 'Запись не найдена');
  }),

  http.post('/api/query/parse', async ({ request }) => {
    const body = (await request.json()) as { text?: string };
    if (!body?.text?.trim()) return err(422, 'validation_error', 'Пустой запрос');
    await delay(200);
    return HttpResponse.json(parseQuery(db, body.text));
  }),

  http.get('/api/export', ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const format = sp.get('format');
    if (format !== 'xlsx' && format !== 'csv') return err(400, 'bad_request', 'format должен быть xlsx или csv');
    const base = window.location.origin;
    const rows = [EXPORT_HEADERS, ...searchObservations(db, filtersFromParams(sp)).map((o) => exportRow(db, o, base))];
    const filename = `letopisi_export_${stamp()}.${format}`;
    const disposition = `attachment; filename="${filename}"`;
    if (format === 'csv') {
      return new HttpResponse(toCsv(rows), {
        headers: { 'Content-Type': 'text/csv; charset=utf-8', 'Content-Disposition': disposition },
      });
    }
    return new HttpResponse(buildXlsx(rows), {
      headers: {
        'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'Content-Disposition': disposition,
      },
    });
  }),

  http.get('/api/phenology', ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const taxonId = intParam(sp.get('taxon_id') ?? undefined);
    if (!taxonId || !db.taxa.some((t) => t.id === taxonId)) return err(404, 'not_found', 'Вид не найден');
    const f = filtersFromParams(sp);
    return HttpResponse.json(phenology(db, taxonId, f.phenophase_id, f.years));
  }),

  http.get('/api/places/:id/species', ({ params, request }) => {
    const sp = new URL(request.url).searchParams;
    const id = intParam(params.id);
    const year = Number(sp.get('year')) || undefined;
    const r = id ? placeSpecies(db, id, year, sp.get('group') ?? undefined) : null;
    return r ? HttpResponse.json(r) : err(404, 'not_found', 'Место не найдено');
  }),

  http.get('/api/suggest/:kind', ({ params, request }) => {
    const kind = params.kind as SuggestKind;
    const sp = new URL(request.url).searchParams;
    const q = sp.get('q') ?? '';
    if (!SUGGEST_KINDS.includes(kind)) return err(400, 'bad_request', 'Неизвестный справочник');
    if (!q.trim()) return err(400, 'bad_request', 'Параметр q обязателен');
    return HttpResponse.json(suggest(db, kind, q, Math.min(50, Number(sp.get('limit')) || 10)));
  }),

  http.get('/api/documents', () => HttpResponse.json(db.documents)),

  http.post('/api/documents', async ({ request }) => {
    const form = await request.formData();
    const file = form.get('file');
    const year = Number(form.get('year'));
    const title = String(form.get('title') ?? '').trim();
    const bookNo = Number(form.get('book_no')) || null;
    if (!(file instanceof File) || !title || !(year >= 1800 && year <= 2100)) {
      return err(422, 'validation_error', 'Нужны файл, год (1800–2100) и название');
    }
    if (file.size > 300 * 1024 * 1024) return err(413, 'payload_too_large', 'Файл больше 300 МБ');
    const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
    const hash = await sha256(await file.arrayBuffer());
    if (db.documents.some((d) => d.sha256 === hash)) return err(409, 'conflict', 'Такой файл уже загружен');
    const supported = ext === 'pdf' || ext === 'docx' || ext === 'xlsx';
    const doc: Document = {
      id: nextId(db.documents),
      title,
      book_no: bookNo,
      year,
      filename: file.name,
      format: (supported ? ext : 'pdf') as Document['format'],
      pages: null,
      has_text_layer: false,
      layout: null,
      sha256: hash,
      status: supported ? 'queued' : 'unsupported',
      error: supported ? null : `Формат .${ext} не поддерживается: загрузите PDF, DOCX или XLSX`,
      created_at: new Date().toISOString(),
    };
    db.documents.push(doc);
    if (supported) simulateProcessing(doc);
    return HttpResponse.json(doc, { status: 201 });
  }),

  http.get('/api/documents/:id/file', () =>
    err(404, 'not_found', 'В режиме моков оригиналы файлов недоступны (data/raw не в git)')),

  http.get('/api/documents/:id/pages/:file', ({ params }) => {
    const id = intParam(params.id);
    const n = intParam(String(params.file).replace(/\.png$/, ''));
    const doc = db.documents.find((d) => d.id === id);
    if (!doc || !n || (doc.pages && n > doc.pages)) return err(404, 'not_found', 'Страница не найдена');
    // В моке отдаём SVG: браузер рисует картинку по Content-Type, а не по расширению.
    return new HttpResponse(pageSvg(doc, n), { headers: { 'Content-Type': 'image/svg+xml' } });
  }),

  http.get('/api/saved-queries', () => HttpResponse.json(db.savedQueries)),

  http.post('/api/saved-queries', async ({ request }) => {
    const body = (await request.json()) as SavedQueryCreate;
    if (!body?.name?.trim() || !body.filters) return err(422, 'validation_error', 'Нужны name и filters');
    const q: SavedQuery = {
      id: nextId(db.savedQueries),
      name: body.name.trim(),
      text: body.text ?? null,
      filters: body.filters,
      created_at: new Date().toISOString(),
    };
    db.savedQueries.push(q);
    return HttpResponse.json(q, { status: 201 });
  }),

  http.delete('/api/saved-queries/:id', ({ params }) => {
    const id = intParam(params.id);
    const i = db.savedQueries.findIndex((q) => q.id === id);
    if (i < 0) return err(404, 'not_found', 'Запрос не найден');
    db.savedQueries.splice(i, 1);
    return new HttpResponse(null, { status: 204 });
  }),

  http.get('/api/quality', () => HttpResponse.json(db.quality)),
];
