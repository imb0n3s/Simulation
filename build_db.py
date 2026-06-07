"""
Build the SQLite card database from cards.json and ingest deck lists.

- Parses PTCG Live export format (e.g. "4 Abra MEG 54").
- Creates tables: cards, decks, deck_cards.
- Stores full structured card data (abilities/attacks/effects as JSON) so the
  database is the queryable record of everything the simulator has "learned".

Usage:
    python build_db.py                      # builds data/cards.db, ingests all decks/*.txt
    python build_db.py decks/mydeck.txt
"""
import json, os, re, sqlite3, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
FINAL_DB = os.path.join(HERE, "data", "cards.db")
DB = "/tmp/_cards_build.db"  # build locally; many mounts reject sqlite locking
CATALOG = json.load(open(os.path.join(HERE, "cards.json"), encoding="utf-8"))

CATEGORY_HEADERS = {"Pokémon": "Pokémon", "Pokemon": "Pokémon",
                    "Trainer": "Trainer", "Energy": "Energy"}

LINE_RE = re.compile(r"^(\d+)\s+(.*?)\s+([A-Z][A-Z-]{1,5})\s+(\w+)$")

# PTCG Live prints some promo set codes differently than the Limitless catalog ids.
SET_ALIASES = {"PR-SV": "SVP"}

def parse_decklist(path):
    """Return (deck_name, [(count, name, set, number, category)])."""
    name = os.path.splitext(os.path.basename(path))[0]
    rows, category = [], None
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line: continue
        head = line.split(":")[0].strip()
        if head in CATEGORY_HEADERS and ":" in line:
            category = CATEGORY_HEADERS[head]; continue
        m = LINE_RE.match(line)
        if not m:
            print(f"  ! could not parse: {line}"); continue
        count, cname, setcode, number = m.groups()
        setcode = SET_ALIASES.get(setcode, setcode)
        rows.append((int(count), cname, setcode, number, category))
    return name, rows

def card_id(setcode, number): return f"{setcode}-{number}"

def init_db(con):
    con.executescript("""
    DROP TABLE IF EXISTS cards;
    DROP TABLE IF EXISTS decks;
    DROP TABLE IF EXISTS deck_cards;
    CREATE TABLE cards (
        card_id TEXT PRIMARY KEY, name TEXT, set_code TEXT, number TEXT,
        supertype TEXT, subtypes TEXT, types TEXT, hp INTEGER,
        evolves_from TEXT, retreat INTEGER, weakness TEXT, resistance TEXT,
        rule_box INTEGER, regulation_mark TEXT, rules_text TEXT,
        abilities TEXT, attacks TEXT, effects TEXT, provides TEXT
    );
    CREATE TABLE decks (deck_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
                        pokemon_ct INTEGER, trainer_ct INTEGER, energy_ct INTEGER, total INTEGER);
    CREATE TABLE deck_cards (deck_id INTEGER, card_id TEXT, count INTEGER, category TEXT,
                             FOREIGN KEY(deck_id) REFERENCES decks(deck_id));
    """)

def upsert_card(con, cid):
    d = CATALOG[cid]
    con.execute("""INSERT OR REPLACE INTO cards VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        cid, d["name"], d["set"], d["number"], d["supertype"],
        json.dumps(d.get("subtypes", [])), json.dumps(d.get("types", [])),
        d.get("hp"), d.get("evolves_from"), d.get("retreat"),
        d.get("weakness"), d.get("resistance"), int(d.get("rule_box", False)),
        d.get("regulation_mark"), d.get("text", ""),
        json.dumps(d.get("abilities", [])), json.dumps(d.get("attacks", [])),
        json.dumps(d.get("effects") or d.get("on_attach") or []),
        json.dumps(d.get("provides", []))))

def ingest_deck(con, path):
    name, rows = parse_decklist(path)
    print(f"Ingesting deck '{name}' ({len(rows)} lines)")
    counts = {"Pokémon": 0, "Trainer": 0, "Energy": 0}
    missing = []
    for count, cname, setcode, number, cat in rows:
        cid = card_id(setcode, number)
        if cid not in CATALOG:
            missing.append(f"{cname} {cid}"); continue
        upsert_card(con, cid)
        counts[cat] = counts.get(cat, 0) + count
    cur = con.execute("INSERT OR REPLACE INTO decks(name,pokemon_ct,trainer_ct,energy_ct,total) VALUES (?,?,?,?,?)",
                      (name, counts["Pokémon"], counts["Trainer"], counts["Energy"], sum(counts.values())))
    deck_id = cur.lastrowid
    con.execute("DELETE FROM deck_cards WHERE deck_id=?", (deck_id,))
    for count, cname, setcode, number, cat in rows:
        cid = card_id(setcode, number)
        if cid in CATALOG:
            con.execute("INSERT INTO deck_cards VALUES (?,?,?,?)", (deck_id, cid, count, cat))
    if missing:
        print("  ! cards not in catalog:", missing)
    print(f"  totals -> Pokémon {counts['Pokémon']}, Trainer {counts['Trainer']}, "
          f"Energy {counts['Energy']}, TOTAL {sum(counts.values())}")
    return name, counts

def main():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB); init_db(con)
    # ensure every catalog card is stored even if not in a deck
    for cid in CATALOG: upsert_card(con, cid)
    paths = sys.argv[1:] or sorted(glob.glob(os.path.join(HERE, "decks", "*.txt")))
    for p in paths: ingest_deck(con, p)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    con.close()
    import shutil; os.makedirs(os.path.dirname(FINAL_DB), exist_ok=True)
    shutil.copy(DB, FINAL_DB)
    print(f"\nDatabase written: {FINAL_DB}  ({n} cards catalogued)")

if __name__ == "__main__":
    main()
