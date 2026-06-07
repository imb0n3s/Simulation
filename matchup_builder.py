#!/usr/bin/env python3
"""Builds a game-level matchup table with real simulations. Resumable.
- field vs field: 25 games each side going first (heuristic skilled both sides)
- user_greninja vs field: 50 games each side going first, YOU piloted by the
  spread-skilled DeepSearchAgent, opponent by the skilled heuristic
- mirrors: 20+20
Stores per ordered (a_first) results: data/matchup_table.json"""
import os, json, glob, random, time
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent, DeepSearchAgent
from effects import Effects, SkilledPolicy
HERE=os.path.dirname(os.path.abspath(__file__)); CAT=load_catalog()
MAXT=60; BUDGET=38
TAB=os.path.join(HERE,"data","matchup_table.json")
fx=Effects(SkilledPolicy())
heur=HeuristicAgent(fx)
deep=DeepSearchAgent(fx, depth=3, samples=2)
FIELD=["dragapult_dudunsparce_ex","dragapult_dudunsparce_ex_v2","dragapult_blaziken","dragapult_ex",
 "dragapult_dusknoir","dragapult_dusknoir_v2","dragapult_dudunsparce","greninja_dragapult",
 "alakazam_dudunsparce","alakazam_dudunsparce_v2","mega_lucario","mega_lucario_v2",
 "team_rockets_mewtwo","ns_zoroark","cynthia_garchomp","mega_starmie_froslass","meganium_ogerpon",
 "slowking_box","mega_kangaskhan_crustle","lillies_clefairy_box","greninja","greninja_dusknoir",
 "greninja_froslass","mega_kangaskhan_box","marnies_grimmsnarl","tera_box","tera_box_kangaskhan",
 "hops_trevenant","ceruledge_ex","mega_venusaur","team_rockets_porygon"]
USER="user_greninja"
DECKS={d:[c.card_id for c in build_deck(os.path.join(HERE,"decks",d+".txt"))] for d in FIELD+[USER]}

def game(a,b,agA,agB,seed):
    pa=Player("A",[Card(c,CAT) for c in DECKS[a]]); pb=Player("B",[Card(c,CAT) for c in DECKS[b]])
    g=Game(pa,pb,seed=seed); g.log=lambda *x,**k:None; g.setup()
    ags={id(pa):agA,id(pb):agB}
    while not g.winner and g.turn<MAXT:
        if not g.start_turn(): break
        ags[id(g.current)].take_turn(g,g.current); g.end_turn()
    return "A" if g.winner is pa else "B" if g.winner is pb else "D"

tab=json.load(open(TAB)) if os.path.exists(TAB) else {}
jobs=[]
for i,a in enumerate(FIELD):
    for b in FIELD[i:]:
        n = 20 if a==b else 25
        jobs.append((a,b,n,"hh"))
for b in FIELD:
    jobs.append((USER,b,50,"dh"))
rng=random.Random(99)
start=time.time(); done_now=0
for a,b,n,mode in jobs:
    key=f"{a}|{b}"
    if key in tab: continue
    if time.time()-start>BUDGET: break
    agA = deep if mode=="dh" else heur
    res={"af":{"w":0,"l":0,"d":0},"bf":{"w":0,"l":0,"d":0},"n":n}
    for k in range(n):
        r=game(a,b,agA,heur,rng.randint(1,10**9))
        res["af"]["w" if r=="A" else "l" if r=="B" else "d"]+=1
        # b goes first: swap construction order by playing (b,a) and inverting
        r2=game(b,a,heur,agA,rng.randint(1,10**9))
        res["bf"]["w" if r2=="B" else "l" if r2=="A" else "d"]+=1
    tab[key]=res; done_now+=1
    if done_now%25==0: json.dump(tab,open(TAB,"w"))
json.dump(tab,open(TAB,"w"))
total=len([0 for i,a in enumerate(FIELD) for b in FIELD[i:]])+len(FIELD)
print(f"pairs done: {len(tab)}/{total}")
if len(tab)>=total: print("TABLE COMPLETE")
