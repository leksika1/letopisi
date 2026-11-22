/**
 * Мок-реализация семантики бэкенда поверх фикстур (contracts/fixtures).
 * Правила — из contracts/README.md «Решения» и openapi.yaml:
 *  - значения внутри массива фильтра — ИЛИ, разные фильтры — И;
 *  - фильтр по датам — пересечение интервалов;
 *  - rejected не возвращаются, если status не задан явно.
 */
import type {
  EditableFields, Observation, ObservationDetail, ObservationEdit, ObservationLabels, ObservationListItem,
  ObservationPatch, PhenologyRow, PlaceSpecies, QueryParseResponse, SearchFilters, SuggestItem, SuggestKind,
} from '../api/types';
import { FIELD_LABELS, STATUS_LABELS, formatObsDate } from '../lib/labels';
import type { MockDb } from './db';
import { nextId } from './db';

const lc = (s: string | null | undefined) => (s ?? '').toLocaleLowerCase('ru').replace(/ё/g, 'е');
const words = (s: string) => lc(s).split(/[^a-zа-я0-9-]+/i).filter(Boolean);
/** Грубая «основа» слова для мок-поиска по русской морфологии. */
const stem = (w: string) => (w.length <= 4 ? w.slice(0, 3) : w.slice(0, Math.max(4, w.length - 2)));

// ---------------------------------------------------------------- labels

export function labelsFor(db: MockDb, o: Observation): ObservationLabels {
  const taxon = o.taxon_id ? db.taxa.find((t) => t.id === o.taxon_id) : undefined;
  const place = o.place_id ? db.places.find((p) => p.id === o.place_id) : undefined;
  const ph = o.phenophase_id ? db.phenophases.find((p) => p.id === o.phenophase_id) : undefined;
  const doc = db.documents.find((d) => d.id === o.document_id);
  return {
    taxon_name_ru: taxon?.name_ru ?? null,
    taxon_name_lat: taxon?.name_lat ?? null,
    taxon_group: taxon?.group_ru ?? null,
    place_name: place?.name ?? null,
    phenophase_code: ph?.code ?? null,
    phenophase_name: ph?.name_ru ?? null,
    persons: o.person_ids.map((id) => {
      const p = db.persons.find((x) => x.id === id);
      return p ? [p.surname, p.initials].filter(Boolean).join(' ') : `#${id}`;
    }),
    document_title: doc?.title ?? `Документ ${o.document_id}`,
    book_no: doc?.book_no ?? null,
    book_year: doc?.year ?? null,
  };
}

export const withLabels = (db: MockDb, o: Observation): ObservationListItem => ({ ...o, labels: labelsFor(db, o) });

// ---------------------------------------------------------------- search

function taxonIdsByName(db: MockDb, name: string): Set<number> {
  const q = lc(name).trim();
  const ids = new Set<number>();
  for (const n of db.taxonNames) if (lc(n.name).includes(q)) ids.add(n.taxon_id);
  for (const t of db.taxa) if (lc(t.genus) === q) ids.add(t.id);
  return ids;
}

function matchesPerson(db: MockDb, o: Observation, surnames: string[]): boolean {
  const wanted = surnames.map(lc);
  const own = o.person_ids.map((id) => lc(db.persons.find((p) => p.id === id)?.surname));
  if (own.some((s) => wanted.includes(s))) return true;
  // наблюдатель не из каталога: ищем фамилию в observers_raw / pub_authors целым словом
  const raw = words(`${o.observers_raw ?? ''} ${o.pub_authors ?? ''}`);
  return wanted.some((w) => raw.includes(w));
}

function matchesText(o: Observation, q: string): boolean {
  const hay = lc([o.object_raw, o.quote, o.toponym_raw, o.event_raw].join(' '));
  return words(q).every((w) => hay.includes(stem(w)));
}

export function searchObservations(db: MockDb, f: SearchFilters): Observation[] {
  const taxonByName = f.taxon ? taxonIdsByName(db, f.taxon) : null;
  const docYear = new Map(db.documents.map((d) => [d.id, d.year]));
  const rows = db.observations.filter((o) => {
    if (f.status ? o.status !== f.status : o.status === 'rejected') return false;
    if (f.q && !matchesText(o, f.q)) return false;
    if (f.taxon_id && o.taxon_id !== f.taxon_id) return false;
    if (taxonByName && !(o.taxon_id && taxonByName.has(o.taxon_id))) return false;
    if (f.group && db.taxa.find((t) => t.id === o.taxon_id)?.group_ru !== f.group) return false;
    if (f.person?.length && !matchesPerson(db, o, f.person)) return false;
    if (f.place_id && o.place_id !== f.place_id) return false;
    if (f.place) {
      const q = lc(f.place);
      const ids = new Set(db.placeNames.filter((n) => lc(n.name).includes(q)).map((n) => n.place_id));
      if (!((o.place_id && ids.has(o.place_id)) || lc(o.toponym_raw).includes(q))) return false;
    }
    if (f.phenophase_id && o.phenophase_id !== f.phenophase_id) return false;
    if (f.indicator && !lc(o.indicator).includes(lc(f.indicator))) return false;
    if (f.record_type && o.record_type !== f.record_type) return false;
    if (f.document_id && o.document_id !== f.document_id) return false;
    if (f.date_from || f.date_to) {
      const from = o.date_from ?? o.date_to;
      const to = o.date_to ?? o.date_from;
      if (!from || !to) return false;
      if (f.date_from && to < f.date_from) return false;
      if (f.date_to && from > f.date_to) return false;
    }
    if (f.years?.length && !(o.obs_year && f.years.includes(o.obs_year))) return false;
    if (f.doc_years?.length) {
      const y = docYear.get(o.document_id);
      if (!(y && f.doc_years.includes(y))) return false;
    }
    if (f.flagged === true && o.flags.length === 0) return false;
    if (f.flagged === false && o.flags.length > 0) return false;
    return true;
  });
  const key = f.sort ?? 'id';
  const dir = f.order === 'desc' ? -1 : 1;
  const val = (o: Observation): string | number | null => {
    const v = (o as unknown as Record<string, unknown>)[key];
    if (key === 'date_from' && v == null) return o.obs_year ? `${o.obs_year}-99` : null;
    return typeof v === 'number' || typeof v === 'string' ? v : null;
  };
  return rows.sort((a, b) => {
    const va = val(a); const vb = val(b);
    if (va === vb) return a.id - b.id;
    if (va === null) return 1; // null — всегда в конце
    if (vb === null) return -1;
    return (va < vb ? -1 : 1) * dir;
  });
}

// ---------------------------------------------------------------- detail & patch

export function observationDetail(db: MockDb, id: number): ObservationDetail | null {
  const o = db.observations.find((x) => x.id === id);
  if (!o) return null;
  const document = db.documents.find((d) => d.id === o.document_id);
  if (!document) return null;
  return {
    observation: withLabels(db, o),
    chunk: db.chunks.find((c) => c.id === o.chunk_id) ?? null,
    section: db.sections.find((s) => s.id === o.section_id) ?? null,
    document,
    edits: db.edits.filter((e) => e.observation_id === id).sort((a, b) => a.created_at.localeCompare(b.created_at)),
  };
}

const STATUS_BY_ACTION = { edit: 'edited', verify: 'verified', reject: 'rejected' } as const;

export function patchObservation(db: MockDb, id: number, patch: ObservationPatch): ObservationDetail | null {
  const o = db.observations.find((x) => x.id === id);
  if (!o) return null;
  const changes: Record<string, { old: unknown; new: unknown }> = {};
  const rec = o as unknown as Record<string, unknown>;
  for (const [k, v] of Object.entries(patch.fields ?? {}) as [keyof EditableFields, unknown][]) {
    if (!(k in FIELD_LABELS)) continue;
    if (JSON.stringify(rec[k]) !== JSON.stringify(v)) {
      changes[k] = { old: rec[k], new: v };
      rec[k] = v;
    }
  }
  const before = o.status;
  o.status = STATUS_BY_ACTION[patch.action];
  o.updated_at = new Date().toISOString();
  const edit: ObservationEdit = {
    id: nextId(db.edits),
    observation_id: id,
    action: patch.action,
    editor: patch.editor,
    changes,
    status_before: before,
    status_after: o.status,
    comment: patch.comment ?? null,
    created_at: o.updated_at,
  };
  db.edits.push(edit);
  return observationDetail(db, id);
}

// ---------------------------------------------------------------- suggest

export function suggest(db: MockDb, kind: SuggestKind, q: string, limit = 10): SuggestItem[] {
  const s = lc(q).trim();
  const score = (name: string) => (lc(name).startsWith(s) ? 0 : lc(name).includes(s) ? 1 : 2);
  const top = <T,>(items: T[], names: (x: T) => string[], toItem: (x: T) => SuggestItem) =>
    items
      .map((x) => ({ x, sc: Math.min(...names(x).map(score)) }))
      .filter((r) => r.sc < 2)
      .sort((a, b) => a.sc - b.sc)
      .slice(0, limit)
      .map((r) => toItem(r.x));
  switch (kind) {
    case 'taxa':
      return top(
        db.taxa,
        (t) => [...db.taxonNames.filter((n) => n.taxon_id === t.id).map((n) => n.name), t.genus ?? ''],
        (t) => ({ id: t.id, label: t.name_ru ?? t.name_lat, sublabel: t.name_lat }),
      );
    case 'places':
      return top(
        db.places,
        (p) => [p.name, ...db.placeNames.filter((n) => n.place_id === p.id).map((n) => n.name)],
        (p) => ({ id: p.id, label: p.name, sublabel: p.category }),
      );
    case 'persons':
      return top(db.persons, (p) => [p.surname], (p) => ({
        id: p.id,
        label: [p.surname, p.initials].filter(Boolean).join(' '),
        sublabel: p.years_active.length ? `годы работы: ${p.years_active.join(', ')}` : null,
      }));
    case 'phenophases':
      return top(db.phenophases, (p) => [p.code, p.name_ru ?? ''], (p) => ({
        id: p.id,
        label: `${p.code} — ${p.name_ru ?? 'название не найдено'}`,
        sublabel: p.applies_to,
      }));
  }
}

// ---------------------------------------------------------------- analytics

export function phenology(db: MockDb, taxonId: number, phenophaseId?: number, years?: number[]): PhenologyRow[] {
  const ord = new Map(db.phenophases.map((p) => [p.id, p.ord]));
  return db.observations
    .filter((o) => o.taxon_id === taxonId && o.phenophase_id && o.status !== 'rejected' && o.obs_year)
    .filter((o) => !phenophaseId || o.phenophase_id === phenophaseId)
    .filter((o) => !years?.length || years.includes(o.obs_year as number))
    .map((o) => {
      const ph = db.phenophases.find((p) => p.id === o.phenophase_id);
      return {
        taxon_id: taxonId,
        phenophase_id: o.phenophase_id,
        phenophase_code: ph?.code ?? null,
        phenophase_name: ph?.name_ru ?? null,
        year: o.obs_year as number,
        date_from: o.date_from,
        date_to: o.date_to,
        observation_id: o.id,
      };
    })
    .sort((a, b) => (ord.get(a.phenophase_id ?? 0) ?? 0) - (ord.get(b.phenophase_id ?? 0) ?? 0) || a.year - b.year);
}

export function placeSpecies(db: MockDb, placeId: number, year?: number, group?: string): PlaceSpecies | null {
  const place = db.places.find((p) => p.id === placeId);
  if (!place) return null;
  const by = new Map<number, Observation[]>();
  for (const o of db.observations) {
    if (o.place_id !== placeId || !o.taxon_id || o.status === 'rejected') continue;
    if (year && o.obs_year !== year) continue;
    by.set(o.taxon_id, [...(by.get(o.taxon_id) ?? []), o]);
  }
  const items = [...by.entries()]
    .map(([taxonId, obs]) => {
      const t = db.taxa.find((x) => x.id === taxonId);
      const dates = obs.map((o) => o.date_from).filter((d): d is string => !!d).sort();
      return t && {
        taxon_id: taxonId, name_ru: t.name_ru, name_lat: t.name_lat, group_ru: t.group_ru,
        n_records: obs.length, first_date: dates[0] ?? null, last_date: dates[dates.length - 1] ?? null,
      };
    })
    .filter((x): x is NonNullable<typeof x> => !!x && (!group || x.group_ru === group))
    .sort((a, b) => b.n_records - a.n_records);
  return { place, items };
}

// ---------------------------------------------------------------- query parse

const PHENO_RULES: [RegExp, string, string][] = [
  [/зацвет|начал\w* цветени/, '5', 'фенофаза: 5 — начало цветения'],
  [/массов\w* цветени/, '6', 'фенофаза: 6 — массовое цветение'],
  [/листопад/, '14', 'фенофаза: 14 — начало листопада'],
  [/прил[её]т/, 'arrival', 'фенофаза: прилёт'],
  [/песн|кукован/, 'first_song', 'фенофаза: первая песня'],
  [/молодняк|выводк|новорожд/, 'young', 'фенофаза: появление молодняка'],
];

/** Мок разбора запроса обычными словами (настоящий парсер — на бэкенде). */
export function parseQuery(db: MockDb, text: string): QueryParseResponse {
  const t = lc(text);
  const tw = words(text);
  const filters: SearchFilters = {};
  const expl: string[] = [];
  const warnings: string[] = [];

  // вид: самое длинное совпадение по всем словам названия (с грубой морфологией)
  let best: { id: number; len: number; name: string } | null = null;
  for (const n of db.taxonNames) {
    const nw = words(n.name);
    if (!nw.length || !nw.every((w) => tw.some((x) => x.startsWith(stem(w))))) continue;
    if (!best || n.name.length > best.len) best = { id: n.taxon_id, len: n.name.length, name: n.name };
  }
  if (best) {
    const taxon = db.taxa.find((x) => x.id === best.id);
    filters.taxon_id = best.id;
    expl.push(`вид: ${taxon?.name_ru ?? best.name} (${taxon?.name_lat ?? ''})`);
  }

  // люди: точная фамилия, иначе по основе
  const persons = new Set<string>();
  for (const w of tw) {
    const exact = db.persons.filter((p) => lc(p.surname) === w);
    const found = exact.length ? exact : w.length > 5 ? db.persons.filter((p) => w.startsWith(stem(lc(p.surname)))) : [];
    found.forEach((p) => persons.add(p.surname));
  }
  if (persons.size) {
    filters.person = [...persons];
    expl.push(`наблюдатели: ${filters.person.join(', ')}`);
  }

  // годы: «в 1995» — годы наблюдения, «за 1995» — годы книг
  const years = new Set<number>();
  const docYears = new Set<number>();
  const re = /(\d{4})(?:\s*[-–]\s*(\d{4}))?/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(t))) {
    const a = Number(m[1]); const b = m[2] ? Number(m[2]) : a;
    if (a < 1800 || a > 2100) continue;
    const before = t.slice(0, m.index).replace(/[\d\s,и–-]+$/u, '');
    const prep = /(\S+)\s*$/.exec(before)?.[1] ?? '';
    const target = prep === 'за' ? docYears : years;
    for (let y = Math.min(a, b); y <= Math.max(a, b); y++) {
      if (y === 2022) {
        target.add(2020);
        if (!warnings.length) warnings.push('2022 → 2020: книги за 2022 год нет, использован том 2020 года');
      } else target.add(y);
    }
  }
  if (years.size) {
    filters.years = [...years].sort();
    expl.push(`годы наблюдения: ${filters.years.join(', ')}`);
  }
  if (docYears.size) {
    filters.doc_years = [...docYears].sort();
    expl.push(`годы книг: ${filters.doc_years.join(', ')}`);
  }

  for (const [rx, code, label] of PHENO_RULES) {
    if (!rx.test(t)) continue;
    const ph = db.phenophases.find((p) => p.code === code);
    if (ph) {
      filters.phenophase_id = ph.id;
      expl.push(label);
    }
    break;
  }

  if (/публикац|стат(ья|ей|ьи)|библиограф/.test(t)) {
    filters.record_type = 'publication';
    expl.push('тип: публикации');
  } else if (/наблюдени/.test(t)) {
    filters.record_type = 'observation';
    expl.push('тип: наблюдения');
  }

  // место (кроме «Пасвик» — это весь заповедник)
  for (const pn of db.placeNames) {
    const nw = words(pn.name);
    const place = db.places.find((p) => p.id === pn.place_id);
    if (!place || lc(place.name) === 'пасвик' || !nw.length) continue;
    if (nw.every((w) => w.length > 2 && tw.some((x) => x.startsWith(stem(w))))) {
      filters.place_id = place.id;
      expl.push(`место: ${place.name}`);
      break;
    }
  }
  if (/пасвик/.test(t)) warnings.push('«Пасвик» понят как весь заповедник — фильтр по месту не ставится');

  if (/хронолог|по дат/.test(t) || filters.phenophase_id || filters.person) {
    filters.sort = 'date_from';
    filters.order = 'asc';
    expl.push('сортировка по дате');
  } else if (filters.record_type === 'publication') {
    filters.sort = 'obs_year';
    filters.order = 'asc';
    expl.push('сортировка по году');
  }

  if (!expl.length) {
    filters.q = text.trim();
    expl.push(`полнотекстовый поиск: «${text.trim()}»`);
    warnings.push('Не удалось распознать вид, людей или годы — ищем по тексту');
  }
  return { filters, explanation: expl.join('; '), warnings };
}

// ---------------------------------------------------------------- export (contracts/export_columns.json)

export const EXPORT_HEADERS = [
  'Дата наблюдения', 'Год наблюдения', 'ФИО наблюдателя', 'Наименование вида (русский)',
  'Наименование вида (латынь)', 'Место регистрации (топоним)', 'Координаты встречи', 'Местообитание',
  'Характер явления, наблюдения', 'Численные показатели', 'Фенологическая фаза',
  'Ссылка на страницу/-цы в документе', 'Примечание', 'Источник (ссылка)', 'Достоверность',
];

export function exportRow(db: MockDb, o: Observation, baseUrl: string): (string | number)[] {
  const l = labelsFor(db, o);
  const place = db.places.find((p) => p.id === o.place_id);
  const ph = db.phenophases.find((p) => p.id === o.phenophase_id);
  const pdfPage = 'pdf_page' in o.locator ? o.locator.pdf_page : null;
  const pub = [o.pub_authors, o.pub_title, o.pub_venue, o.pub_year].filter((x) => x != null && x !== '').join(' ');
  return [
    formatObsDate(o),
    o.obs_year ?? '',
    o.observers_raw ?? l.persons.join(', '),
    o.taxon_id ? (l.taxon_name_ru ?? '') : o.record_type === 'publication' ? (o.pub_title ?? o.object_raw) : o.object_raw,
    l.taxon_name_lat ?? '',
    o.toponym_raw ?? l.place_name ?? '',
    o.coords_raw ?? (place?.lat != null && place.lon != null ? `${place.lat}, ${place.lon} (приблизительно, ${place.coords_source})` : 'нет'),
    o.habitat_raw ?? '',
    o.record_type === 'publication' ? `публикация: ${pub}` : (o.event_raw ?? ''),
    o.value_num ?? o.value_raw ?? 'нет',
    ph ? (ph.applies_to === 'plants' ? `фаза ${ph.code} - ${ph.name_ru ?? ''}` : (ph.name_ru ?? ph.code)) : (o.phenophase_raw ?? 'не фиксировалась'),
    `${o.printed_pages ?? `PDF стр. ${pdfPage ?? '?'}`}, том ${l.book_no ?? '?'}`,
    o.note ?? '',
    `${baseUrl}/observations/${o.id}${pdfPage ? `?page=${pdfPage}` : ''}`,
    STATUS_LABELS[o.status] + (o.flags.length ? `; флаги: ${o.flags.join(', ')}` : ''),
  ];
}

export function toCsv(rows: (string | number)[][]): string {
  const cell = (v: string | number) => {
    const s = String(v);
    return /[;"\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return '﻿' + rows.map((r) => r.map(cell).join(';')).join('\r\n') + '\r\n';
}
