#!/usr/bin/env python3
"""Planner-piloted rows for starmie_froslass_munkidori vs every field deck (n=30/side)."""
import os, json, glob, random, time
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent, DeepSearchAgent
from effects import Effects, SkilledPolicy
HERE=os.path.dirname(os.path.abspath(__file__)); CAT=load_catalog()
fx=Effects(SkilledPolicy()); H=HeuristicAgent(fx); D=DeepSearchAgent(fx)
ME="starmie_froslass_munkidori"
TAB=os.path.join(HERE,"data","matchup_table.json")
tab=json.load(open(TAB))
U=[c.card_id for c in build_deck(f"decks/{ME}.txt")]
FIELD=sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob("decks/*.txt"))
FIELD=[d for d in FIELD if d not in (ME,)]
def play(a,b,first,seed):
    pa=Player("A",[Card(c,CAT) for c in a]); pb=Player("B",[Card(c,CAT) for c in b])
    p1,p2=(pa,pb) if first else (pb,pa)
    g=Game(p1,p2,seed=seed); g.log=lambda *x,**k:None; g.setup()
    ags={id(pa):D,id(pb):H}
    while not g.winner and g.turn<60:
        if not g.start_turn(): break
        ags[id(g.current)].take_turn(g,g.current); g.end_turn()
    return "A" if g.winner is pa else "B" if g.winner is pb else None
start=time.time(); done=0
for d in FIELD:
    k=f"{ME}|{d}"
    if k in tab and tab[k].get("n")==30: done+=1; continue
    if time.time()-start>36: print(f"rows done: {done}/{len(FIELD)} — RERUN"); raise SystemExit
    B=[c.card_id for c in build_deck(f"decks/{d}.txt")]
    row={"af":{"w":0,"l":0,"d":0},"bf":{"w":0,"l":0,"d":0},"n":30}
    for i in range(30):
        r=play(U,B,True,5000+i); row["af"]["w" if r=="A" else "l" if r=="B" else "d"]+=1
        r=play(U,B,False,6000+i); row["bf"]["w" if r=="A" else "l" if r=="B" else "d"]+=1
    tab[k]=row; json.dump(tab,open(TAB,"w")); done+=1
print("ROWS COMPLETE")
