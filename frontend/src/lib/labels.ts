import type { Flag, RecordType, Status, TaxonGroup } from '../api/types';

export const TAXON_GROUPS: TaxonGroup[] = [
  'сосудистые растения', 'мхи', 'лишайники', 'грибы', 'птицы', 'млекопитающие', 'рыбы',
  'амфибии и рептилии', 'насекомые', 'прочие беспозвоночные',
];

export const RECORD_TYPES: Record<RecordType, string> = {
  observation: 'наблюдение',
  env_measurement: 'измерение среды',
  publication: 'публикация',
};

export const STATUS_LABELS: Record<Status, string> = {
  auto: 'авто',
  verified: 'подтверждено',
  edited: 'исправлено',
  rejected: 'отклонено',
};

export const FLAG_LABELS: Record<Flag, string> = {
  quote_not_found: 'Цитата не найдена в исходном тексте дословно',
  value_not_in_quote: 'Числовое значение отсутствует в цитате',
  date_not_in_quote: 'Дата отсутствует в цитате',
  date_ambiguous: 'Дата неоднозначна',
  taxon_unmatched: 'Вид не найден в справочнике',
  taxon_ambiguous: 'Вид определён неоднозначно',
  place_unmatched: 'Место не найдено в справочнике',
  person_unmatched: 'Наблюдатель не найден в каталоге сотрудников',
  year_outside_book: 'Год наблюдения не совпадает с годом книги',
  low_ocr: 'Низкое качество распознавания (OCR)',
  table_parse_uncertain: 'Таблица разобрана неуверенно',
};

/** Флаги, которые носят информативный характер и не требуют проверки. */
export const INFO_FLAGS: Flag[] = ['year_outside_book'];

export const FIELD_LABELS: Record<string, string> = {
  record_type: 'Тип записи',
  object_type: 'Тип объекта',
  object_raw: 'Объект (как в тексте)',
  taxon_id: 'Вид',
  date_raw: 'Дата (как в тексте)',
  date_from: 'Дата с',
  date_to: 'Дата по',
  date_precision: 'Точность даты',
  obs_year: 'Год наблюдения',
  toponym_raw: 'Топоним (как в тексте)',
  place_id: 'Место',
  coords_raw: 'Координаты',
  habitat_raw: 'Местообитание',
  event_raw: 'Характер явления',
  phenophase_id: 'Фенофаза',
  phenophase_raw: 'Фенофаза (как в тексте)',
  indicator: 'Показатель',
  value_num: 'Значение',
  value_raw: 'Значение (как в тексте)',
  unit: 'Единица',
  observers_raw: 'Наблюдатели (как в тексте)',
  person_ids: 'Наблюдатели (каталог)',
  pub_authors: 'Авторы публикации',
  pub_year: 'Год публикации',
  pub_title: 'Название публикации',
  pub_venue: 'Издание',
  printed_pages: 'Печатные страницы',
  note: 'Примечание',
  flags: 'Флаги',
  confidence: 'Достоверность',
  status: 'Статус',
};

export const DATE_PRECISION: Record<string, string> = {
  day: 'день', decade: 'декада', month: 'месяц', season: 'сезон', year: 'год', range: 'интервал',
};

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y}`;
}

export function formatObsDate(o: {
  date_from: string | null; date_to: string | null; date_precision: string | null; date_raw: string | null; obs_year: number | null;
}): string {
  if (o.date_precision === 'day' && o.date_from) return formatDate(o.date_from);
  if (o.date_precision === 'range' && o.date_from && o.date_to) return `${formatDate(o.date_from)}–${formatDate(o.date_to)}`;
  if (o.date_precision === 'year' && o.obs_year) return String(o.obs_year);
  return o.date_raw ?? '';
}
