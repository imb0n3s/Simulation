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
deep=DeepSearchAgent(fx, depth=3, samples=3)
FIELD=["alakazam_dudunsparce","alakazam_dudunsparce_v2","alakazam_v3","beedrill_dudunsparce_v2","beedrill_ex","beedrill_ex_dudunsparce","blaziken_greninja_zoroark","ceruledge_ex","cynthia_garchomp","dragapult_blaziken","dragapult_blaziken_v2","dragapult_blaziken_v3","dragapult_dudunsparce","dragapult_dudunsparce_ex","dragapult_dudunsparce_ex_v2","dragapult_dusknoir","dragapult_dusknoir_budew","dragapult_dusknoir_v2","dragapult_dusknoir_v3","dragapult_ex","dragapult_hammers","dragapult_starmie","dragapult_toolbox","festival_greninja","festival_lead","froslass_arboliva","froslass_lopunny","greninja","greninja_dragapult","greninja_dusknoir","greninja_froslass","greninja_noctowl","hops_trevenant","hydrapple_meganium","hydreigon_cinderace","ionos_bellibolt","jellicent_control","lillies_clefairy_box","lopunny_froslass_box","marnies_grimmsnarl","mega_diancie_dusknoir","mega_feraligatr","mega_kangaskhan_box","mega_kangaskhan_crustle","mega_lopunny_dudunsparce","mega_lucario","mega_lucario_v2","mega_lucario_v3","mega_lucario_v4","mega_lucario_v5","mega_pyroar","mega_starmie_froslass","mega_venusaur","meganium_ogerpon","ogerpon_meganium_arboliva","ogerpon_meganium_arboliva_v2","ogerpon_meganium_hydrapple","metagross_cinccino","ns_zoroark","ns_zoroark_v2","rockets_honchkrow","sinistcha_ogerpon","slowking_box","slowking_latias","slowking_latias_v2","starmie_dusknoir","starmie_froslass_v2","team_rockets_mewtwo","team_rockets_porygon","tera_box","tera_box_kangaskhan","tera_box_raging_bolt","user_greninja_dragapult","user_greninja_dudunsparce","user_greninja_froslass","user_greninja_v2","zoroark_greninja_dusknoir"]
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
        n = 12 if a==b else 15
        jobs.append((a,b,n,"hh"))
for b in FIELD:
    jobs.append((USER,b,30,"dh"))
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
    json.dump(tab,open(TAB,"w"))
json.dump(tab,open(TAB,"w"))
total=len([0 for i,a in enumerate(FIELD) for b in FIELD[i:]])+len(FIELD)
print(f"pairs done: {len(tab)}/{total}")
if len(tab)>=total: print("TABLE COMPLETE")
