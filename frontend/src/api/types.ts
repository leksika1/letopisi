import type { components, operations } from './schema';

type S = components['schemas'];

export type Id = S['Id'];
export type Observation = S['Observation'];
export type ObservationListItem = S['ObservationListItem'];
export type ObservationLabels = S['ObservationLabels'];
export type ObservationPage = S['ObservationPage'];
export type ObservationDetail = S['ObservationDetail'];
export type ObservationPatch = S['ObservationPatch'];
export type ObservationEdit = S['ObservationEdit'];
export type EditableFields = S['ObservationEditableFields'];
export type EditableFieldName = S['EditableFieldName'];
export type Document = S['Document'];
export type Section = S['Section'];
export type Chunk = S['Chunk'];
export type Locator = S['Locator'];
export type PdfLocator = S['PdfLocator'];
export type Taxon = S['Taxon'];
export type TaxonName = S['TaxonName'];
export type Place = S['Place'];
export type PlaceName = S['PlaceName'];
export type Person = S['Person'];
export type Phenophase = S['Phenophase'];
export type SearchFilters = S['SearchFilters'];
export type SavedQuery = S['SavedQuery'];
export type SavedQueryCreate = S['SavedQueryCreate'];
export type QueryParseResponse = S['QueryParseResponse'];
export type PhenologyRow = S['PhenologyRow'];
export type PlaceSpecies = S['PlaceSpecies'];
export type SuggestItem = S['SuggestItem'];
export type Quality = S['Quality'];
export type FieldMetric = S['FieldMetric'];
export type Flag = S['Flag'];
export type Status = S['Status'];
export type RecordType = S['RecordType'];
export type TaxonGroup = S['TaxonGroup'];
export type SortField = S['SortField'];
export type ApiError = S['Error'];
export type Health = S['Health'];

export type SuggestKind = operations['suggest']['parameters']['path']['kind'];

export function isPdfLocator(l: Locator): l is PdfLocator {
  return 'pdf_page' in l;
}
