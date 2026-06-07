#!/usr/bin/env python3
"""800-player event, EVERYTHING Bo3 (Swiss + cut). Field = real aggregated meta
shares (Utrecht+Campinas+Indy) over my available decks, plus 1 pilot 'YOU' on the
user's Greninja list. Resumable: re-run until it prints FINAL."""
import glob, os, json, random, time, collections
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent
from effects import Effects, SkilledPolicy

HERE=os.path.dirname(os.path.abspath(__file__)); CAT=load_catalog()
fx=Effects(SkilledPolicy()); agent=HeuristicAgent(fx)
MAXT=60; ROUNDS=9; FIELD=800; BUDGET=34
ST=os.path.join(HERE,"data","event800.json")
W = {
 "dragapult_dudunsparce_ex":4.56,"dragapult_dudunsparce_ex_v2":4.56,"dragapult_blaziken":4.56,
 "dragapult_ex":4.56,"dragapult_dusknoir":4.56,"dragapult_dusknoir_v2":4.56,
 "dragapult_dudunsparce":4.56,"greninja_dragapult":4.56,
 "alakazam_dudunsparce":2.6,"alakazam_dudunsparce_v2":2.6,
 "mega_lucario":2.25,"mega_lucario_v2":2.25,
 "team_rockets_mewtwo":3.6,"ns_zoroark":3.5,"cynthia_garchomp":3.1,
 "mega_starmie_froslass":2.4,"meganium_ogerpon":1.7,"slowking_box":1.7,
 "mega_kangaskhan_crustle":1.6,"lillies_clefairy_box":1.2,
 "greninja":0.34,"greninja_dusknoir":0.33,"greninja_froslass":0.33,
 "mega_kangaskhan_box":0.7,"marnies_grimmsnarl":0.6,"tera_box":0.15,"tera_box_kangaskhan":0.15,
 "hops_trevenant":0.2,"ceruledge_ex":0.1,"mega_venusaur":0.1,
}
DECKS={os.path.splitext(os.path.basename(p))[0]:[c.card_id for c in build_deck(p)]
       for p in sorted(glob.glob(os.path.join(HERE,"decks","*.txt")))}

def play1(a,b,a_first,seed):
    pa=Player("A",[Card(c,CAT) for c in a]); pb=Player("B",[Card(c,CAT) for c in b])
    p1,p2=(pa,pb) if a_first else (pb,pa)
    g=Game(p1,p2,seed=seed); g.log=lambda *x,**k:None; g.setup()
    while not g.winner and g.turn<MAXT:
        if not g.start_turn(): break
        agent.take_turn(g,g.current); g.end_turn()
    return "A" if g.winner is pa else "B" if g.winner is pb else None
def match(rng,a,b):  # ALWAYS Bo3
    wa=wb=n=0
    while wa<2 and wb<2 and n<5:
        r=play1(a["cids"],b["cids"],n%2==0,rng.randint(1,10**9)); n+=1
        if r=="A": wa+=1
        elif r=="B": wb+=1
    return a if wa>=wb else b

if os.path.exists(ST):
    st=json.load(open(ST))
else:
    rng=random.Random(800800)
    pool=list(W); wts=[W[d] for d in pool]
    picks=rng.choices(pool,weights=wts,k=FIELD-1)
    players=[{"id":i,"deck":d,"w":0,"l":0,"opp":[]} for i,d in enumerate(picks)]
    players.append({"id":FIELD-1,"deck":"user_greninja","w":0,"l":0,"opp":[],"you":True})
    st={"rounds_done":0,"players":players,"phase":"swiss"}
players=st["players"]
for p in players: p["cids"]=DECKS[p["deck"]]

start=time.time()
rng=random.Random(424242+st["rounds_done"])
while st["phase"]=="swiss" and st["rounds_done"]<ROUNDS and time.time()-start<BUDGET:
    order=sorted(players,key=lambda p:(-p["w"],p["l"],rng.random())); pool2=order[:]; paired=set()
    while pool2:
        a=pool2.pop(0)
        if a["id"] in paired: continue
        opp=next((b for b in pool2 if b["id"] not in paired and b["id"] not in a["opp"]),None) or \
            next((b for b in pool2 if b["id"] not in paired),None)
        if opp is None: break
        paired.add(a["id"]); paired.add(opp["id"]); a["opp"].append(opp["id"]); opp["opp"].append(a["id"])
        win=match(rng,a,opp); lose=opp if win is a else a
        win["w"]+=1; lose["l"]+=1
    st["rounds_done"]+=1
    for p in players: p.pop("cids",None)
    json.dump(st,open(ST,"w"))
    for p in players: p["cids"]=DECKS[p["deck"]]
    print(f"swiss round {st['rounds_done']}/{ROUNDS} done")
if st["rounds_done"]<ROUNDS:
    print("RERUN to continue"); raise SystemExit

# standings + cut (only once)
wmap={p["id"]:p["w"]/max(1,p["w"]+p["l"]) for p in players}
for p in players: p["res"]=sum(wmap[o] for o in p["opp"])/max(1,len(p["opp"]))
standings=sorted(players,key=lambda p:(-p["w"],-p["res"],p["id"]))
for s,p in enumerate(standings,1): p["seed"]=s
YOU=next(p for p in players if p.get("you"))
top=standings[:64]
place=None
if YOU["seed"]>64:
    place=YOU["seed"]
else:
    field=top; rng=random.Random(999)
    bands=[(64,33),(32,17),(16,9),(8,5),(4,3),(2,2)]
    rnd_names=["Top 64","Top 32","Top 16","Quarterfinals","Semifinals","Finals"]
    cur=field; out_round=None
    for bi,(size,_) in enumerate(bands):
        cur=sorted(cur,key=lambda p:p["seed"])
        n=len(cur); winners=[]; losers=[]
        for i in range(n//2):
            a,b=cur[i],cur[n-1-i]
            wv=match(rng,a,b); lv=b if wv is a else a
            winners.append(wv); losers.append(lv)
        if YOU in losers: out_round=bi; break
        cur=winners
        if len(cur)==1: break
    if YOU in cur and len(cur)==1: place=1
    elif out_round is not None:
        size,best=bands[out_round]
        lose_grp=sorted([l for l in losers],key=lambda p:p["seed"])
        place=best+lose_grp.index(YOU)
    else: place=2 if len(cur)==2 and YOU in cur else place

dist=collections.Counter(p["deck"] for p in players)
L=[f"800-PLAYER EVENT — field at real meta shares, ALL matches Bo3 ({ROUNDS} Swiss + Top 64 cut)\n"]
L.append(f"YOUR deck: user_greninja (Mega Greninja / Dragapult)")
L.append(f"Swiss record: {YOU['w']}-{YOU['l']}   Resistance: {100*YOU['res']:.1f}%   Swiss seed: {YOU['seed']}/800")
L.append(f"FINAL PLACEMENT: {place}")
L.append("\nTop 10 Swiss standings:")
for p in standings[:10]:
    tag=" <== YOU" if p.get("you") else ""
    L.append(f"  {p['seed']:>3}. {p['w']}-{p['l']}  res {100*p['res']:.0f}%  {p['deck']}{tag}")
L.append(f"\n(field: {dist.most_common(5)} ...)")
open(os.path.join(HERE,"data","event800_result.txt"),"w").write("\n".join(L))
print("\n".join(L)); print("FINAL")
