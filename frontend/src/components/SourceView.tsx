import { useCallback, useEffect, useRef, useState } from 'react';
import { documentFileUrl, pageImageUrl } from '../api/client';
import type { Chunk, Document, ObservationListItem } from '../api/types';
import { isPdfLocator } from '../api/types';

/** Текст фрагмента с выделенной цитатой по quote_span ([start, end), индексы символов). */
export function QuoteText({ text, span }: { text: string; span: number[] | null }) {
  const ref = useRef<HTMLElement>(null);
  // прокручиваем только ближайший контейнер с текстом, а не всю панель/страницу
  useEffect(() => {
    const mark = ref.current;
    const box = mark?.closest<HTMLElement>('[data-scroll-box]');
    if (mark && box) box.scrollTop = mark.offsetTop - box.offsetTop - box.clientHeight / 3;
  }, [span]);
  const [s, e] = span ?? [];
  if (s === undefined || e === undefined || s < 0 || e > text.length || s >= e) {
    return <pre className="whitespace-pre-wrap font-serif text-sm leading-relaxed">{text}</pre>;
  }
  return (
    <pre className="whitespace-pre-wrap font-serif text-sm leading-relaxed">
      {text.slice(0, s)}
      <mark ref={ref} className="quote" data-testid="quote-highlight">{text.slice(s, e)}</mark>
      {text.slice(e)}
    </pre>
  );
}

const ZOOMS = [0.75, 1, 1.5, 2, 3];

/**
 * Прямоугольники подсветки: bboxes записи; если их нет — строки chunk.lines (контракт v1.1),
 * пересекающиеся с quote_span. Иначе — пусто (подсветка только в тексте фрагмента).
 */
export function highlightBoxes(o: Pick<ObservationListItem, 'bboxes' | 'quote_span'>, chunk: Pick<Chunk, 'lines'> | null): { boxes: number[][]; fromLines: boolean } {
  if (o.bboxes.length) return { boxes: o.bboxes, fromLines: false };
  const [s, e] = o.quote_span ?? [];
  if (s === undefined || e === undefined || !chunk?.lines?.length) return { boxes: [], fromLines: false };
  const boxes = chunk.lines.filter((l) => l.start < e && l.end > s).map((l) => l.bbox);
  return { boxes, fromLines: boxes.length > 0 };
}

/** Картинка страницы PDF с полупрозрачными прямоугольниками по bboxes (доли 0..1 всей страницы). */
export function PdfPageView({ documentId, page, bboxes, half }: {
  documentId: number; page: number; bboxes: number[][]; half: 'L' | 'R' | null;
}) {
  const [zoom, setZoom] = useState(1);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const img = useRef<HTMLImageElement>(null);

  useEffect(() => { setLoaded(false); setFailed(false); }, [documentId, page]);

  const scrollToHighlight = useCallback(() => {
    const box = scroller.current;
    const el = img.current;
    if (!box || !el) return;
    const first = bboxes[0];
    let x = 0; let y = 0;
    if (first) { x = first[0] ?? 0; y = first[1] ?? 0; }
    else if (half === 'R') x = 0.5;
    box.scrollTo({ top: Math.max(0, el.clientHeight * y - 60), left: Math.max(0, el.clientWidth * x - 40), behavior: 'smooth' });
  }, [bboxes, half]);

  useEffect(() => { if (loaded) scrollToHighlight(); }, [loaded, zoom, scrollToHighlight]);

  const zi = ZOOMS.indexOf(zoom);
  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-sm">
        <span className="text-stone-500">PDF стр. {page}{half ? ` · ${half === 'L' ? 'левая' : 'правая'} половина разворота` : ''}</span>
        <div className="ml-auto flex items-center gap-1">
          <button type="button" className="btn px-2 py-0.5" disabled={zi <= 0} onClick={() => setZoom(ZOOMS[zi - 1] ?? zoom)} aria-label="Уменьшить">−</button>
          <span className="w-12 text-center tabular-nums">{Math.round(zoom * 100)}%</span>
          <button type="button" className="btn px-2 py-0.5" disabled={zi >= ZOOMS.length - 1} onClick={() => setZoom(ZOOMS[zi + 1] ?? zoom)} aria-label="Увеличить">+</button>
          <button type="button" className="btn px-2 py-0.5" onClick={scrollToHighlight} title="Прокрутить к подсветке">⌖</button>
        </div>
      </div>
      <div ref={scroller} className="max-h-[60vh] overflow-auto rounded border border-stone-200 bg-stone-100">
        {failed ? (
          <div className="p-6 text-center text-sm text-stone-500">Картинка страницы недоступна.</div>
        ) : (
          <div className="relative" style={{ width: `${zoom * 100}%` }} data-testid="page-view">
            <img
              ref={img}
              src={pageImageUrl(documentId, page)}
              alt={`Страница PDF ${page}`}
              className="block w-full select-none"
              onLoad={() => setLoaded(true)}
              onError={() => setFailed(true)}
              draggable={false}
            />
            {half && (
              <>
                {/* затемняем другую половину разворота и обводим нужную */}
                <div className="pointer-events-none absolute inset-y-0 w-1/2 bg-stone-900/15" style={{ left: half === 'L' ? '50%' : 0 }} />
                <div className="pointer-events-none absolute inset-y-0 w-1/2 border-2 border-amber-500/70" style={{ left: half === 'L' ? 0 : '50%' }} data-testid="half-frame" />
              </>
            )}
            {bboxes.map((b, i) => {
              const [x0 = 0, y0 = 0, x1 = 0, y1 = 0] = b;
              return (
                <div
                  key={i}
                  data-bbox={b.join(',')}
                  className="pointer-events-none absolute rounded-sm border border-amber-600 bg-amber-300/40 mix-blend-multiply"
                  style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }}
                />
              );
            })}
          </div>
        )}
      </div>
      {loaded && bboxes.length === 0 && (
        <p className="mt-1 text-xs text-stone-500">
          Координат фрагмента нет{half ? ' — выделена половина разворота' : ''}; цитата показана в тексте ниже.
        </p>
      )}
    </div>
  );
}

export default function SourceView({ observation: o, chunk, document: doc }: {
  observation: ObservationListItem; chunk: Chunk | null; document: Document;
}) {
  const pdf = isPdfLocator(o.locator) ? o.locator : null;
  const page = pdf?.pdf_page ?? null;
  const hl = highlightBoxes(o, chunk);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <a className="btn" href={documentFileUrl(doc.id, doc.format === 'pdf' ? page : null)} target="_blank" rel="noreferrer">
          Открыть оригинал{page ? ` (стр. ${page})` : ''} ↗
        </a>
        {!isPdfLocator(o.locator) && (
          <span className="text-xs text-stone-500">
            {'sheet' in o.locator ? `лист «${o.locator.sheet}», строка ${o.locator.row}` :
              'table' in o.locator ? `таблица ${o.locator.table}, строка ${o.locator.row}` : `абзац ${o.locator.para}`}
          </span>
        )}
      </div>
      {doc.format === 'pdf' && page && (
        <>
          <PdfPageView documentId={doc.id} page={page} bboxes={hl.boxes} half={pdf?.half ?? null} />
          {hl.fromLines && <p className="mt-1 text-xs text-stone-500">Подсветка построена по строкам фрагмента (chunk.lines).</p>}
        </>
      )}
      <div>
        <div className="label">
          Текст фрагмента{chunk ? ` (${chunk.text_source === 'ocr' ? 'OCR' : chunk.text_source === 'manual' ? 'ручная расшифровка' : 'текстовый слой'})` : ''}
        </div>
        {chunk ? (
          <div data-scroll-box className="relative max-h-72 overflow-auto rounded border border-stone-200 bg-paper p-3">
            <QuoteText text={chunk.text} span={o.quote_span} />
          </div>
        ) : (
          <p className="text-sm text-stone-500">Фрагмент не привязан.</p>
        )}
        {o.quote && !o.quote_span && (
          <div className="mt-2 rounded border border-amber-200 bg-amber-50 p-2 text-sm">
            <div className="text-xs text-amber-800">Цитата не найдена в тексте дословно:</div>
            <q className="font-serif">{o.quote}</q>
          </div>
        )}
      </div>
    </div>
  );
}
