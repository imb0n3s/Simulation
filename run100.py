#!/usr/bin/env python3
"""100 complete 800-player tournaments. Exact Swiss (record pairing, resistance,
per-event cut), ALL Bo3. Per-game outcomes drawn from the measured matchup table
(first/second-player specific, built from ~26k real sims; user deck piloted by
the spread-skilled DeepSearchAgent in its measurements)."""
import json, random, collections, statistics, os
HERE=os.path.dirname(os.path.abspath(__file__))
tab=json.load(open(os.path.join(HERE,"data","matchup_table.json")))
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
USER="user_greninja"
def gprob(x,y,x_first):
    if x==y: return 0.52 if x_first else 0.48, 0.01   # mirror: slight first-player edge
    k1,k2=f"{x}|{y}",f"{y}|{x}"
    if k1 in tab:
        s=tab[k1]["af" if x_first else "bf"]; n=sum(s.values())
        return s["w"]/n, s["d"]/n
    s=tab[k2]["bf" if x_first else "af"]; n=sum(s.values())
    return s["l"]/n, s["d"]/n
def game(rng,x,y,x_first):
    pw,pd=gprob(x,y,x_first); r=rng.random()
    return "X" if r<pw else ("D" if r<pw+pd else "Y")
def bo3(rng,a,b):
    wa=wb=n=0
    while wa<2 and wb<2 and n<5:
        r=game(rng,a["deck"],b["deck"], n%2==0); n+=1
        if r=="X": wa+=1
        elif r=="Y": wb+=1
    return a if wa>=wb else b

ROUNDS=9; FIELD=800; N=100
ROUND_BAND=[(33,64),(17,32),(9,16),(5,8),(3,4),(2,2)]
placements=[]; recs=collections.Counter(); cuts=0; top8=0; champs=collections.Counter()
elim_at=collections.Counter(); cutline=[]
master=random.Random(20262026)
for ev in range(N):
    rng=random.Random(master.randint(1,10**9))
    pool=list(W); wts=[W[d] for d in pool]
    players=[{"id":i,"deck":d,"w":0,"l":0,"opp":[]} for i,d in enumerate(rng.choices(pool,weights=wts,k=FIELD-1))]
    players.append({"id":FIELD-1,"deck":USER,"w":0,"l":0,"opp":[],"you":True})
    for rd in range(ROUNDS):
        order=sorted(players,key=lambda p:(-p["w"],p["l"],rng.random())); pool2=order[:]; paired=set()
        while pool2:
            a=pool2.pop(0)
            if a["id"] in paired: continue
            opp=next((b for b in pool2 if b["id"] not in paired and b["id"] not in a["opp"]),None) or \
                next((b for b in pool2 if b["id"] not in paired),None)
            if opp is None: break
            paired.add(a["id"]); paired.add(opp["id"]); a["opp"].append(opp["id"]); opp["opp"].append(a["id"])
            wn=bo3(rng,a,opp); ls=opp if wn is a else a
            wn["w"]+=1; ls["l"]+=1
    wmap={p["id"]:p["w"]/max(1,p["w"]+p["l"]) for p in players}
    for p in players: p["res"]=sum(wmap[o] for o in p["opp"])/max(1,len(p["opp"]))
    standings=sorted(players,key=lambda p:(-p["w"],-p["res"],rng.random()))
    for s,p in enumerate(standings,1): p["seed"]=s
    YOU=next(p for p in players if p.get("you"))
    recs[f"{YOU['w']}-{YOU['l']}"]+=1
    cutline.append(standings[63]["w"])
    cur=standings[:64]; place=None
    you_in= YOU["seed"]<=64
    if you_in: cuts+=1
    for bi,(blo,bhi) in enumerate(ROUND_BAND):
        cur=sorted(cur,key=lambda p:p["seed"]); n=len(cur); winners=[]; losers=[]
        for i in range(n//2):
            a,b=cur[i],cur[n-1-i]
            wv=bo3(rng,a,b); winners.append(wv); losers.append(b if wv is a else a)
        if you_in and YOU in losers:
            grp=sorted(losers,key=lambda p:p["seed"]); place=blo+grp.index(YOU); elim_at[ROUND_BAND[bi]]+=1; break
        cur=winners
        if len(cur)==1: break
    champs[cur[0]["deck"] if len(cur)>=1 else "?"]+=1
    if you_in and place is None:
        place=1 if cur and cur[0] is YOU else 2
    if not you_in: place=YOU["seed"]
    if place<=8: top8+=1
    placements.append(place)
placements.sort()
L=[f"100 FULL 800-PLAYER TOURNAMENTS — exact Swiss + Top64 cut, ALL Bo3, matchup table from ~26k real games",
   f"YOUR deck piloted (in its matchup measurements) by the spread-skilled DeepSearchAgent\n",
   f"YOUR Swiss records: {dict(sorted(recs.items(), key=lambda kv:-int(kv[0][0])))}",
   f"median placement: {statistics.median(placements):.0f}",
   f"best: {placements[0]}   worst: {placements[-1]}",
   f"25th-75th pct: {placements[24]} - {placements[74]}",
   f"made Top 64: {cuts}/100   made Top 8: {top8}/100",
   f"typical cut line (wins of 64th seed): min {min(cutline)}, median {statistics.median(cutline)}, max {max(cutline)}",
   f"\nEvent champions by archetype: {dict(champs.most_common())}"]
open(os.path.join(HERE,"data","event100_result.txt"),"w").write("\n".join(L))
print("\n".join(L))
