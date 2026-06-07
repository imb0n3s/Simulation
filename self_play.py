#!/usr/bin/env python3
"""Self-play loop. Resumable: re-run until games_done reaches TARGET.
Logs per-deck how-it-wins profile (prizes / deckout / no-Pokemon / timeout) and
samples board-state features (labelled by eventual winner) for value training."""
import glob, os, json, random, time, csv
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent
from effects import Effects, SkilledPolicy

HERE=os.path.dirname(os.path.abspath(__file__)); CAT=load_catalog()
TARGET=10000; BUDGET=40; MAXT=60
STATS=os.path.join(HERE,"data","selfplay_stats.json")
SAMP =os.path.join(HERE,"data","value_samples.csv")
fx=Effects(SkilledPolicy()); agent=HeuristicAgent(fx)
DECKS={os.path.splitext(os.path.basename(p))[0]:[c.card_id for c in build_deck(p)]
       for p in sorted(glob.glob(os.path.join(HERE,"decks","*.txt")))}
names=list(DECKS)

if os.path.exists(STATS):
    st=json.load(open(STATS))
else:
    st={"games_done":0,"decks":{n:{"games":0,"wins":0,"losses":0,"draws":0,
        "win_prizes":0,"win_deckout":0,"win_nopoke":0,"turns_sum":0} for n in names}}
D=st["decks"]
for n in names: D.setdefault(n,{"games":0,"wins":0,"losses":0,"draws":0,"win_prizes":0,"win_deckout":0,"win_nopoke":0,"turns_sum":0})

def feats(p, opp, turn):
    me_e=sum(len(m.energy) for m in p.all_pokemon()); op_e=sum(len(m.energy) for m in opp.all_pokemon())
    me_d=sum(m.damage for m in p.all_pokemon()); op_d=sum(m.damage for m in opp.all_pokemon())
    return [1.0, len(p.prizes), len(opp.prizes), len(p.prizes)-len(opp.prizes),
            me_d/100.0, op_d/100.0, len(p.bench), len(opp.bench), me_e, op_e, turn/10.0]

rng=random.Random(1234 + st["games_done"])
samp_f=open(SAMP,"a",newline=""); sw=csv.writer(samp_f)
start=time.time(); did=0
while st["games_done"]<TARGET and time.time()-start<BUDGET:
    a,b=rng.sample(names,2)
    pa=Player("A",[Card(c,CAT) for c in DECKS[a]]); pb=Player("B",[Card(c,CAT) for c in DECKS[b]])
    g=Game(pa,pb,seed=rng.randint(1,10**9)); g.log=lambda *x,**k:None; g.setup()
    snaps=[]
    while not g.winner and g.turn<MAXT:
        if not g.start_turn(): break
        cur=g.current; opp=g.players[1-g.players.index(cur)]
        agent.take_turn(g,cur); g.end_turn()
        if rng.random()<0.25:   # sample ~quarter of turns
            who = a if cur is pa else b
            snaps.append((who, cur is pa, feats(cur,opp,g.turn)))
    # outcome
    for nm in (a,b): D[nm]["games"]+=1; D[nm]["turns_sum"]+=g.turn
    if g.winner is pa or g.winner is pb:
        wdeck = a if g.winner is pa else b; ldeck = b if g.winner is pa else a
        D[wdeck]["wins"]+=1; D[ldeck]["losses"]+=1
        r=g.win_reason
        key="win_prizes" if "prize" in r else "win_deckout" if "draw" in r else "win_nopoke" if "no Pokemon" in r else "win_prizes"
        D[wdeck][key]+=1
        win_is_a = g.winner is pa
        for who,is_a,fv in snaps:
            label=1 if (is_a==win_is_a) else 0
            sw.writerow(fv+[label])
    else:
        D[a]["draws"]+=1; D[b]["draws"]+=1
    st["games_done"]+=1; did+=1
samp_f.close()
json.dump(st,open(STATS,"w"))
print(f"ran {did} games this call; total {st['games_done']}/{TARGET}")
