import type {
  Chunk, Document, ObservationEdit, Observation, Person, Phenophase, Place, PlaceName,
  Quality, SavedQuery, Section, Taxon, TaxonName,
} from '../api/types';
import documentsJson from '@contracts/fixtures/documents.json';
import sectionsJson from '@contracts/fixtures/sections.json';
import taxaJson from '@contracts/fixtures/taxa.json';
import taxonNamesJson from '@contracts/fixtures/taxon_names.json';
import placesJson from '@contracts/fixtures/places.json';
import placeNamesJson from '@contracts/fixtures/place_names.json';
import personsJson from '@contracts/fixtures/persons.json';
import phenophasesJson from '@contracts/fixtures/phenophases.json';
import savedQueriesJson from '@contracts/fixtures/saved_queries.json';
import qualityJson from '@contracts/fixtures/quality.json';
import observationsRaw from '@contracts/fixtures/observations.jsonl?raw';
import chunksRaw from '@contracts/fixtures/chunks.jsonl?raw';
import editsRaw from '@contracts/fixtures/observation_edits.jsonl?raw';

function jsonl<T>(raw: string): T[] {
  return raw.split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l) as T);
}
function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

export interface MockDb {
  documents: Document[];
  sections: Section[];
  chunks: Chunk[];
  observations: Observation[];
  edits: ObservationEdit[];
  taxa: Taxon[];
  taxonNames: TaxonName[];
  places: Place[];
  placeNames: PlaceName[];
  persons: Person[];
  phenophases: Phenophase[];
  savedQueries: SavedQuery[];
  quality: Quality;
}

/** Свежая копия фикстур: изменения (PATCH, сохранённые запросы) живут до перезагрузки страницы. */
export function createDb(): MockDb {
  return {
    documents: clone(documentsJson as unknown as Document[]),
    sections: clone(sectionsJson as unknown as Section[]),
    chunks: jsonl<Chunk>(chunksRaw),
    observations: jsonl<Observation>(observationsRaw),
    edits: jsonl<ObservationEdit>(editsRaw),
    taxa: clone(taxaJson as unknown as Taxon[]),
    taxonNames: clone(taxonNamesJson as unknown as TaxonName[]),
    places: clone(placesJson as unknown as Place[]),
    placeNames: clone(placeNamesJson as unknown as PlaceName[]),
    persons: clone(personsJson as unknown as Person[]),
    phenophases: clone(phenophasesJson as unknown as Phenophase[]),
    savedQueries: clone(savedQueriesJson as unknown as SavedQuery[]),
    quality: clone(qualityJson as unknown as Quality),
  };
}

export function nextId(rows: { id: number }[]): number {
  return rows.reduce((m, r) => Math.max(m, r.id), 0) + 1;
}
