import { describe, expect, it } from 'vitest';
import { clean, filtersFromParams, parseYears, writeFilters } from '../lib/filters';
import { createDb } from './db';
import { EXPORT_HEADERS, exportRow, observationDetail, parseQuery, patchObservation, phenology, searchObservations, suggest, toCsv } from './engine';
import exportColumns from '@contracts/export_columns.json';

describe('приёмочные запросы (saved_queries.json) на фикстурах', () => {
  const db = createDb();
  const ids = (n: number) => searchObservations(db, db.savedQueries[n]!.filters).map((o) => o.id);

  it('1: Макарова и Сметанников, 1995–1996 — ЗМУ 1995', () => {
    expect(ids(0)).toEqual(expect.arrayContaining([34, 35, 36, 37, 38]));
    expect(ids(0).length).toBeGreaterThan(0);
  });
  it('2: лось 1995, 1996, 2020 по хронологии', () => {
    const rows = searchObservations(db, db.savedQueries[1]!.filters);
    expect(rows.length).toBeGreaterThanOrEqual(9);
    expect(rows.every((o) => o.taxon_id === 16 && o.record_type === 'observation')).toBe(true);
    const dates = rows.map((o) => o.date_from).filter(Boolean) as string[];
    expect([...dates].sort()).toEqual(dates);
  });
  it('3: публикации о буром медведе', () => {
    expect(ids(2)).toEqual([31]);
  });
  it('4: зацветание герани лесной', () => {
    expect(ids(3)).toEqual(expect.arrayContaining([1, 2, 3, 5, 6]));
  });
});

describe('семантика фильтров', () => {
  const db = createDb();
  it('rejected не возвращаются без явного status', () => {
    db.observations[0]!.status = 'rejected';
    expect(searchObservations(db, {}).some((o) => o.id === 1)).toBe(false);
    expect(searchObservations(db, { status: 'rejected' }).map((o) => o.id)).toEqual([1]);
  });
  it('даты — пересечение интервалов', () => {
    const r = searchObservations(db, { date_from: '1995-03-31', date_to: '1995-03-31' }).map((o) => o.id);
    expect(r).toEqual(expect.arrayContaining([34, 38]));
  });
  it('taxon строкой: синоним и род', () => {
    expect(searchObservations(db, { taxon: 'Alces' }).every((o) => o.taxon_id === 16)).toBe(true);
    expect(searchObservations(db, { taxon: 'Geranium' }).length).toBeGreaterThan(0);
  });
  it('flagged', () => {
    expect(searchObservations(db, { flagged: true }).every((o) => o.flags.length > 0)).toBe(true);
  });
});

describe('разбор запроса обычными словами', () => {
  const db = createDb();
  const texts = db.savedQueries.map((q) => q.text as string);
  it('лось: years + 2022→2020 + хронология', () => {
    const r = parseQuery(db, texts[1]!);
    expect(r.filters).toMatchObject({ taxon_id: 16, years: [1995, 1996, 2020], record_type: 'observation', sort: 'date_from' });
    expect(r.warnings.join()).toContain('2022 → 2020');
  });
  it('медведь: публикации, doc_years', () => {
    expect(parseQuery(db, texts[2]!).filters).toMatchObject({ taxon_id: 17, record_type: 'publication', doc_years: [1995, 1996, 2020] });
  });
  it('герань: фенофаза 5, без фильтра по месту «Пасвик»', () => {
    const f = parseQuery(db, texts[3]!).filters;
    expect(f).toMatchObject({ taxon_id: 13, phenophase_id: 5, doc_years: [1995, 1996, 2020] });
    expect(f.place_id).toBeUndefined();
  });
  it('люди: Макарова и Сметанников (не Сметанникова)', () => {
    const f = parseQuery(db, texts[0]!).filters;
    expect(f.person).toEqual(['Макарова', 'Сметанников']);
    expect(f.years).toEqual([1995, 1996]);
    expect(searchObservations(db, f).length).toBeGreaterThan(0);
  });
});

describe('PATCH пишет observation_edits', () => {
  it('edit → edited, changes {old,new}', () => {
    const db = createDb();
    const before = db.edits.length;
    const oldNote = db.observations.find((o) => o.id === 4)!.note;
    const d = patchObservation(db, 4, { action: 'edit', editor: 'Тест', fields: { value_num: 3, note: 'x' }, comment: 'c' });
    expect(d?.observation.status).toBe('edited');
    expect(db.edits.length).toBe(before + 1);
    expect(d?.edits.at(-1)?.changes).toEqual({ value_num: { old: null, new: 3 }, note: { old: oldNote, new: 'x' } });
    const v = patchObservation(db, 4, { action: 'verify', editor: 'Тест' });
    expect(v?.observation.status).toBe('verified');
    expect(v?.edits.at(-1)).toMatchObject({ status_before: 'edited', status_after: 'verified', changes: {} });
  });
});

describe('карточка, фенология, подсказки, выгрузка', () => {
  const db = createDb();
  it('detail: quote совпадает с chunk.text[quote_span]', () => {
    for (const o of db.observations) {
      const d = observationDetail(db, o.id)!;
      if (d.chunk && o.quote_span) {
        expect(d.chunk.text.slice(o.quote_span[0], o.quote_span[1])).toBe(o.quote);
      }
    }
  });
  it('фенология герани: 2018–2020', () => {
    expect(new Set(phenology(db, 13).map((r) => r.year))).toEqual(new Set([2018, 2019, 2020]));
  });
  it('suggest по русскому, латыни и роду', () => {
    expect(suggest(db, 'taxa', 'лось')[0]?.id).toBe(16);
    expect(suggest(db, 'taxa', 'Alces')[0]?.id).toBe(16);
    expect(suggest(db, 'persons', 'Сметан').map((x) => x.label)).toContain('Сметанников');
  });
  it('колонки выгрузки — ровно как в export_columns.json', () => {
    expect(EXPORT_HEADERS).toEqual(exportColumns.map((c) => c.header));
    const row = exportRow(db, db.observations.find((o) => o.id === 34)!, 'http://x');
    expect(row[0]).toBe('30.03.1995–31.03.1995');
    expect(row[11]).toBe('76-77, том 2');
    expect(row[13]).toBe('http://x/observations/34?page=40');
    expect(toCsv([EXPORT_HEADERS]).startsWith('﻿Дата наблюдения;')).toBe(true);
  });
});

describe('фильтры ↔ URL', () => {
  it('туда и обратно без потерь', () => {
    const f = clean({ taxon_id: 16, person: ['Макарова', 'Сметанников'], years: [1995, 2020], flagged: true, sort: 'date_from', order: 'asc' });
    const sp = writeFilters(new URLSearchParams('tab=author&page=3'), f);
    expect(sp.get('years')).toBe('1995,2020');
    expect(sp.get('tab')).toBe('author');
    expect(filtersFromParams(sp)).toEqual(f);
  });
  it('мусор в URL отбрасывается', () => {
    expect(filtersFromParams(new URLSearchParams('taxon_id=abc&group=xxx&years=19x5&date_from=2020-1-1'))).toEqual({});
  });
  it('parseYears: списки и диапазоны', () => {
    expect(parseYears('1995, 1996')).toEqual([1995, 1996]);
    expect(parseYears('2018–2020')).toEqual([2018, 2019, 2020]);
  });
});

