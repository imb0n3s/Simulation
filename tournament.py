#!/usr/bin/env python3
"""Resumable tournament: each deck plays N games vs random opponents.
Re-run until it prints ALL DONE; checkpoints to data/tournament_results.json."""
import glob, os, json, random, sys, time
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent
from effects import Effects

HERE = os.path.dirname(os.path.abspath(__file__))
CAT = load_catalog()
N = 1000
MAX_TURNS = 60
BUDGET = 38  # seconds per invocation
RES = os.path.join(HERE, "data", "tournament_results_skilled.json")

deck_paths = sorted(glob.glob(os.path.join(HERE, "decks", "*.txt")))
DECKS = {os.path.splitext(os.path.basename(p))[0]: [c.card_id for c in build_deck(p)]
         for p in deck_paths}
names = list(DECKS)

# resume
if os.path.exists(RES):
    state = json.load(open(RES))
    results = state["results"]
else:
    results = {}
for n in names:
    results.setdefault(n, {"wins": 0, "losses": 0, "draws": 0})

def total_games(r): return r["wins"] + r["losses"] + r["draws"]

from effects import SkilledPolicy
fx = Effects(SkilledPolicy()); agent = HeuristicAgent(fx)
def play(cids_a, cids_b, seed, a_first):
    pa = Player("A", [Card(c, CAT) for c in cids_a])
    pb = Player("B", [Card(c, CAT) for c in cids_b])
    p1, p2 = (pa, pb) if a_first else (pb, pa)
    g = Game(p1, p2, seed=seed); g.log = lambda *a, **k: None
    g.setup()
    while not g.winner and g.turn < MAX_TURNS:
        if not g.start_turn(): break
        agent.take_turn(g, g.current); g.end_turn()
    return "A" if g.winner is pa else "B" if g.winner is pb else None

rng = random.Random(2024)
start = time.time()
todo = [n for n in names if total_games(results[n]) < N]
for name in todo:
    if time.time() - start > BUDGET:
        break
    others = [o for o in names if o != name]
    r = results[name]
    for k in range(N):
        opp = rng.choice(others)
        res = play(DECKS[name], DECKS[opp], seed=rng.randint(1, 10**9), a_first=(k % 2 == 0))
        if res == "A": r["wins"] += 1
        elif res == "B": r["losses"] += 1
        else: r["draws"] += 1
    json.dump({"N": N, "results": results}, open(RES, "w"), indent=2)

done = sum(1 for n in names if total_games(results[n]) >= N)
print(f"decks complete: {done}/{len(names)}")
if done == len(names):
    rows = sorted(results.items(), key=lambda kv: kv[1]["wins"], reverse=True)
    L = [f"Tournament — each deck played {N} games vs random opponents (max {MAX_TURNS} turns)\n",
         f"{'Rank':<5}{'Deck':<30}{'W':>6}{'L':>6}{'D':>6}{'Win%':>8}"]
    for rk,(n,r) in enumerate(rows,1):
        g=total_games(r); L.append(f"{rk:<5}{n:<30}{r['wins']:>6}{r['losses']:>6}{r['draws']:>6}{100*r['wins']/g:>7.1f}%")
    open(os.path.join(HERE,"data","tournament_standings_skilled.txt"),"w").write("\n".join(L))
    print("ALL DONE")
