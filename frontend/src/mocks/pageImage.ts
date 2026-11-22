import type { Document } from '../api/types';

/**
 * Заглушка картинки страницы: в режиме моков исходных PDF нет (data/ не в git).
 * Пропорции как у настоящих страниц: разворот — альбомный, одиночная страница — A4.
 * Подсветка bboxes рисуется клиентом в долях 0..1, поэтому совпадает с позициями из фикстур.
 */
export function pageSvg(doc: Document, page: number): string {
  const spread = doc.layout === 'spread';
  const w = spread ? 1190 : 595;
  const h = 842;
  const cols: [number, number][] = spread ? [[60, w / 2 - 40], [w / 2 + 40, w - 60]] : [[60, w - 60]];
  const lines: string[] = [];
  for (const [x0, x1] of cols) {
    for (let y = 90; y < h - 80; y += 22) {
      const len = x1 - x0 - ((y * 7) % 90);
      lines.push(`<rect x="${x0}" y="${y}" width="${len}" height="7" rx="3" fill="#e7e2d6"/>`);
    }
  }
  const title = doc.title.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const divider = spread
    ? `<line x1="${w / 2}" y1="0" x2="${w / 2}" y2="${h}" stroke="#d6cfbf" stroke-width="2" stroke-dasharray="6 6"/>`
    : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">`
    + `<rect width="100%" height="100%" fill="#fdfcf7"/>${divider}${lines.join('')}`
    + `<text x="${w / 2}" y="44" text-anchor="middle" font-family="Georgia, serif" font-size="16" fill="#8a7f68">`
    + `Мок: PDF стр. ${page}${spread ? ' (разворот)' : ''} — оригинал недоступен без data/raw</text>`
    + `<text x="${w / 2}" y="${h - 30}" text-anchor="middle" font-family="Georgia, serif" font-size="12" fill="#a59b85">${title}</text>`
    + '</svg>';
}
