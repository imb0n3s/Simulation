#!/usr/bin/env python3
"""100 complete 800-player tournaments. Exact Swiss (record pairing, resistance,
per-event cut), ALL Bo3. Per-game outcomes drawn from the measured matchup table
(first/second-player specific, built from ~26k real sims; user deck piloted by
the spread-skilled DeepSearchAgent in its measurements)."""
import json, random, collections, statistics, os
HERE=os.path.dirname(os.path.abspath(__file__))
tab=json.load(open(os.path.join(HERE,"data","matchup_table.json")))
OFF=json.load(open(os.path.join(HERE,"data","calibration_offsets.json")))
import math
def _cal(p, x, y):
    p=min(max(p,1e-3),1-1e-3)
    z=math.log(p/(1-p)) + OFF.get(x,0.0) - OFF.get(y,0.0)
    return min(0.95, max(0.05, 1/(1+math.exp(-z))))
W = {
 "dragapult_dudunsparce_ex":2.43,"dragapult_dudunsparce_ex_v2":2.43,"dragapult_blaziken":2.43,
 "dragapult_ex":2.43,"dragapult_dusknoir":2.43,"dragapult_dusknoir_v2":2.43,"dragapult_dusknoir_v3":2.43,
 "dragapult_dudunsparce":2.43,"greninja_dragapult":2.43,"dragapult_toolbox":2.43,"dragapult_dusknoir_budew":2.43,
 "dragapult_blaziken_v2":2.43,"dragapult_blaziken_v3":2.43,"dragapult_starmie":2.43,"dragapult_hammers":2.43,
 "tera_box_raging_bolt":6.5,"ionos_bellibolt":2.0,
 "mega_lopunny_dudunsparce":3.0,"froslass_lopunny":1.5,"lopunny_froslass_box":1.5,
 "alakazam_dudunsparce":1.75,"alakazam_dudunsparce_v2":1.75,"alakazam_v3":1.75,
 "festival_lead":3.0,"festival_greninja":1.5,
 "mega_lucario":1.12,"mega_lucario_v2":1.12,"mega_lucario_v3":1.12,"mega_lucario_v4":1.12,"mega_lucario_v5":1.12,
 "team_rockets_mewtwo":3.6,"ns_zoroark":1.75,"ns_zoroark_v2":1.75,"cynthia_garchomp":3.1,
 "mega_starmie_froslass":0.8,"starmie_froslass_v2":0.8,"starmie_dusknoir":0.8,
 "rockets_honchkrow":1.6,"team_rockets_porygon":0.8,"meganium_ogerpon":1.7,
 "slowking_box":0.56,"slowking_latias":0.56,"slowking_latias_v2":0.56,"hydrapple_meganium":4.5,
 "mega_kangaskhan_crustle":1.3,"lillies_clefairy_box":1.2,"mega_diancie_dusknoir":0.4,
 "mega_feraligatr":0.3,"metagross_cinccino":0.3,"hydreigon_cinderace":0.3,
 "sinistcha_ogerpon":3.2,"mega_pyroar":0.3,"jellicent_control":0.4,
 "beedrill_ex_dudunsparce":0.5,"beedrill_dudunsparce_v2":0.5,
 "greninja":0.2,"greninja_dusknoir":0.2,"greninja_froslass":0.2,"user_greninja_dudunsparce":0.2,
 "user_greninja_dragapult":0.2,"user_greninja_froslass":0.2,"greninja_noctowl":0.2,
 "blaziken_greninja_zoroark":0.3,"zoroark_greninja_dusknoir":0.3,
 "mega_kangaskhan_box":0.7,"marnies_grimmsnarl":0.6,"tera_box":0.15,"tera_box_kangaskhan":0.15,
 "hops_trevenant":0.2,"ceruledge_ex":0.1,"mega_venusaur":0.1,
}
USER="user_greninja"
def gprob(x,y,x_first):
    if x==y: return 0.52 if x_first else 0.48, 0.01   # mirror: slight first-player edge
    k1,k2=f"{x}|{y}",f"{y}|{x}"
    if k1 in tab:
        s=tab[k1]["af" if x_first else "bf"]; n=sum(s.values())
        return _cal(s["w"]/n, x, y), s["d"]/n
    s=tab[k2]["bf" if x_first else "af"]; n=sum(s.values())
    return _cal(s["l"]/n, x, y), s["d"]/n
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

ROUNDS=12; FIELD=2500; N=100
ROUND_BAND=[(33,64),(17,32),(9,16),(5,8),(3,4),(2,2)]
import time, json as _json
STF=os.path.join(HERE,"data","event100_2500_state.json")
_st=_json.load(open(STF)) if os.path.exists(STF) else {"done":0,"placements":[],"recs":{},"cuts":0,"top8":0,"champs":{},"cutline":[]}
placements=_st["placements"]; recs=collections.Counter(_st["recs"]); cuts=_st["cuts"]; top8=_st["top8"]
champs=collections.Counter(_st["champs"]); cutline=_st["cutline"]
elim_at=collections.Counter()
t64=collections.Counter(_st.get("t64",{}))
master=random.Random(20262026)
_t0=time.time()
for ev in range(N):
    _seed=master.randint(1,10**9)
    if ev < _st["done"]: continue
    if time.time()-_t0 > 38:
        _json.dump({"done":_st["done"],"placements":placements,"recs":dict(recs),"cuts":cuts,"top8":top8,"champs":dict(champs),"cutline":cutline,"t64":dict(t64)},open(STF,"w"))
        print(f"checkpoint: {_st['done']}/100 events done — RERUN to continue"); raise SystemExit
    rng=random.Random(_seed)
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
    for _p in standings[:64]: t64[_p["deck"]]+=1
    you_in= YOU["seed"]<=64
    if you_in: cuts+=1
    out=False
    for bi,(blo,bhi) in enumerate(ROUND_BAND[:3]):      # 64 -> 32 -> 16 -> 8 (re-seeded)
        cur=sorted(cur,key=lambda p:p["seed"]); n=len(cur); winners=[]; losers=[]
        for i in range(n//2):
            a,b=cur[i],cur[n-1-i]
            wv=bo3(rng,a,b); winners.append(wv); losers.append(b if wv is a else a)
        if you_in and YOU in losers:
            grp=sorted(losers,key=lambda p:p["seed"]); place=blo+grp.index(YOU); out=True; break
        cur=winners
    if not out:
        # FIXED seeded Top 8: QF 1v8/2v7/3v6/4v5, SF w(1v8)vsw(4v5) & w(2v7)vsw(3v6)
        cur=sorted(cur,key=lambda p:p["seed"])
        qf=[bo3(rng,cur[i],cur[j]) for i,j in [(0,7),(1,6),(2,5),(3,4)]]
        if you_in and YOU in cur and YOU not in qf: place=5
        sf1=bo3(rng,qf[0],qf[3]); sf2=bo3(rng,qf[1],qf[2])
        if you_in and YOU in qf and YOU not in (sf1,sf2): place=3
        ch=bo3(rng,sf1,sf2)
        cur=[ch]
        if you_in and place is None: place=1 if YOU is ch else (2 if YOU in (sf1,sf2) else place)
    champs[cur[0]["deck"] if len(cur)>=1 else "?"]+=1
    if not you_in: place=YOU["seed"]
    if place<=8: top8+=1
    placements.append(place); _st["done"]=ev+1
placements.sort()
L=[f"100 FULL 2500-PLAYER TOURNAMENTS — exact Swiss + Top64 cut, ALL Bo3, matchup table (~92k sims) + real-results calibration layer",
   f"YOUR deck piloted (in its matchup measurements) by the spread-skilled DeepSearchAgent\n",
   f"YOUR Swiss records: {dict(sorted(recs.items(), key=lambda kv:-int(kv[0][0])))}",
   f"median placement: {statistics.median(placements):.0f}",
   f"best: {placements[0]}   worst: {placements[-1]}",
   f"25th-75th pct: {placements[24]} - {placements[74]}",
   f"made Top 64: {cuts}/100   made Top 8: {top8}/100",
   f"typical cut line (wins of 64th seed): min {min(cutline)}, median {statistics.median(cutline)}, max {max(cutline)}",
   f"\nEvent champions by archetype: {dict(champs.most_common())}"]
open(os.path.join(HERE,"data","event100_2500_result.txt"),"w").write("\n".join(L))
json.dump(dict(t64),open(os.path.join(HERE,"data","sim_top64.json"),"w"))
print("\n".join(L))
