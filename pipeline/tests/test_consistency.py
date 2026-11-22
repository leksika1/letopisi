from pipeline.extract.consistency import enforce


def test_citation_fields_moved_to_note_for_observation():
    o = enforce({"record_type": "observation", "object_type": "taxon", "pub_authors": "Крутов и др.",
                 "pub_year": 2012, "pub_title": None, "pub_venue": None, "note": None})
    assert o["pub_authors"] is None and o["pub_year"] is None
    assert "Крутов и др., 2012" in o["note"]


def test_publication_keeps_fields_and_object_type():
    o = enforce({"record_type": "publication", "object_type": "taxon", "pub_authors": "Иванов",
                 "pub_year": 2020, "pub_title": "Т", "pub_venue": None})
    assert o["object_type"] == "publication" and o["pub_authors"] == "Иванов"


def test_inverted_dates_reset():
    o = enforce({"record_type": "observation", "date_from": "2020-06-10", "date_to": "2020-06-01",
                 "date_precision": "range", "flags": []})
    assert o["date_from"] is None and "date_ambiguous" in o["flags"]
