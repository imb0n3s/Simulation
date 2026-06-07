#!/usr/bin/env python3
"""Tournament with a field weighted to the REAL Indianapolis 559 metagame shares.
Archetypes that weren't present at 559 (Beedrill, Mega Venusaur, Ceruledge, Hop's)
are excluded. Archetypes in the real meta that I have no deck for (Raging Bolt,
Hydrapple, Festival Lead, Mega Lopunny, etc.) are simply absent — a known ~27% gap."""
import glob, os, random, collections
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent
from effects import Effects, SkilledPolicy

HERE=os.path.dirname(os.path.abspath(__file__)); CAT=load_catalog()
fx=Effects(SkilledPolicy()); agent=HeuristicAgent(fx)
rng=random.Random(559)
MAXT=60; FIELD=360

# deck-file -> real 559 share (%). Dragapult split across its variants; proxies noted.
W = {  # aggregated share (%) across Utrecht(535)+Campinas(544)+Indianapolis(559), 1071 decks
 "dragapult_dudunsparce_ex":4.56,"dragapult_dudunsparce_ex_v2":4.56,"dragapult_blaziken":4.56,
 "dragapult_ex":4.56,"dragapult_dusknoir":4.56,"dragapult_dusknoir_v2":4.56,
 "dragapult_dudunsparce":4.56,"greninja_dragapult":4.56,                    # Dragapult 36.5%
 "alakazam_dudunsparce":2.6,"alakazam_dudunsparce_v2":2.6,                   # Alakazam 5.2%
 "mega_lucario":2.25,"mega_lucario_v2":2.25,                                # Mega Lucario 4.5%
 "team_rockets_mewtwo":3.6,"ns_zoroark":3.5,"cynthia_garchomp":3.1,
 "mega_starmie_froslass":2.4,"meganium_ogerpon":1.7,"slowking_box":1.7,
 "mega_kangaskhan_crustle":1.6,                                             # ~Crustle
 "lillies_clefairy_box":1.2,"greninja":0.34,"greninja_dusknoir":0.33,"greninja_froslass":0.33,
 "mega_kangaskhan_box":0.7,"marnies_grimmsnarl":0.6,"tera_box":0.15,"tera_box_kangaskhan":0.15,
 "hops_trevenant":0.2,"ceruledge_ex":0.1,"mega_venusaur":0.1,
 # NOTE missing ~30% of the real field (no deck): Raging Bolt 8.8, Mega Lopunny 6.0,
 # Festival Lead 4.6, Hydrapple 4.5, Ogerpon Box 3.2, Rocket's Honchkrow 2.4, Okidogi, etc.
}
ARCH = {  # group variants for reporting
 **{d:"Dragapult" for d in ["dragapult_dudunsparce_ex","dragapult_dudunsparce_ex_v2","dragapult_blaziken","dragapult_ex","dragapult_dusknoir","dragapult_dusknoir_v2","dragapult_dudunsparce","greninja_dragapult"]},
 "meganium_ogerpon":"Ogerpon/Meganium","alakazam_dudunsparce":"Alakazam","alakazam_dudunsparce_v2":"Alakazam",
 "mega_lucario":"Mega Lucario","mega_lucario_v2":"Mega Lucario","slowking_box":"Slowking",
 "cynthia_garchomp":"Cynthia's Garchomp","ns_zoroark":"N's Zoroark","team_rockets_mewtwo":"Rocket's Mewtwo",
 "mega_kangaskhan_crustle":"Crustle/Kangaskhan","mega_kangaskhan_box":"Mega Kangaskhan",
 "mega_starmie_froslass":"Mega Starmie","greninja":"Greninja","greninja_dusknoir":"Greninja","greninja_froslass":"Greninja",
 "lillies_clefairy_box":"Lillie's Clefairy","marnies_grimmsnarl":"Marnie's Grimmsnarl",
 "tera_box":"Tera Box","tera_box_kangaskhan":"Tera Box",
}
DECKS={os.path.splitext(os.path.basename(p))[0]:[c.card_id for c in build_deck(p)]
       for p in sorted(glob.glob(os.path.join(HERE,"decks","*.txt")))}
pool=[d for d in W]; weights=[W[d] for d in pool]

def play1(a,b,a_first,seed):
    pa=Player("A",[Card(c,CAT) for c in a]); pb=Player("B",[Card(c,CAT) for c in b])
    p1,p2=(pa,pb) if a_first else (pb,pa)
    g=Game(p1,p2,seed=seed); g.log=lambda *x,**k:None; g.setup()
    while not g.winner and g.turn<MAXT:
        if not g.start_turn(): break
        agent.take_turn(g,g.current); g.end_turn()
    return "A" if g.winner is pa else "B" if g.winner is pb else None
def match(a,b,bo=3):
    need=bo//2+1; wa=wb=n=0
    while wa<need and wb<need and n<bo+2:
        r=play1(a["cids"],b["cids"],n%2==0,rng.randint(1,10**9)); n+=1
        if r=="A": wa+=1
        elif r=="B": wb+=1
    return a if wa>=wb else b

# sample field by weight
players=[]
chosen=rng.choices(pool,weights=weights,k=FIELD)
for i,name in enumerate(chosen):
    players.append({"id":i,"deck":name,"arch":ARCH.get(name,name),"cids":DECKS[name],"w":0,"l":0,"opp":[]})

# per-archetype W/L over all swiss games
arch_wl=collections.defaultdict(lambda:[0,0])
ROUNDS=9
for rd in range(ROUNDS):
    order=sorted(players,key=lambda p:(-p["w"],p["l"],rng.random())); pool2=order[:]; paired=set()
    while pool2:
        a=pool2.pop(0)
        if a["id"] in paired: continue
        opp=next((b for b in pool2 if b["id"] not in paired and b["id"] not in a["opp"]),None) or \
            next((b for b in pool2 if b["id"] not in paired),None)
        if opp is None: break
        paired.add(a["id"]); paired.add(opp["id"]); a["opp"].append(opp["id"]); opp["opp"].append(a["id"])
        w=match(a,opp,bo=3); l=opp if w is a else a
        w["w"]+=1; l["l"]+=1
        arch_wl[w["arch"]][0]+=1; arch_wl[l["arch"]][1]+=1

wmap={p["id"]:p["w"]/max(1,p["w"]+p["l"]) for p in players}
for p in players: p["res"]=sum(wmap[o] for o in p["opp"])/max(1,len(p["opp"]))
standings=sorted(players,key=lambda p:(-p["w"],-p["res"],rng.random()))
for s,p in enumerate(standings,1): p["seed"]=s
def comp(group): return ", ".join(f"{d} x{n}" for d,n in collections.Counter(p["arch"] for p in group).most_common())

top64=standings[:64]
def rnd(field):
    n=len(field); return [match(field[i],field[n-1-i],bo=3) for i in range(n//2)]
r32=rnd(top64); r16=rnd(sorted(r32,key=lambda p:p['seed'])); r8=rnd(sorted(r16,key=lambda p:p['seed']))
r4=rnd(sorted(r8,key=lambda p:p['seed'])); r2=rnd(sorted(r4,key=lambda p:p['seed'])); champ=rnd(sorted(r2,key=lambda p:p['seed']))[0]

L=[]
L.append(f"WEIGHTED to real Indianapolis 559 metagame — {FIELD} pilots, 9 Swiss (Bo3) + Top64 cut (Bo3)\n")
L.append("Field entered (by archetype):")
for d,n in collections.Counter(p['arch'] for p in players).most_common():
    L.append(f"  {d:<24}{n:>4}  ({100*n/FIELD:.1f}%)")
L.append("\nArchetype win% across all Swiss games (skilled Bo3):")
for a,(w,l) in sorted(arch_wl.items(),key=lambda kv:-kv[1][0]/max(1,sum(kv[1]))):
    g=w+l; L.append(f"  {a:<24}{w:>4}-{l:<4}{100*w/max(1,g):>6.1f}%")
L.append(f"\nTOP 64 composition: {comp(top64)}")
L.append(f"TOP 32 composition: {comp(r32)}")
L.append(f"TOP 8 composition:  {comp(r8)}")
L.append(f"Finalists: {comp(r2)}")
L.append(f"\n*** CHAMPION: {champ['arch']} ({champ['deck']}, Swiss {champ['w']}-{champ['l']}, seed {champ['seed']}) ***")
open(os.path.join(HERE,"data","tournament_559weighted.txt"),"w").write("\n".join(L))
print("\n".join(L))
