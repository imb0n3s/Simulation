#!/usr/bin/env python3
"""Real-world calibration: per-deck Bradley-Terry logit offsets =
logit(real-performance target win rate) - logit(simulated field win rate).
Real targets come from pts/deck across 1,071 real decks (events 535/544/549/559).
This removes the bot's systematic piloting bias (beatsticks up, control down)
while preserving each pair's matchup texture."""
import json, math, os
HERE=os.path.dirname(os.path.abspath(__file__))
tab=json.load(open(os.path.join(HERE,"data","matchup_table.json")))
real=json.load(open(os.path.join(HERE,"data","real_metagame.json")))["agg"]

FAM={  # deck -> real archetype family
 **{d:"Dragapult" for d in ["dragapult_dudunsparce_ex","dragapult_dudunsparce_ex_v2","dragapult_blaziken",
   "dragapult_ex","dragapult_dusknoir","dragapult_dusknoir_v2","dragapult_dusknoir_v3","dragapult_dudunsparce",
   "greninja_dragapult","dragapult_toolbox","dragapult_dusknoir_budew","dragapult_blaziken_v2",
   "dragapult_blaziken_v3","dragapult_starmie","dragapult_hammers"]},
 **{d:"Alakazam" for d in ["alakazam_dudunsparce","alakazam_dudunsparce_v2","alakazam_v3"]},
 **{d:"Mega Lucario" for d in ["mega_lucario","mega_lucario_v2","mega_lucario_v3","mega_lucario_v4","mega_lucario_v5"]},
 **{d:"Mega Lopunny" for d in ["mega_lopunny_dudunsparce","froslass_lopunny","lopunny_froslass_box"]},
 **{d:"Raging Bolt" for d in ["tera_box_raging_bolt","ionos_bellibolt"]},
 **{d:"Festival Lead" for d in ["festival_lead","festival_greninja"]},
 **{d:"Mega Starmie" for d in ["mega_starmie_froslass","starmie_froslass_v2","starmie_dusknoir"]},
 **{d:"Rocket's Honchkrow" for d in ["rockets_honchkrow","team_rockets_porygon"]},
 **{d:"Slowking" for d in ["slowking_box","slowking_latias","slowking_latias_v2"]},
 **{d:"Greninja" for d in ["greninja","greninja_dusknoir","greninja_froslass","user_greninja",
   "user_greninja_dudunsparce","user_greninja_dragapult","user_greninja_froslass","greninja_noctowl",
   "blaziken_greninja_zoroark","zoroark_greninja_dusknoir"]},
 **{d:"N's Zoroark" for d in ["ns_zoroark","ns_zoroark_v2"]},
 **{d:"Mega Kangaskhan" for d in ["mega_kangaskhan_box","mega_kangaskhan_crustle","tera_box_kangaskhan"]},
 "team_rockets_mewtwo":"Rocket's Mewtwo","cynthia_garchomp":"Cynthia's Garchomp",
 "meganium_ogerpon":"Ogerpon Meganium","ogerpon_meganium_arboliva":"Ogerpon Meganium",
 "ogerpon_meganium_arboliva_v2":"Ogerpon Meganium","ogerpon_meganium_hydrapple":"Ogerpon Meganium",
 "froslass_arboliva":"Mega Froslass","hydrapple_meganium":"Hydrapple",
 "lillies_clefairy_box":"Lillie's Clefairy","marnies_grimmsnarl":"Marnie's Grimmsnarl",
 "tera_box":"Tera Box","hops_trevenant":"Hop's Trevenant","ceruledge_ex":"Ceruledge",
 "mega_venusaur":"Mega Venusaur","mega_diancie_dusknoir":"Mega Diancie",
 "mega_feraligatr":"Mega Sharpedo","metagross_cinccino":"Steven's Metagross",
 "beedrill_ex_dudunsparce":"Crustle","beedrill_dudunsparce_v2":"Crustle",
 "hydreigon_cinderace":"Archaludon","sinistcha_ogerpon":"Ogerpon Box","mega_pyroar":"Flareon",
 "jellicent_control":"Mega Gardevoir",
}
mean_pts=sum(v["points"] for v in real.values())/sum(v["count"] for v in real.values())
# measured top-cut composition across Utrecht/Indy/Aichi (n=126) and real field shares
TOPCUT=json.load(open(os.path.join(HERE,"data","real_topcut.json")))["shares"]
def target(d):
    fam=FAM.get(d)
    if not fam: return 0.5
    t_pts=0.5
    if fam in real:
        r=real[fam]; pts=(r["pts_per_deck"]*r["count"] + mean_pts*15)/(r["count"]+15)
        t_pts=max(0.40, min(0.60, 0.5 + 0.25*math.log(pts/mean_pts)))
    t_conv=None
    if fam in TOPCUT and fam in real and real[fam]["share"]>0:
        conv=max(0.15, TOPCUT[fam]/real[fam]["share"])   # top-cut conversion vs field share
        t_conv=max(0.40, min(0.62, 0.5 + 0.16*math.log(conv)))
    if t_conv is None: return t_pts
    return 0.25*t_conv + 0.75*t_pts  # pts proxy dominates; conv is a gentle pull
def logit(p): p=min(max(p,1e-3),1-1e-3); return math.log(p/(1-p))

decks=set()
for k in tab: decks.update(k.split("|"))
# sim field win rate per deck
wins={d:[0,0] for d in decks}
for k,res in tab.items():
    a,b=k.split("|")
    if a==b: continue
    w=res["af"]["w"]+res["bf"]["w"]; n=sum(res["af"].values())+sum(res["bf"].values())
    wins[a][0]+=w; wins[a][1]+=n
    wins[b][0]+=n-w-res["af"]["d"]-res["bf"]["d"]; wins[b][1]+=n
# pairwise sim probabilities for iterative fitting
import itertools
P={}
for k,res in tab.items():
    a_,b_=k.split("|")
    if a_==b_: continue
    n=sum(res["af"].values())+sum(res["bf"].values())
    P[(a_,b_)]=(res["af"]["w"]+res["bf"]["w"])/max(1,n)
dl=sorted(decks)
off={d:0.0 for d in dl}
def field_wr(d):
    tot=0;cnt=0
    for e in dl:
        if e==d: continue
        p=P.get((d,e)); 
        if p is None:
            q=P.get((e,d)); p=None if q is None else 1-q
        if p is None: continue
        z=logit(p)+off[d]-off[e]
        tot+=1/(1+math.exp(-z)); cnt+=1
    return tot/max(1,cnt)
for it in range(6):   # fixed-point iteration: push calibrated field wr to real target
    for d in dl:
        gap=logit(target(d))-logit(field_wr(d))
        off[d]+=0.7*gap
m=sum(off.values())/len(off)
off={d:round(v-m,4) for d,v in off.items()}
# YOUR deck is piloted by YOU (DeepSearch-modeled), not the average real Greninja player:
off["user_greninja"]=0.0
off["starmie_froslass_munkidori"]=0.0  # user pilot decks: rows are direct planner measurements
json.dump(off,open(os.path.join(HERE,"data","calibration_offsets.json"),"w"),indent=1)
rk=sorted(off.items(),key=lambda kv:-kv[1])
print("biggest boosts (under-piloted in sim):")
for d,v in rk[:6]: print(f"  {d:<28} {v:+.2f}")
print("biggest cuts (over-piloted in sim):")
for d,v in rk[-6:]: print(f"  {d:<28} {v:+.2f}")
