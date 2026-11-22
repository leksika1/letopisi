"""Генератор фикстур contracts/fixtures из реальных файлов data/raw.

Запуск из корня репозитория: python contracts/gen_fixtures.py
Нужны data/raw/*.pdf, parameters_2020.xlsx, PyMuPDF (fitz) и openpyxl.
Цитаты, quote_span и bboxes для книги 2020 берутся из текстового слоя PDF (посимвольные координаты);
фрагменты книги 1995 — ручная расшифровка скана (text_source=manual).
После генерации запустите python contracts/validate.py.
"""
import json, sys, re, hashlib
from pathlib import Path
import fitz, openpyxl

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "raw"
OUT = REPO / "contracts" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)
TS = "2026-11-22T12:00:00Z"
EXV = "fixture-1.0"

def dump(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

def dump_jsonl(name, rows):
    with open(OUT / name, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

# ---------------- documents ----------------
d27 = fitz.open(RAW / "book27_2020.pdf")
d02 = fitz.open(RAW / "book02_1995.pdf")
d03 = fitz.open(RAW / "book03_1996.pdf")
def text_pages(doc):
    return sum(1 for p in doc if p.get_text().strip())
documents = [
    {"id": 1, "title": "Летопись природы заповедника «Пасвик». Книга 2 (1995 год)", "book_no": 2, "year": 1995,
     "filename": "book02_1995.pdf", "format": "pdf", "pages": d02.page_count, "has_text_layer": text_pages(d02) > 0,
     "layout": "spread", "sha256": sha(RAW / "book02_1995.pdf"), "status": "done", "error": None, "created_at": TS},
    {"id": 2, "title": "Летопись природы заповедника «Пасвик». Книга 3 (1996 год)", "book_no": 3, "year": 1996,
     "filename": "book03_1996.pdf", "format": "pdf", "pages": d03.page_count, "has_text_layer": text_pages(d03) > 0,
     "layout": "spread", "sha256": sha(RAW / "book03_1996.pdf"), "status": "queued", "error": None, "created_at": TS},
    {"id": 3, "title": "Летопись природы заповедника «Пасвик». Книга 27 (2020 год)", "book_no": 27, "year": 2020,
     "filename": "book27_2020.pdf", "format": "pdf", "pages": d27.page_count, "has_text_layer": True,
     "layout": "single", "sha256": sha(RAW / "book27_2020.pdf"), "status": "done", "error": None, "created_at": TS},
]
dump("documents.json", documents)

# ---------------- sections ----------------
sections = [
    {"id": 1, "document_id": 1, "parent_id": None, "code": None, "title": "Млекопитающие. Результаты зимнего маршрутного учета зверей и птиц (март 1995 года)", "pdf_page_from": 40, "pdf_page_to": 40},
    {"id": 10, "document_id": 3, "parent_id": None, "code": "4", "title": "Погода", "pdf_page_from": 11, "pdf_page_to": 40},
    {"id": 11, "document_id": 3, "parent_id": 10, "code": "4.2", "title": "Даты начала сезонов, продолжительность, температура воздуха", "pdf_page_from": 12, "pdf_page_to": 35},
    {"id": 12, "document_id": 3, "parent_id": None, "code": "6", "title": "Календарь природы", "pdf_page_from": 46, "pdf_page_to": 49},
    {"id": 13, "document_id": 3, "parent_id": None, "code": "7", "title": "Флора и растительность", "pdf_page_from": 50, "pdf_page_to": 94},
    {"id": 14, "document_id": 3, "parent_id": 13, "code": "7.3", "title": "Сезонная динамика растительных сообществ", "pdf_page_from": 69, "pdf_page_to": 78},
    {"id": 15, "document_id": 3, "parent_id": None, "code": "8", "title": "Фауна и животный мир", "pdf_page_from": 95, "pdf_page_to": 520},
    {"id": 16, "document_id": 3, "parent_id": 15, "code": "8.4", "title": "Птицы", "pdf_page_from": 143, "pdf_page_to": 448},
    {"id": 17, "document_id": 3, "parent_id": 15, "code": "8.5", "title": "Млекопитающие", "pdf_page_from": 449, "pdf_page_to": 520},
    {"id": 18, "document_id": 3, "parent_id": None, "code": "10", "title": "Научные исследования", "pdf_page_from": 523, "pdf_page_to": 550},
    {"id": 19, "document_id": 3, "parent_id": None, "code": None, "title": "Библиографический список", "pdf_page_from": 600, "pdf_page_to": 610},
]
dump("sections.json", sections)

def section_for(doc_id, page):
    best = None
    for s in sections:
        if s["document_id"] == doc_id and s["pdf_page_from"] <= page <= s["pdf_page_to"]:
            if best is None or (s["pdf_page_to"] - s["pdf_page_from"]) < (best["pdf_page_to"] - best["pdf_page_from"]):
                best = s
    return best["id"] if best else None

# ---------------- xlsx ----------------
wb = openpyxl.load_workbook(RAW / "parameters_2020.xlsx", data_only=True)

# places
places, place_names = [], []
approx = {"Никель": (69.41, 30.22), "Заполярный": (69.42, 30.80), "Раякоски": (69.03, 29.02)}
ws = wb["каталог_топонимы"]
for r in ws.iter_rows(min_row=2, values_only=True):
    if not r[0] or str(r[0]).startswith("Название топонима"):
        continue
    pid = len(places) + 1
    name = str(r[0]).strip()
    lat, lon = approx.get(name, (None, None))
    places.append({"id": pid, "name": name, "category": (str(r[1]).strip() if r[1] else None),
                   "lat": lat, "lon": lon, "coords_source": "manual_approx" if lat is not None else None})
assert len(places) == 37, len(places)
P = {p["name"]: p["id"] for p in places}
variants = {
    "Паз": ["р. Паз", "реки Паз", "река Паз"],
    "Пасвик": ["заповедник «Пасвик»", "заповедник \"Пасвик\""],
    "Варлама": ["о. Варлама", "о-в Варлама", "о-ве Варлама", "остров Варлама"],
    "Калкупя": ["г. Калкупя", "гора Калкупя", "горы Калкупя", "Калкупи"],
    "Каскама": ["г. Каскама", "гора Каскама"],
    "Каскамаярви": ["оз. Каскамаярви", "озеро Каскамаярви", "озера Каскамаярви"],
    "Мениккайоки": ["р. Мениккайоки", "река Мениккайоки"],
    "Наутсийоки": ["р. Наутсийоки"],
    "Латвала": ["урочище Латвала", "ур. Латвала"],
    "Мениккасаари": ["болото Мениккасаари"],
    "Кайтоапа": ["болото Кайтоапа"],
    "Сальмиярви": ["оз. Сальмиярви", "застава Сальмиярви"],
    "Лауккуйоки": ["р. Лауккуйоки"],
    "Янискоски": ["пос. Янискоски"],
    "Раякоски": ["пос. Раякоски"],
    "Никель": ["пгт. Никель", "пос. Никель"],
    "Заполярный": ["г. Заполярный"],
}
for p in places:
    place_names.append({"id": len(place_names) + 1, "place_id": p["id"], "name": p["name"], "kind": "accepted"})
    for v in variants.get(p["name"], []):
        place_names.append({"id": len(place_names) + 1, "place_id": p["id"], "name": v, "kind": "variant"})
dump("places.json", places)
dump("place_names.json", place_names)

# persons
ws = wb["каталог_сотрудники"]
persons = {}
for r in ws.iter_rows(min_row=2, values_only=True):
    if not r[0] or str(r[0]).startswith("ФИО сотрудника"):
        continue
    sn = str(r[0]).strip()
    p = persons.setdefault(sn, {"surname": sn, "initials": None, "full_raw": sn, "years_active": [],
                                "year_start": None, "year_end": None, "still_working": None})
    if isinstance(r[1], int) and r[1] not in p["years_active"]:
        p["years_active"].append(r[1])
    if isinstance(r[2], int):
        p["year_start"] = r[2]
    if isinstance(r[3], int):
        p["year_end"] = r[3]; p["still_working"] = False
    elif isinstance(r[3], str) and "настоящее" in r[3]:
        p["still_working"] = True
persons_list = []
for i, p in enumerate(persons.values(), 1):
    p["years_active"].sort()
    persons_list.append({"id": i, **p})
dump("persons.json", persons_list)
PER = {p["surname"]: p["id"] for p in persons_list}

# ---------------- taxa ----------------
# (name_lat, name_ru, genus, family, order, class, group, [(variant, lang, kind)])
T = [
    ("Salix caprea", "Ива козья", "Salix", "Salicaceae", "Malpighiales", "Magnoliopsida", "сосудистые растения", [("ива козья", "ru", "variant")]),
    ("Tussilago farfara", "Мать-и-мачеха", "Tussilago", "Asteraceae", "Asterales", "Magnoliopsida", "сосудистые растения", [("мать-и-мачеха обыкновенная", "ru", "variant")]),
    ("Betula nana", "Береза карликовая", "Betula", "Betulaceae", "Fagales", "Magnoliopsida", "сосудистые растения", [("Берёза карликовая", "ru", "variant"), ("карликовая березка", "ru", "variant")]),
    ("Betula pubescens", "Береза пушистая", "Betula", "Betulaceae", "Fagales", "Magnoliopsida", "сосудистые растения", [("Берёза пушистая", "ru", "variant"), ("Betula pubescens Ehrh.", "lat", "variant")]),
    ("Pinus sylvestris", "Сосна обыкновенная", "Pinus", "Pinaceae", "Pinales", "Pinopsida", "сосудистые растения", [("Pinus sylvestris L.", "lat", "variant")]),
    ("Rubus chamaemorus", "Морошка", "Rubus", "Rosaceae", "Rosales", "Magnoliopsida", "сосудистые растения", [("Морошка приземистая", "ru", "variant")]),
    ("Vaccinium myrtillus", "Черника", "Vaccinium", "Ericaceae", "Ericales", "Magnoliopsida", "сосудистые растения", [("Черника обыкновенная", "ru", "variant")]),
    ("Vaccinium vitis-idaea", "Брусника", "Vaccinium", "Ericaceae", "Ericales", "Magnoliopsida", "сосудистые растения", [("Брусника обыкновенная", "ru", "variant")]),
    ("Vaccinium uliginosum", "Голубика", "Vaccinium", "Ericaceae", "Ericales", "Magnoliopsida", "сосудистые растения", [("Голубика болотная", "ru", "variant")]),
    ("Empetrum hermaphroditum", "Водяника обоеполая", "Empetrum", "Ericaceae", "Ericales", "Magnoliopsida", "сосудистые растения", [("Вороника", "ru", "variant"), ("Вороника обоеполая", "ru", "variant")]),
    ("Rhododendron tomentosum", "Багульник болотный", "Rhododendron", "Ericaceae", "Ericales", "Magnoliopsida", "сосудистые растения", [("Ledum palustre", "lat", "synonym"), ("Ledum palustre L.", "lat", "synonym")]),
    ("Cornus suecica", "Дерен шведский", "Cornus", "Cornaceae", "Cornales", "Magnoliopsida", "сосудистые растения", [("Chamaepericlymenum suecicum", "lat", "synonym"), ("Дёрен шведский", "ru", "variant")]),
    ("Geranium sylvaticum", "Герань лесная", "Geranium", "Geraniaceae", "Geraniales", "Magnoliopsida", "сосудистые растения", [("Geranium sylvaticum L.", "lat", "variant"), ("герани лесной", "ru", "variant")]),
    ("Trollius europaeus", "Купальница европейская", "Trollius", "Ranunculaceae", "Ranunculales", "Magnoliopsida", "сосудистые растения", [("Trollius europaea", "lat", "variant"), ("Купальница европ.", "ru", "variant")]),
    ("Eriophorum vaginatum", "Пушица влагалищная", "Eriophorum", "Cyperaceae", "Poales", "Liliopsida", "сосудистые растения", []),
    ("Alces alces", "Лось", "Alces", "Cervidae", "Artiodactyla", "Mammalia", "млекопитающие", [("лосиха", "ru", "variant"), ("лосенок", "ru", "variant"), ("Moose", "lat", "variant")]),
    ("Ursus arctos", "Бурый медведь", "Ursus", "Ursidae", "Carnivora", "Mammalia", "млекопитающие", [("медведь", "ru", "variant"), ("Ursus arctos L.", "lat", "variant")]),
    ("Lepus timidus", "Заяц-беляк", "Lepus", "Leporidae", "Lagomorpha", "Mammalia", "млекопитающие", [("Заяц беляк", "ru", "variant"), ("беляк", "ru", "variant")]),
    ("Vulpes vulpes", "Обыкновенная лисица", "Vulpes", "Canidae", "Carnivora", "Mammalia", "млекопитающие", [("Лисица", "ru", "variant"), ("лиса", "ru", "variant")]),
    ("Mustela erminea", "Горностай", "Mustela", "Mustelidae", "Carnivora", "Mammalia", "млекопитающие", []),
    ("Neogale vison", "Американская норка", "Neogale", "Mustelidae", "Carnivora", "Mammalia", "млекопитающие", [("Neovison vison", "lat", "synonym"), ("Mustela vison", "lat", "synonym"), ("Норка американская", "ru", "variant")]),
    ("Martes martes", "Лесная куница", "Martes", "Mustelidae", "Carnivora", "Mammalia", "млекопитающие", [("Куница", "ru", "variant")]),
    ("Gulo gulo", "Росомаха", "Gulo", "Mustelidae", "Carnivora", "Mammalia", "млекопитающие", []),
    ("Sciurus vulgaris", "Обыкновенная белка", "Sciurus", "Sciuridae", "Rodentia", "Mammalia", "млекопитающие", [("Белка", "ru", "variant")]),
    ("Rangifer tarandus", "Северный олень", "Rangifer", "Cervidae", "Artiodactyla", "Mammalia", "млекопитающие", []),
    ("Cygnus cygnus", "Лебедь-кликун", "Cygnus", "Anatidae", "Anseriformes", "Aves", "птицы", [("кликун", "ru", "variant")]),
    ("Bucephala clangula", "Гоголь", "Bucephala", "Anatidae", "Anseriformes", "Aves", "птицы", [("Обыкновенный гоголь", "ru", "variant")]),
    ("Lagopus lagopus", "Белая куропатка", "Lagopus", "Phasianidae", "Galliformes", "Aves", "птицы", []),
    ("Tetrao urogallus", "Глухарь", "Tetrao", "Phasianidae", "Galliformes", "Aves", "птицы", [("Обыкновенный глухарь", "ru", "variant")]),
    ("Cuculus canorus", "Обыкновенная кукушка", "Cuculus", "Cuculidae", "Cuculiformes", "Aves", "птицы", [("Кукушка", "ru", "variant")]),
    ("Parus major", "Большая синица", "Parus", "Paridae", "Passeriformes", "Aves", "птицы", []),
    ("Plectrophenax nivalis", "Пуночка", "Plectrophenax", "Calcariidae", "Passeriformes", "Aves", "птицы", []),
]
taxa, taxon_names = [], []
for i, (lat, ru, g, fam, order, cls, grp, vs) in enumerate(T, 1):
    taxa.append({"id": i, "name_lat": lat, "name_ru": ru, "rank": "species", "genus": g, "family": fam,
                 "order": order, "class": cls, "group_ru": grp, "gbif_key": None})
    taxon_names.append({"id": len(taxon_names) + 1, "taxon_id": i, "name": lat, "lang": "lat", "kind": "accepted"})
    taxon_names.append({"id": len(taxon_names) + 1, "taxon_id": i, "name": ru, "lang": "ru", "kind": "accepted"})
    for v, lang, kind in vs:
        taxon_names.append({"id": len(taxon_names) + 1, "taxon_id": i, "name": v, "lang": lang, "kind": kind})
dump("taxa.json", taxa)
dump("taxon_names.json", taxon_names)
TX = {t["name_lat"]: t["id"] for t in taxa}

# ---------------- phenophases ----------------
# Легенда «Фенофазы / баллы» к табл. 7.11–7.12, книга 2020, стр. 76 (PDF 76) — проверяем, что текст реально там есть.
legend_page = d27[75].get_text()
legend = [
    ("1", "BBCH 10", "начало роста, начало распускания почек"),
    ("2", "BBCH 11", "начало развёртывания листьев, «зеленение» [10% экз.]"),
    ("3", "BBCH 12-13", "появление полного листа [10% экз.]"),
    ("4", "BBCH 51", "начало бутонизации [10% экз.]"),
    ("5", "BBCH 60", "начало цветения [10% экз.]"),
    ("6", "BBCH 65", "массовое цветение [50% экз. и более]"),
    ("7", "BBCH 66-67", "начало отцветания [10% экз.]"),
    ("8", "BBCH 68-69", "массовое отцветание [50% экз. и более]"),
    ("9", "BBCH 69", "полное отцветание"),
    ("10а", "BBCH 81-86", "начало образования плодов, соплодий [10% экз. имеют незрелые плоды]"),
    ("10б", "BBCH 81-86", "начало созревания плодов (начало рассеивания семян) [10% экз. имеют зрелые плоды, начинают рассеивать семена]"),
    ("11", "BBCH 87", "массовое созревание, поспевание плодов, массовое рассеивание семян [50% экз. и более]"),
    ("12", "BBCH 92", "начало изменения окраски листьев [10% экз.]"),
    ("13", "BBCH 94", "массовое изменение окраски листьев [50% экз. и более]"),
    ("14", "BBCH 93", "начало листопада, начало отмирания трав [10% экз.]"),
    ("15", "BBCH 97", "массовое опадение листьев [50% экз. и более], массовое отмирание у трав"),
    ("16", "BBCH 87", "окончание листопада, полное отмирание надземной части"),
]
norm = lambda s: re.sub(r"\s+", " ", s)
lp = norm(legend_page)
phenophases = []
for code, bbch, name in legend:
    key = name.split(" [")[0].split(",")[0]
    found = key in lp and bbch.replace("BBCH ", "BBCH ") in lp
    phenophases.append({"id": len(phenophases) + 1, "code": code, "name_ru": name if found else None,
                        "applies_to": "plants", "bbch": bbch, "ord": len(phenophases) + 1})
    if not found:
        print("WARN legend not found:", code, key)
animals = [
    ("arrival", "прилёт (первая встреча весной перелётного вида)"),
    ("first_song", "первая песня / первое кукование"),
    ("first_seen_spring", "первая встреча весной (выход из берлоги, спячки)"),
    ("breeding", "гнездование, кладка яиц, гон"),
    ("young", "появление молодняка (выводок, новорождённые)"),
    ("moult_summer", "переход в летний наряд (мех, оперение)"),
    ("moult_winter", "переход в зимний наряд (мех, оперение)"),
    ("departure", "отлёт / последняя встреча осенью"),
]
for code, name in animals:
    phenophases.append({"id": len(phenophases) + 1, "code": code, "name_ru": name, "applies_to": "animals",
                        "bbch": None, "ord": len(phenophases) + 1})
dump("phenophases.json", phenophases)
PH = {(p["applies_to"], p["code"]): p["id"] for p in phenophases}

# ---------------- chunks ----------------
chunks, char_boxes = [], {}

def page_chunk(doc_id, pno):
    """Chunk = вся страница PDF (текстовый слой) + посимвольные bbox для расчёта bboxes цитат."""
    for c in chunks:
        if c["document_id"] == doc_id and c["locator"].get("pdf_page") == pno:
            return c
    page = d27[pno - 1]
    W, H = page.rect.width, page.rect.height
    raw = page.get_text("rawdict")
    text, boxes = [], []
    for b in raw["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b["lines"]:
            for span in line["spans"]:
                for ch in span["chars"]:
                    text.append(ch["c"]); boxes.append(ch["bbox"])
            text.append("\n"); boxes.append(None)
    t = "".join(text)
    printed = None
    m = re.match(r"\s*(\d{1,3})\s*\n", t)
    if m and int(m.group(1)) == pno:
        printed = m.group(1)
    cid = 100 + len(chunks) + 1
    c = {"id": cid, "document_id": doc_id, "section_id": section_for(doc_id, pno), "ord": pno, "kind": "page",
         "locator": {"pdf_page": pno, "half": None, "printed_page": printed or str(pno)},
         "text": t, "text_source": "text_layer", "ocr_confidence": None}
    chunks.append(c)
    char_boxes[cid] = (boxes, W, H)
    return c

def locate(c, quote, occurrence=0, after=None):
    """Возвращает quote_span и bboxes (по строкам, в долях страницы)."""
    t = c["text"]
    start = t.find(after) if after else 0
    assert start >= 0, after
    i = t.find(quote, start)
    for _ in range(occurrence):
        i = t.find(quote, i + 1)
    if i < 0:
        return None, []
    j = i + len(quote)
    boxes, W, H = char_boxes.get(c["id"], (None, 1, 1))
    out = []
    if boxes:
        cur = None
        for k in range(i, j):
            bb = boxes[k]
            if bb is None or t[k].isspace() and cur is None:
                if bb is None and cur:
                    out.append(cur); cur = None
                continue
            if cur is None:
                cur = list(bb)
            else:
                cur = [min(cur[0], bb[0]), min(cur[1], bb[1]), max(cur[2], bb[2]), max(cur[3], bb[3])]
        if cur:
            out.append(cur)
    rel = [[round(max(0, b[0] / W), 4), round(max(0, b[1] / H), 4), round(min(1, b[2] / W), 4), round(min(1, b[3] / H), 4)] for b in out]
    return [i, j], rel

# Сканы 1995: ручная расшифровка половин разворота PDF стр. 40 (печатные 76 и 77).
scan_L = (
    "Млекопитающие\n"
    "Результаты зимнего маршрутного учета зверей и птиц\n"
    "(март 1995 года)\n"
    "В 1995 году учет проводили сотрудники заповедника \"Пасвик\"\n"
    "А. М. Хохлов, Е. В. Сметанников и В. Е. Шершнев.\n"
    "При проведении учета маршруты № 1 и 2 не полностью совпа-\n"
    "ли с предыдущими, но в целом материал был собран с одной и той\n"
    "же территории, южнее горы Калкупя - между системой проволочных\n"
    "заграждений, берегом реки Паз, подножием горы и берегом озера\n"
    "Каскамаярви (см.рис. 1 и 29).\n"
    "Погода была довольно благоприятной для проведения учетных\n"
    "работ. 30 марта 1995 года учетчики начали работать после сильной\n"
    "пурги 29.03.95. В первый и второй день учета стояла тихая ясная\n"
    "погода с температурой от 0° до -4°С.\n"
    "Снегосъемка была сделана 31 марта. Высота снега в среднем\n"
    "по 10 промерам в редком молодом сосняке по болоту составила\n"
    "55 см; в сосняке на южном склоне горы - 65 см и в зарослях березня-\n"
    "ка у вышки - 64 см; в среднем по всем 30 промерам - 61.7 см, округ-\n"
    "ленно - 62 см (Lim. 48-77).\n"
    "Маршрут № 1 протяженностью 12 км (по спидометру) идет\n"
    "параллельно дороге и практически совпадает с маршрутом А. С. Ги-\n"
    "лязова.\n"
    "Маршрут № 2 протяженностью 10 км идет от линии инженер-\n"
    "ных сооружений (от автодороги) по направлению к реке Паз южнее\n"
    "озера Каскамаярви.\n"
    "Таблица 45\n"
    "Встречаемость следов зверей и птиц в марте 1995 года\n"
    "в сосновых лесах заповедника \"Пасвик\"\n"
    "Вид | Маршрут № 1 (12 км): Следов, абс. | на 10 км | Маршрут № 2 (10 км): Следов, абс. | на 10 км\n"
    "Белка | - | - | - | -\n"
    "Волк | - | - | - | -\n"
    "Выдра | - | - | - | -\n"
    "Горностай | 1 | 0.83 | 2 | 2.0\n"
    "76\n"
)
scan_R = (
    "Заяц-беляк | 9 | 7.50 | 4 | 4.0\n"
    "Куница | - | - | 2 | 2.0\n"
    "Лисица | 4 | 3.33 | 3 | 3.0\n"
    "Лось | - | - | - | -\n"
    "Норка американская | - | - | 2 | 2.0\n"
    "Росомаха | - | - | - | -\n"
    "Таким образом, видовое разнообразие зверей, отмеченных на\n"
    "маршруте, невелико. Численность их также в основном низкая.\n"
    "В целом общая протяженность маршрутов составила 22 км. Число\n"
    "следов на 10 км равнялось: горностай - 1.36; заяц-беляк - 5.90; куни-\n"
    "ца - 0.9; лисица - 3.18 и норка - 0.9.\n"
    "Можно предположить по\n"
    "косвенным данным, что лося в эту пору - в конце зимы - на террито-\n"
    "рии заповедника очень мало, практически единицы.\n"
    "77\n"
)
chunks.append({"id": 1, "document_id": 1, "section_id": 1, "ord": 79, "kind": "page",
               "locator": {"pdf_page": 40, "half": "L", "printed_page": "76"}, "text": scan_L,
               "text_source": "manual", "ocr_confidence": None})
chunks.append({"id": 2, "document_id": 1, "section_id": 1, "ord": 80, "kind": "page",
               "locator": {"pdf_page": 40, "half": "R", "printed_page": "77"}, "text": scan_R,
               "text_source": "manual", "ocr_confidence": None})
CH = {1: chunks[0], 2: chunks[1]}

# ---------------- observations ----------------
obs = []
def O(**kw):
    base = dict(record_type="observation", object_type="taxon", object_raw=None, taxon_id=None,
                date_raw=None, date_from=None, date_to=None, date_precision=None, obs_year=None,
                toponym_raw=None, place_id=None, coords_raw=None, habitat_raw=None,
                event_raw=None, phenophase_id=None, phenophase_raw=None,
                indicator=None, value_num=None, value_raw=None, unit=None,
                observers_raw=None, person_ids=[],
                pub_authors=None, pub_year=None, pub_title=None, pub_venue=None,
                document_id=3, section_id=None, chunk_id=None, locator=None, printed_pages=None,
                quote=None, quote_span=None, bboxes=[], note=None,
                confidence=0.8, flags=[], status="auto", extractor_version=EXV,
                created_at=TS, updated_at=TS)
    page = kw.pop("page", None)
    after = kw.pop("after", None)
    occ = kw.pop("occ", 0)
    keep_quote = kw.pop("keep_quote", False)
    chunk = kw.pop("chunk", None)
    base.update(kw)
    if page is not None:
        chunk = page_chunk(3, page)
    if chunk is not None:
        base["chunk_id"] = chunk["id"]; base["section_id"] = chunk["section_id"]
        base["document_id"] = chunk["document_id"]
        if base["locator"] is None:
            base["locator"] = dict(chunk["locator"])
        if base["printed_pages"] is None:
            base["printed_pages"] = chunk["locator"]["printed_page"]
        if base["quote"] is not None:
            span, bb = locate(chunk, base["quote"], occ, after)
            if span is None:
                assert "quote_not_found" in base["flags"], ("quote not found", base["quote"])
            else:
                base["quote_span"] = span
                base["bboxes"] = bb if chunk["text_source"] == "text_layer" else []
    base["id"] = len(obs) + 1
    obs.append(base)
    return base

def d(s):  # 'dd.mm.yyyy' -> ISO
    dd, mm, yy = s.split(".")
    return f"{yy}-{mm}-{dd}"

PL5 = PH[("plants", "5")]

# --- табл. 7.10: герань лесная, одна строка — три года (стр. 71) ---
c71 = page_chunk(3, 71)
row_geran = c71["text"][c71["text"].find("29. \nГерань лесная"):c71["text"].find("22.06") + len("22.06")]
for yr, dt, fl in [(2020, "19.06", []), (2019, "24.06", ["year_outside_book"]), (2018, "22.06", ["year_outside_book"])]:
    O(page=71, object_raw="Герань лесная Geranium sylvaticum L.", taxon_id=TX["Geranium sylvaticum"],
      date_raw=dt, date_from=d(f"{dt}.{yr}"), date_to=d(f"{dt}.{yr}"), date_precision="day", obs_year=yr,
      toponym_raw=None, place_id=None, event_raw="зацветание (дата зацветания высших растений, табл. 7.10)",
      phenophase_id=PL5, phenophase_raw="зацветание", observers_raw=None,
      quote=row_geran,
      note=f"Таблица 7.10 «Сравнительная хронология зацветания высших растений … за период 2018 - 2020 гг.», столбец {yr}. Год наблюдения взят из заголовка столбца.",
      confidence=0.9, flags=fl)

# --- табл. 7.10: береза пушистая 2020 (стр. 69) ---
c69 = page_chunk(3, 69)
row_bp = c69["text"][c69["text"].find("7. \nБереза пушистая"):]
row_bp = row_bp[:row_bp.find("18.05") + len("18.05")]
O(page=69, object_raw="Береза пушистая Betula pubescens Ehrh.", taxon_id=TX["Betula pubescens"],
  date_raw="03.06", date_from="2020-06-03", date_to="2020-06-03", date_precision="day", obs_year=2020,
  event_raw="зацветание (дата зацветания высших растений, табл. 7.10)", phenophase_id=PL5, phenophase_raw="зацветание",
  observers_raw="Поликарпова Н.В., Бычков Ю.М, Крюкова С.А., Воробьева Н.Г.",
  person_ids=[PER["Поликарпова"], PER["Бычков"], PER["Крюкова"], PER["Воробьева"]],
  quote=row_bp, note="Наблюдатели — авторы раздела 7.3 (указаны под заголовком раздела на этой же странице).",
  confidence=0.85)

# --- календарь природы: зацветание герани лесной (стр. 47) ---
O(page=47, object_raw="Зацветание герани лесной", taxon_id=TX["Geranium sylvaticum"],
  date_raw="19.06", date_from="2020-06-19", date_to="2020-06-19", date_precision="day", obs_year=2020,
  event_raw="Зацветание герани лесной", phenophase_id=PL5, phenophase_raw="зацветание",
  quote="Зацветание герани лесной \n19.06 \n22.06 \n01.06.13 \n04.07.17 \n26 ",
  note="Календарь природы 2020 г.: дата 2020 г. 19.06; средняя многолетняя 22.06; самая ранняя 01.06.13; самая поздняя 04.07.17; 26 лет наблюдений.",
  confidence=0.9)

# --- табл. 7.11: фенология герани лесной, фаза 5 (стр. 76) — пример правки ---
c76 = page_chunk(3, 76)
q76 = "Долина ручья с березово-ивовым лесом \nGeranium sylvaticum  \n(Герань лесная) \n01.06 \n03.06 \n15.06 \n13.06 \n23.06 \n03.07 \n"
o_edit = O(page=76, object_raw="Geranium sylvaticum (Герань лесная)", taxon_id=TX["Geranium sylvaticum"],
  date_raw="23.06", date_from="2020-06-23", date_to="2020-06-23", date_precision="day", obs_year=2020,
  toponym_raw="р. Мениккайоки", place_id=P["Мениккайоки"], habitat_raw="Долина ручья с березово-ивовым лесом",
  event_raw="фаза 5 - начало цветения", phenophase_id=PL5, phenophase_raw="5 (BBCH 60) - начало цветения",
  quote=q76, printed_pages="75-76",
  note="Таблица 7.11 «Фенология растительных сообществ в 2020 году (фенологический маршрут в окрестностях р. Мениккайоки)»; топоним из заголовка таблицы на стр. 75. Столбцы восстановлены по порядку, пустые ячейки в тексте не видны.",
  confidence=0.7, flags=["table_parse_uncertain"], status="edited")

# --- календарь природы: снег, лёд, сезоны (стр. 46, 48) ---
O(page=46, record_type="env_measurement", object_type="env_indicator", object_raw="Установление устойчивого снежного покрова",
  date_raw="20.10.19", date_from="2019-10-20", date_to="2019-10-20", date_precision="day", obs_year=2019,
  event_raw="Установление устойчивого снежного покрова", indicator="дата установления устойчивого снежного покрова",
  quote="Установление устойчивого снежного покрова \n20.10.19 \n29.10 \n07.10.93 \n05.12.18 \n21 ",
  note="Календарь природы, сезон «Предзимье» 2019 г. (начало фенологического года).", confidence=0.9, flags=["year_outside_book"])
O(page=48, record_type="env_measurement", object_type="env_indicator", object_raw="Установление устойчивого снежного покрова",
  date_raw="23.11", date_from="2020-11-23", date_to="2020-11-23", date_precision="day", obs_year=2020,
  event_raw="Установление устойчивого снежного покрова", indicator="дата установления устойчивого снежного покрова",
  quote="Установление устойчивого снежного покрова \n23.11 \n30.10 \n07.10.93 \n02.12.00 \n21 ", confidence=0.9)
O(page=46, record_type="env_measurement", object_type="phenomenon", object_raw="Первый ледостав на р. Паз",
  date_raw="28.10.19", date_from="2019-10-28", date_to="2019-10-28", date_precision="day", obs_year=2019,
  toponym_raw="р. Паз", place_id=P["Паз"], event_raw="Первый ледостав", indicator="дата первого ледостава",
  quote="Первый ледостав на р. Паз \n28.10.19 \n04.11 \n05.10.01 \n27.11.18 \n12 ", confidence=0.9, flags=["year_outside_book"])
O(page=12, record_type="env_measurement", object_type="env_indicator", object_raw="Продолжительность весны",
  date_raw="с 01 по 31 мая", date_from="2020-05-01", date_to="2020-05-31", date_precision="range", obs_year=2020,
  indicator="продолжительность весны", value_num=31, value_raw="31 день", unit="дней",
  quote="В 2020 г. самая короткая за период \nнаблюдений весна, которая длится 31 день (с 01 по 31 мая).", confidence=0.9)
O(page=13, record_type="env_measurement", object_type="env_indicator", object_raw="Начало зимы (устойчивый переход среднесуточных температур воздуха ниже 0°С)",
  date_raw="16 октября 2019 г.", date_from="2019-10-16", date_to="2019-10-16", date_precision="day", obs_year=2019,
  place_id=P["Пасвик"], toponym_raw="на севере и на юге заповедника", indicator="дата начала зимы",
  quote="Начало зимы (устойчивый переход среднесуточных температур воздуха ниже 0°С) \nприходится на дату 16 октября 2019 г. на севере и на юге заповедника.",
  confidence=0.85, flags=["year_outside_book"])
O(page=13, record_type="env_measurement", object_type="env_indicator", object_raw="Продолжительность зимы",
  date_raw=None, date_precision=None, obs_year=2020,
  indicator="продолжительность зимы", value_num=195, value_raw="195 дней", unit="дней",
  quote="Продолжительность зимы в заповеднике составила 195 дней",
  note="Зима 2019/2020 г.; даты начала/конца в цитате нет — date_from/date_to не заполнены.", confidence=0.8)

# --- календарь природы: животные (стр. 46, 47) ---
O(page=46, object_raw="Первая встреча медведя", taxon_id=TX["Ursus arctos"],
  date_raw="28.03", date_from="2020-03-28", date_to="2020-03-28", date_precision="day", obs_year=2020,
  event_raw="Первая встреча медведя", phenophase_id=PH[("animals", "first_seen_spring")], phenophase_raw="первая встреча",
  quote="Первая встреча медведя \n28.03 \n29.04 \n03.04.03 \n03.05.11 \n20 ", confidence=0.9)
O(page=46, object_raw="Прилет лебедя", taxon_id=TX["Cygnus cygnus"],
  date_raw="13.03", date_from="2020-03-13", date_to="2020-03-13", date_precision="day", obs_year=2020,
  event_raw="Прилет лебедя", phenophase_id=PH[("animals", "arrival")], phenophase_raw="прилет",
  quote="Прилет лебедя \n13.03 \n02.04 \n11.03.03 \n24.04.98 \n27 ",
  note="В тексте «лебедь» без вида; сопоставлено с лебедем-кликуном как самым обычным видом — требует проверки.",
  confidence=0.6, flags=["taxon_ambiguous"])
O(page=46, object_raw="Прилет пуночки", taxon_id=TX["Plectrophenax nivalis"],
  date_raw="20.03", date_from="2020-03-20", date_to="2020-03-20", date_precision="day", obs_year=2020,
  event_raw="Прилет пуночки", phenophase_id=PH[("animals", "arrival")], phenophase_raw="прилет",
  quote="Прилет пуночки \n20.03 \n31.03 \n20.03.08 \n11.04.96 \n26 ", confidence=0.9)
O(page=46, object_raw="Первая песня большой синицы", taxon_id=TX["Parus major"],
  date_raw="11.02", date_from="2020-02-11", date_to="2020-02-11", date_precision="day", obs_year=2020,
  event_raw="Первая песня большой синицы", phenophase_id=PH[("animals", "first_song")], phenophase_raw="первая песня",
  quote="Первая песня большой синицы  \n11.02 \n23.02 \n25.01.16 \n03.04.97 \n26 ", confidence=0.9)
O(page=47, object_raw="Первое кукование кукушки", taxon_id=TX["Cuculus canorus"],
  date_raw="25.05", date_from="2020-05-25", date_to="2020-05-25", date_precision="day", obs_year=2020,
  event_raw="Первое кукование кукушки", phenophase_id=PH[("animals", "first_song")], phenophase_raw="первое кукование",
  quote="Первое кукование кукушки \n25.05 \n29.05 \n21.05.96 \n11.06.09 \n25 ", confidence=0.9)

# --- птицы: таблица учёта на маршрутах (стр. 157), колонки идут столбиком ---
O(page=157, object_raw="Лебедь-кликун (Cygnus cygnus)", taxon_id=TX["Cygnus cygnus"],
  date_raw=None, date_precision=None, obs_year=2020,
  toponym_raw="Остров Варлама – Йорданфосс (5.3)", place_id=P["Варлама"],
  event_raw="учёт птиц на маршруте", indicator="количество особей (встреч)", value_num=90, value_raw="90(1)", unit="особей",
  quote="2. Лебедь-кликун \n(Cygnus cygnus) \n90(1) \n",
  note="Значение отнесено к первому маршруту по порядку столбцов; в текстовом слое таблица идёт столбиком, пропуски обозначены «-». Год — по году книги.",
  confidence=0.6, flags=["table_parse_uncertain"])

# --- млекопитающие: сводная таблица (стр. 449) ---
O(page=449, object_raw="Лось Alces alces", taxon_id=TX["Alces alces"], obs_year=2020,
  event_raw="встречи при полевых исследованиях", indicator="количество особей/встреч", value_num=32, value_raw="32/22", unit="особей",
  observers_raw="Валовой Е.В.", person_ids=[PER["Валова"]],
  quote="11.  Лось Alces alces \n32/22", note="32 особи за 22 встречи за полевой сезон 2020 г.", confidence=0.8)
O(page=449, object_raw="Заяц-беляк Lepus timidus", taxon_id=TX["Lepus timidus"], obs_year=2020,
  event_raw="встречи при полевых исследованиях", indicator="количество особей/встреч", value_num=39, value_raw="39/15", unit="особей",
  observers_raw="Валовой Е.В.", person_ids=[PER["Валова"]],
  quote="2.  Заяц-беляк Lepus timidus 39/15",
  note="Цитата нормализована экстрактором (в тексте «Заяц- беляк» с пробелом и перенос строки) — дословно не найдена.",
  confidence=0.5, flags=["quote_not_found"])

# --- бурый медведь (стр. 486) ---
O(page=486, object_raw="Бурый медведь Ursus arctos", taxon_id=TX["Ursus arctos"],
  date_raw="03.06.2020 г.", date_from="2020-06-03", date_to="2020-06-03", date_precision="day", obs_year=2020,
  toponym_raw="Урочище Латвала", place_id=P["Латвала"], habitat_raw="лесистая гряда среди болот",
  event_raw="встреча (фото)", value_num=1, value_raw="встречен единожды", unit="особей",
  observers_raw="Валова Е.В.", person_ids=[PER["Валова"]],
  quote="Рисунок 8.513 - Бурый медведь Ursus arctos. Урочище Латвала, 03.06.2020 г. Фото: Валова Е.В. ",
  note="Местообитание — из текста выше на той же странице: «встречен единожды в урочище Латвала на лесистой гряде среди болот».",
  confidence=0.9, status="verified")
O(page=486, object_raw="Бурый медведь", taxon_id=TX["Ursus arctos"], obs_year=2020,
  toponym_raw="южное подножие горы Калкупя", place_id=P["Калкупя"], habitat_raw="у муравейника",
  event_raw="найдена берлога", indicator="число берлог", value_num=2, value_raw="две берлоги", unit="берлог",
  quote="Найдено две берлоги: одна располагалась у \nмуравейника возле южного подножия горы Калкупя",
  note="Вторая берлога — на болоте Кайтоапа (в той же фразе). Дата в цитате не указана (фото берлоги на стр. 487 — 14.06.2020).",
  confidence=0.75)

# --- лось (стр. 497–500) ---
O(page=497, object_raw="новорожденный лосенок", taxon_id=TX["Alces alces"],
  date_raw="14.06.2020 г.", date_from="2020-06-14", date_to="2020-06-14", date_precision="day", obs_year=2020,
  toponym_raw="болото южнее г. Калкупя", place_id=P["Калкупя"], habitat_raw="небольшая залесенная гряда на болоте",
  event_raw="обнаружен новорожденный лосенок", phenophase_id=PH[("animals", "young")], phenophase_raw="новорожденный",
  value_num=1, value_raw="лосенок", unit="особей",
  observers_raw="Валовой Е.В.", person_ids=[PER["Валова"]],
  quote="На \nнебольшой залесенной гряде на болоте южнее г. Калкупя 14.06.2020 г. был обнаружен \nноворожденный лосенок",
  confidence=0.9, status="verified")
O(page=498, object_raw="Лосиные следы", taxon_id=TX["Alces alces"],
  date_raw="13.01", date_from="2020-01-13", date_to="2020-01-13", date_precision="day", obs_year=2020,
  toponym_raw="у озера Каскама", place_id=None, event_raw="следы поднимались в гору",
  observers_raw="Бычков Ю.М.", person_ids=[PER["Бычков"]],
  quote="13.01 \nЛосиные следы поднимались в гору у озера Каскама (Бычков Ю.М.)",
  note="В каталоге «Каскама» — гора, озеро — «Каскамаярви»; топоним не сопоставлен однозначно.",
  confidence=0.7, flags=["place_unmatched"])
O(page=499, object_raw="лосей", taxon_id=TX["Alces alces"],
  date_raw="30.04 (10:30)", date_from="2020-04-30", date_to="2020-04-30", date_precision="day", obs_year=2020,
  toponym_raw=None, event_raw="проходило стадо", value_num=13, value_raw="13(!)", unit="особей",
  observers_raw="Дмитренко Г.А.", person_ids=[PER["Дмитренко"]],
  quote="30.04 (10:30) \nВ это же время там проходило стадо из 13(!) лосей, есть видеозапись у \nАнисимовой Е.С. (Дмитренко Г.А.)",
  note="«там» отсылает к предыдущей записи (оз. Каскамаярви) — топоним в цитате отсутствует и не подставлен.",
  confidence=0.85)
O(page=499, object_raw="Четыре лося", taxon_id=TX["Alces alces"],
  date_raw="21.05", date_from="2020-05-21", date_to="2020-05-21", date_precision="day", obs_year=2020,
  toponym_raw="за ИТС у Каскамаярви", place_id=P["Каскамаярви"],
  event_raw="встреча; все самцы, 3 взрослых и 1 молодой 2-х летний телёнок", value_num=4, value_raw="Четыре", unit="особей",
  observers_raw="Поликарпова Н.В.", person_ids=[PER["Поликарпова"]],
  quote="21.05 \nЧетыре лося за ИТС у Каскамаярви, все самцы, 3 взрослых и 1 молодой 2-х \nлетний телёнок. Есть фото (Поликарпова Н.В.)",
  confidence=0.9)
O(page=499, object_raw="Две особи", taxon_id=TX["Alces alces"],
  date_raw="23.06 (15:00)", date_from="2020-06-23", date_to="2020-06-23", date_precision="day", obs_year=2020,
  toponym_raw="о-ве Варлама", place_id=P["Варлама"], event_raw="переплывали реку", value_num=2, value_raw="Две", unit="особей",
  observers_raw="Зацаринный И.В.", person_ids=[],
  quote="23.06 (15:00) \nДве особи переплывали реку на о-ве Варлама (Зацаринный И.В.)",
  note="Зацаринный И.В. отсутствует в каталоге сотрудников.", confidence=0.8, flags=["person_unmatched"])
O(page=499, object_raw="Четыре молодых лося", taxon_id=TX["Alces alces"],
  date_raw="12.10 (09:50)", date_from="2020-10-12", date_to="2020-10-12", date_precision="day", obs_year=2020,
  toponym_raw="за ИТС у устья Лауккуйоки", place_id=P["Лауккуйоки"], event_raw="стояли; туман, +5 градусов",
  value_num=4, value_raw="Четыре", unit="особей",
  observers_raw="Поликарпова Н.В.", person_ids=[PER["Поликарпова"]],
  quote="12.10 (09:50) \nЧетыре молодых лося стояли за ИТС у устья Лауккуйоки. Туман, +5 градусов \n(Поликарпова Н.В.)",
  confidence=0.9)
O(page=500, object_raw="лосей", taxon_id=TX["Alces alces"], date_precision=None, obs_year=2020,
  place_id=P["Пасвик"], toponym_raw="в заповеднике и окрестностях", event_raw="регистрация следов на ЗМУ", indicator="следов на 10 км маршрута",
  value_num=3.58, value_raw="3,58 следов/10 км", unit="следов/10 км", observers_raw="Дмитренко, 2020", person_ids=[PER["Дмитренко"]],
  quote="Показатель учета – 3,58 следов/10 км – незначительно больше, чем в прошлом году",
  note="Зимний маршрутный учёт (ЗМУ) 2020 г.; точной даты в цитате нет.", confidence=0.85)
O(page=500, object_raw="лосей", taxon_id=TX["Alces alces"], date_precision=None, obs_year=2019,
  place_id=P["Пасвик"], toponym_raw="в заповеднике и окрестностях", event_raw="регистрация следов на ЗМУ", indicator="следов на 10 км маршрута",
  value_num=3.17, value_raw="3,17", unit="следов/10 км",
  quote="Показатель учета – 3,58 следов/10 км – незначительно больше, чем в прошлом году",
  note="Пример ошибки экстрактора: значение прошлого года (3,17) взято из продолжения фразы, которое не попало в цитату.",
  confidence=0.4, flags=["value_not_in_quote", "year_outside_book"], occ=0)

# --- публикации ---
c526 = page_chunk(3, 526)
q_bear = ("2. Смирнов М. Н., Макарова О. А., Региональные особенности питания бурого \n"
          "медведя (Ursus arctos L. 1758) в нажировочный период и их экологические \nпоследствия")
O(page=526, record_type="publication", object_type="publication",
  object_raw="Региональные особенности питания бурого медведя (Ursus arctos L. 1758) в нажировочный период и их экологические последствия",
  taxon_id=TX["Ursus arctos"], obs_year=2020, date_raw="апрель-июнь 2020", date_from="2020-04-01", date_to="2020-06-30", date_precision="range",
  pub_authors="Смирнов М. Н., Макарова О. А.", pub_year=2020,
  pub_title="Региональные особенности питания бурого медведя (Ursus arctos L. 1758) в нажировочный период и их экологические последствия",
  pub_venue="Научно-практический и теоретический журнал «Вестник Охотоведения» Том 17, № 2 апрель-июнь 2020. С. 64-71",
  observers_raw=None, person_ids=[PER["Макарова"]], quote=q_bear,
  note="Раздел 10 «Научные исследования», научные статьи сотрудников. person_ids — автор-сотрудник (Макарова).", confidence=0.85)
q_kost = "Костина В.А. Флора заповедника «Пасвик» (сосудистые растения). Апатиты, 1995. 52 с."
O(page=601, record_type="publication", object_type="publication", object_raw="Флора заповедника «Пасвик» (сосудистые растения)",
  obs_year=1995, date_raw="1995", date_from="1995-01-01", date_to="1995-12-31", date_precision="year",
  pub_authors="Костина В.А.", pub_year=1995, pub_title="Флора заповедника «Пасвик» (сосудистые растения)", pub_venue="Апатиты, 1995. 52 с.",
  quote=q_kost, confidence=0.9, flags=["year_outside_book"])
q_zac = ("Зацаринный И.В., Большаков А.А., Бузун В.А., Макарова О.А., Поликарпова Н.В., Катаев \n"
         "Г.Д., Терентьев П.М., Собчук И.С., Бычков Ю.М., Булычева И.А., Бузун М.В., \n"
         "Варюхин В.С., Шаврина У.Ю., Огурцов С.С. Позвоночные животные заповедника \n"
         "«Пасвик» / Под ред. Н. В. Поликарповой. Петрозаводск: КарНЦ РАН, 2018. 218 c.")
O(page=600, record_type="publication", object_type="publication", object_raw="Позвоночные животные заповедника «Пасвик»",
  obs_year=2018, date_raw="2018", date_from="2018-01-01", date_to="2018-12-31", date_precision="year",
  pub_authors="Зацаринный И.В., Большаков А.А., Бузун В.А., Макарова О.А., Поликарпова Н.В., Катаев Г.Д., Терентьев П.М., Собчук И.С., Бычков Ю.М., Булычева И.А., Бузун М.В., Варюхин В.С., Шаврина У.Ю., Огурцов С.С.",
  pub_year=2018, pub_title="Позвоночные животные заповедника «Пасвик»", pub_venue="Под ред. Н. В. Поликарповой. Петрозаводск: КарНЦ РАН, 2018. 218 c.",
  person_ids=[PER["Макарова"], PER["Поликарпова"], PER["Бычков"], PER["Шаврина"]],
  quote=q_zac, confidence=0.9, flags=["year_outside_book"])

# --- 1995, книга 2: ЗМУ (разворот PDF 40, печ. 76-77) ---
L, R = CH[1], CH[2]
loc_R = {"pdf_page": 40, "half": "R", "printed_page": "77"}
obs1995 = dict(document_id=1, date_raw="30 марта 1995 года … В первый и второй день учета", date_from="1995-03-30", date_to="1995-03-31",
               date_precision="range", obs_year=1995, observers_raw="А. М. Хохлов, Е. В. Сметанников и В. Е. Шершнев",
               person_ids=[PER["Хохлов"], PER["Сметанников"], PER["Шершнев"]], printed_pages="76-77")
O(chunk=R, object_raw="Заяц-беляк", taxon_id=TX["Lepus timidus"], **obs1995,
  toponym_raw="Калкупя, южнее озера Каскамаярви", place_id=P["Калкупя"], habitat_raw="маршрут 1",
  event_raw="регистрация следов на ЗМУ", indicator="следов на 10 км маршрута", value_num=7.5, value_raw="7.50", unit="следов/10 км",
  quote="Заяц-беляк | 9 | 7.50 | 4 | 4.0",
  note="кол-во следов на 10 км маршрута. Маршрут № 1 (12 км), 9 следов абс. Эталон шаблона даёт дату 31.03.1995; в тексте учёт 30–31 марта.",
  confidence=0.9, status="verified", flags=["date_ambiguous"])
O(chunk=R, object_raw="Заяц-беляк", taxon_id=TX["Lepus timidus"], **obs1995,
  toponym_raw="Калкупя, южнее озера Каскамаярви", place_id=P["Калкупя"], habitat_raw="маршрут 2",
  event_raw="регистрация следов на ЗМУ", indicator="следов на 10 км маршрута", value_num=4.0, value_raw="4.0", unit="следов/10 км",
  quote="Заяц-беляк | 9 | 7.50 | 4 | 4.0",
  note="кол-во следов на 10 км маршрута. Маршрут № 2 (10 км), 4 следа абс.", confidence=0.9, flags=["date_ambiguous"])
O(chunk=R, object_raw="заяц-беляк", taxon_id=TX["Lepus timidus"], **obs1995,
  toponym_raw="южнее горы Калкупя", place_id=P["Калкупя"], habitat_raw="сосновые леса, маршруты № 1 и 2 (22 км)",
  event_raw="регистрация следов на ЗМУ", indicator="следов на 10 км маршрута", value_num=5.90, value_raw="5.90", unit="следов/10 км",
  quote="следов на 10 км равнялось: горностай - 1.36; заяц-беляк - 5.90",
  note="Среднее по обоим маршрутам. pdf_page определён по печатным номерам: разворот PDF 40 = печатные 76 (L) и 77 (R).",
  confidence=0.85, flags=["date_ambiguous"])
O(chunk=R, object_raw="лося", taxon_id=TX["Alces alces"], document_id=1, obs_year=1995,
  date_raw="в конце зимы", date_precision="season", observers_raw="А. М. Хохлов, Е. В. Сметанников и В. Е. Шершнев",
  person_ids=[PER["Хохлов"], PER["Сметанников"], PER["Шершнев"]], printed_pages="77",
  place_id=P["Пасвик"], toponym_raw="на территории заповедника", event_raw="оценка численности по косвенным данным (ЗМУ)",
  value_raw="практически единицы",
  quote="лося в эту пору - в конце зимы - на террито-\nрии заповедника очень мало, практически единицы.",
  note="На ЗМУ следов лося не отмечено (строка «Лось | - | - | - | -» таблицы 45).", confidence=0.7)
O(chunk=L, record_type="env_measurement", object_type="env_indicator", object_raw="Высота снега", document_id=1,
  date_raw="31 марта", date_from="1995-03-31", date_to="1995-03-31", date_precision="day", obs_year=1995,
  toponym_raw="южнее горы Калкупя", place_id=P["Калкупя"], habitat_raw="сосняк, болото, березняк (30 промеров)",
  indicator="высота снега, среднее по 30 промерам", value_num=61.7, value_raw="61.7 см", unit="см",
  observers_raw="А. М. Хохлов, Е. В. Сметанников и В. Е. Шершнев",
  person_ids=[PER["Хохлов"], PER["Сметанников"], PER["Шершнев"]], printed_pages="76",
  quote="в среднем по всем 30 промерам - 61.7 см",
  note="Снегосъемка 31 марта 1995 г.", confidence=0.85)

dump_jsonl("chunks.jsonl", sorted(chunks, key=lambda c: c["id"]))
dump_jsonl("observations.jsonl", obs)

# ---------------- edits ----------------
edits = [
    {"id": 1, "observation_id": o_edit["id"], "action": "edit", "editor": "Поликарпова Н.В.",
     "changes": {"date_raw": {"old": "13.06", "new": "23.06"},
                 "date_from": {"old": "2020-06-13", "new": "2020-06-23"},
                 "date_to": {"old": "2020-06-13", "new": "2020-06-23"}},
     "status_before": "auto", "status_after": "edited",
     "comment": "Экстрактор сдвинул столбец: 13.06 — фаза 4 (бутонизация), начало цветения (фаза 5) — 23.06. Сверено со сканом стр. 76.",
     "created_at": "2026-11-22T13:05:00Z"},
]
for o in obs:
    if o["status"] == "verified":
        edits.append({"id": len(edits) + 1, "observation_id": o["id"], "action": "verify", "editor": "Поликарпова Н.В.",
                      "changes": {}, "status_before": "auto", "status_after": "verified",
                      "comment": "Сверено с исходной страницей.", "created_at": "2026-11-22T13:10:00Z"})
dump_jsonl("observation_edits.jsonl", edits)

# ---------------- saved queries ----------------
saved = [
    {"id": 1, "name": "Запрос 1: наблюдения Макаровой и Сметанникова, 1995–1996",
     "text": "Создать сводную таблицу данных, которые зарегистрировали Макарова и Сметанников в 1995-1996 году (на основании пдф-версий сканированной Летописи природы за 1995 и 1996 год)",
     "filters": {"person": ["Макарова", "Сметанников"], "years": [1995, 1996], "doc_years": [1995, 1996], "sort": "date_from", "order": "asc"},
     "created_at": TS},
    {"id": 2, "name": "Запрос 2: лось в 1995, 1996, 2020 по хронологии",
     "text": "Создать таблицу со всеми наблюдениями за лосем в 1995, 1996 и 2022 годах, расположить все наблюдения по хронологии",
     "filters": {"taxon_id": TX["Alces alces"], "years": [1995, 1996, 2020], "record_type": "observation", "sort": "date_from", "order": "asc"},
     "created_at": TS},
    {"id": 3, "name": "Запрос 3: публикации о буром медведе",
     "text": "Создать список всех публикаций о буром медведе за 1995, 1996 и 2022 годы",
     "filters": {"taxon_id": TX["Ursus arctos"], "record_type": "publication", "doc_years": [1995, 1996, 2020], "sort": "obs_year", "order": "asc"},
     "created_at": TS},
    {"id": 4, "name": "Запрос 4: зацветание герани лесной",
     "text": "Создать список всех регистраций зацветания герани лесной в заповеднике \"Пасвик\" за 1995, 1996 и 2022 годы",
     "filters": {"taxon_id": TX["Geranium sylvaticum"], "phenophase_id": PL5, "doc_years": [1995, 1996, 2020], "sort": "date_from", "order": "asc"},
     "created_at": TS},
]
dump("saved_queries.json", saved)

# ---------------- quality ----------------
quality = {
    "generated_at": TS, "gold_version": "gold-placeholder", "extractor_version": EXV, "is_placeholder": True,
    "n_gold_records": 0, "n_extracted_records": len(obs),
    "record_level": {"field": "record", "precision": 0.0, "recall": 0.0, "f1": 0.0, "n": 0},
    "fields": [{"field": f, "precision": 0.0, "recall": 0.0, "f1": 0.0, "n": 0} for f in
               ["object_raw", "taxon_id", "date_from", "obs_year", "toponym_raw", "place_id", "habitat_raw",
                "event_raw", "phenophase_id", "value_num", "observers_raw", "printed_pages"]],
    "by_document": [{"document_id": i, "record_level": {"field": "record", "precision": 0.0, "recall": 0.0, "f1": 0.0, "n": 0}} for i in (1, 2, 3)],
    "error_examples": [
        {"kind": "wrong_value", "field": "date_from", "document_id": 3, "pdf_page": 76, "observation_id": o_edit["id"],
         "expected": "2020-06-23", "actual": "2020-06-13", "quote": q76, "comment": "placeholder: сдвиг столбца в таблице, где ячейки идут столбиком"},
        {"kind": "spurious", "field": "value_num", "document_id": 3, "pdf_page": 500, "observation_id": next(o["id"] for o in obs if o["value_num"] == 3.17),
         "expected": None, "actual": "3.17", "quote": None, "comment": "placeholder: значение не из цитаты (флаг value_not_in_quote)"},
        {"kind": "missed", "field": None, "document_id": 1, "pdf_page": 40, "observation_id": None,
         "expected": "Лисица, маршрут 1: 3.33 следов/10 км", "actual": None, "quote": "Лисица | 4 | 3.33 | 3 | 3.0", "comment": "placeholder"},
    ],
    "unsupported": [
        {"document_id": 2, "pdf_page": None, "reason": "no_text_layer", "comment": "placeholder: скан без текстового слоя, OCR ещё не выполнен"},
        {"document_id": 3, "pdf_page": 1, "reason": "image_only", "comment": "placeholder: обложка без текста"},
    ],
}
dump("quality.json", quality)
print("documents", len(documents), "taxa", len(taxa), "names", len(taxon_names), "places", len(places),
      "persons", len(persons_list), "phen", len(phenophases), "chunks", len(chunks), "obs", len(obs), "edits", len(edits))
