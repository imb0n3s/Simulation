#!/usr/bin/env python3
"""2500-player event, EVERYTHING Bo3. Field = real aggregated meta shares.
YOU pilot user_greninja with the spread-skilled DeepSearchAgent; the field is
heuristic. Cut: Top 64 -> 32 -> 16 -> 8 (re-seeded rounds), then a FIXED seeded
Top 8 bracket: QF 1v8 / 2v7 / 3v6 / 4v5, SF w(1v8) vs w(4v5) and w(2v7) vs
w(3v6), then the final. Resumable: re-run until it prints FINAL."""
import glob, os, json, random, time, collections
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent, DeepSearchAgent
from effects import Effects, SkilledPolicy

HERE = os.path.dirname(os.path.abspath(__file__)); CAT = load_catalog()
fx = Effects(SkilledPolicy())
agent = HeuristicAgent(fx)
you_agent = DeepSearchAgent(fx, depth=3, samples=3)
MAXT = 60; ROUNDS = 12; FIELD = 2500; BUDGET = 34
ST = os.path.join(HERE, "data", "event2500.json")

W = {
 "dragapult_dudunsparce_ex":3.32,"dragapult_dudunsparce_ex_v2":3.32,"dragapult_blaziken":3.32,
 "dragapult_ex":3.32,"dragapult_dusknoir":3.32,"dragapult_dusknoir_v2":3.32,"dragapult_dusknoir_v3":3.32,
 "dragapult_dudunsparce":3.32,"greninja_dragapult":3.32,"dragapult_toolbox":3.32,"dragapult_dusknoir_budew":3.32,
 "alakazam_dudunsparce":2.6,"alakazam_dudunsparce_v2":2.6,
 "mega_lucario":1.12,"mega_lucario_v2":1.12,"mega_lucario_v3":1.12,"mega_lucario_v4":1.12,
 "team_rockets_mewtwo":3.6,"ns_zoroark":3.5,"cynthia_garchomp":3.1,
 "mega_starmie_froslass":2.4,"team_rockets_porygon":2.4,"meganium_ogerpon":1.7,
 "slowking_box":0.85,"slowking_latias":0.85,"hydrapple_meganium":4.5,
 "mega_kangaskhan_crustle":1.6,"lillies_clefairy_box":1.2,"mega_diancie_dusknoir":0.4,
 "mega_feraligatr":0.3,"metagross_cinccino":0.3,
 "beedrill_ex_dudunsparce":0.5,"beedrill_dudunsparce_v2":0.5,
 "greninja":0.26,"greninja_dusknoir":0.25,"greninja_froslass":0.25,"user_greninja_dudunsparce":0.26,
 "mega_kangaskhan_box":0.7,"marnies_grimmsnarl":0.6,"tera_box":0.15,"tera_box_kangaskhan":0.15,
 "hops_trevenant":0.2,"ceruledge_ex":0.1,"mega_venusaur":0.1,
}
DECKS = {os.path.splitext(os.path.basename(p))[0]: [c.card_id for c in build_deck(p)]
         for p in sorted(glob.glob(os.path.join(HERE, "decks", "*.txt")))}

def play1(a, b, a_first, seed, a_you=False, b_you=False):
    pa = Player("A", [Card(c, CAT) for c in a]); pb = Player("B", [Card(c, CAT) for c in b])
    p1, p2 = (pa, pb) if a_first else (pb, pa)
    g = Game(p1, p2, seed=seed); g.log = lambda *x, **k: None; g.setup()
    ags = {id(pa): you_agent if a_you else agent, id(pb): you_agent if b_you else agent}
    while not g.winner and g.turn < MAXT:
        if not g.start_turn(): break
        ags[id(g.current)].take_turn(g, g.current); g.end_turn()
    return "A" if g.winner is pa else "B" if g.winner is pb else None

def match(rng, a, b):  # ALWAYS Bo3
    wa = wb = n = 0
    while wa < 2 and wb < 2 and n < 5:
        r = play1(a["cids"], b["cids"], n % 2 == 0, rng.randint(1, 10**9),
                  a.get("you", False), b.get("you", False)); n += 1
        if r == "A": wa += 1
        elif r == "B": wb += 1
    return a if wa >= wb else b

if os.path.exists(ST) and json.load(open(ST)).get("players"):
    st = json.load(open(ST))
else:
    rng = random.Random(25002500)
    pool = list(W); wts = [W[d] for d in pool]
    picks = rng.choices(pool, weights=wts, k=FIELD - 1)
    players = [{"id": i, "deck": d, "w": 0, "l": 0, "opp": []} for i, d in enumerate(picks)]
    players.append({"id": FIELD - 1, "deck": "user_greninja", "w": 0, "l": 0, "opp": [], "you": True})
    st = {"rounds_done": 0, "players": players, "phase": "swiss"}
players = st["players"]
for p in players: p["cids"] = DECKS[p["deck"]]

start = time.time()
rng = random.Random(909090 + st["rounds_done"])
while st["phase"] == "swiss" and st["rounds_done"] < ROUNDS and time.time() - start < BUDGET:
    order = sorted(players, key=lambda p: (-p["w"], p["l"], rng.random())); pool2 = order[:]; paired = set()
    while pool2:
        a = pool2.pop(0)
        if a["id"] in paired: continue
        opp = next((b for b in pool2 if b["id"] not in paired and b["id"] not in a["opp"]), None) or \
              next((b for b in pool2 if b["id"] not in paired), None)
        if opp is None: break
        paired.add(a["id"]); paired.add(opp["id"]); a["opp"].append(opp["id"]); opp["opp"].append(a["id"])
        win = match(rng, a, opp); lose = opp if win is a else a
        win["w"] += 1; lose["l"] += 1
    st["rounds_done"] += 1
    for p in players: p.pop("cids", None)
    json.dump(st, open(ST, "w"))
    for p in players: p["cids"] = DECKS[p["deck"]]
    print(f"swiss round {st['rounds_done']}/{ROUNDS} done")
if st["rounds_done"] < ROUNDS:
    print("RERUN to continue"); raise SystemExit

# ---- standings, cut, bracket (single shot once Swiss is finished) ----
wmap = {p["id"]: p["w"] / max(1, p["w"] + p["l"]) for p in players}
for p in players: p["res"] = sum(wmap[o] for o in p["opp"]) / max(1, len(p["opp"]))
standings = sorted(players, key=lambda p: (-p["w"], -p["res"], p["id"]))
for s, p in enumerate(standings, 1): p["seed"] = s
YOU = next(p for p in players if p.get("you"))
brng = random.Random(987654)
L = [f"2500-PLAYER EVENT — real meta shares, ALL matches Bo3 ({ROUNDS} Swiss + Top 64 cut)",
     f"YOUR deck: user_greninja, piloted by the spread-skilled DeepSearchAgent",
     f"Swiss record: {YOU['w']}-{YOU['l']}   Resistance: {100*YOU['res']:.1f}%   Swiss seed: {YOU['seed']}/{FIELD}", ""]
place = None
cur = standings[:64]
if YOU["seed"] > 64:
    place = YOU["seed"]
    L.append(f"Missed the Top 64 cut (cut line: {standings[63]['w']} wins).")
else:
    # re-seeded rounds down to Top 8
    out = None
    for rname, size in [("Top 64", 64), ("Top 32", 32), ("Top 16", 16)]:
        cur = sorted(cur, key=lambda p: p["seed"]); n = len(cur); winners = []; losers = []
        for i in range(n // 2):
            a, b = cur[i], cur[n - 1 - i]
            wv = match(brng, a, b); lv = b if wv is a else a
            winners.append(wv); losers.append(lv)
        L.append(f"{rname}: YOU {'ADVANCE' if YOU in winners else 'eliminated' if YOU in losers else '—'}")
        if YOU in losers: out = (rname, n // 2 + 1, losers); break
        cur = winners
    if out:
        rname, best, losers = out
        place = best + sorted(losers, key=lambda p: p["seed"]).index(YOU)
    else:
        # FIXED seeded Top 8: re-seed 1..8, QF 1v8/2v7/3v6/4v5, SF crosses, final
        cur = sorted(cur, key=lambda p: p["seed"])
        for s8, p in enumerate(cur, 1): p["s8"] = s8
        L.append("\n--- TOP 8 (re-seeded; fixed bracket) ---")
        qf_pairs = [(0, 7), (1, 6), (2, 5), (3, 4)]   # 1v8, 2v7, 3v6, 4v5
        qf_w = []
        for i, j in qf_pairs:
            a, b = cur[i], cur[j]; wv = match(brng, a, b)
            qf_w.append(wv)
            L.append(f"QF {a['s8']}v{b['s8']}: {a['deck']}({a['s8']}) vs {b['deck']}({b['s8']}) -> "
                     f"{wv['deck']}({wv['s8']}){' <== YOU' if wv.get('you') else ''}")
        sf1 = match(brng, qf_w[0], qf_w[3])   # w(1v8) vs w(4v5)
        sf2 = match(brng, qf_w[1], qf_w[2])   # w(2v7) vs w(3v6)
        L.append(f"SF: {qf_w[0]['deck']}({qf_w[0]['s8']}) vs {qf_w[3]['deck']}({qf_w[3]['s8']}) -> {sf1['deck']}({sf1['s8']})")
        L.append(f"SF: {qf_w[1]['deck']}({qf_w[1]['s8']}) vs {qf_w[2]['deck']}({qf_w[2]['s8']}) -> {sf2['deck']}({sf2['s8']})")
        champ = match(brng, sf1, sf2)
        L.append(f"FINAL: {sf1['deck']}({sf1['s8']}) vs {sf2['deck']}({sf2['s8']}) -> CHAMPION {champ['deck']}({champ['s8']})")
        if YOU is champ: place = 1
        elif YOU in (sf1, sf2): place = 2
        elif YOU in qf_w: place = 3
        else: place = 5
L.append(f"\nFINAL PLACEMENT: {place}")
L.append("\nTop 10 Swiss standings:")
for p in standings[:10]:
    L.append(f"  {p['seed']:>3}. {p['w']}-{p['l']}  res {100*p['res']:.0f}%  {p['deck']}{' <== YOU' if p.get('you') else ''}")
cl = standings[63]
L.append(f"\nCut line: seed 64 at {cl['w']}-{cl['l']} (res {100*cl['res']:.1f}%)")
dist = collections.Counter(p["deck"] for p in players)
L.append(f"field: {dist.most_common(6)}")
open(os.path.join(HERE, "data", "event2500_result.txt"), "w").write("\n".join(L))
print("\n".join(L)); print("FINAL")
