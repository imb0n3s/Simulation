import os, json, random, time
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent, DeepSearchAgent
from effects import Effects, SkilledPolicy
HERE=os.path.dirname(os.path.abspath(__file__)); CAT=load_catalog()
MAXT=60; BUDGET=38
TAB=os.path.join(HERE,"data","matchup_table.json")
fx=Effects(SkilledPolicy()); heur=HeuristicAgent(fx); deep=DeepSearchAgent(fx,depth=3,samples=2)
FIELD=["dragapult_dudunsparce_ex","dragapult_dudunsparce_ex_v2","dragapult_blaziken","dragapult_ex",
 "dragapult_dusknoir","dragapult_dusknoir_v2","dragapult_dudunsparce","greninja_dragapult",
 "alakazam_dudunsparce","alakazam_dudunsparce_v2","mega_lucario","mega_lucario_v2",
 "team_rockets_mewtwo","ns_zoroark","cynthia_garchomp","mega_starmie_froslass","meganium_ogerpon",
 "slowking_box","mega_kangaskhan_crustle","lillies_clefairy_box","greninja","greninja_dusknoir",
 "greninja_froslass","mega_kangaskhan_box","marnies_grimmsnarl","tera_box","tera_box_kangaskhan",
 "hops_trevenant","ceruledge_ex","mega_venusaur"]
U="user_greninja_v2"
DECKS={d:[c.card_id for c in build_deck(os.path.join(HERE,"decks",d+".txt"))] for d in FIELD+[U]}
def game(a,b,agA,agB,seed):
    pa=Player("A",[Card(c,CAT) for c in DECKS[a]]); pb=Player("B",[Card(c,CAT) for c in DECKS[b]])
    g=Game(pa,pb,seed=seed); g.log=lambda *x,**k:None; g.setup()
    ags={id(pa):agA,id(pb):agB}
    while not g.winner and g.turn<MAXT:
        if not g.start_turn(): break
        ags[id(g.current)].take_turn(g,g.current); g.end_turn()
    return "A" if g.winner is pa else "B" if g.winner is pb else "D"
tab=json.load(open(TAB)); rng=random.Random(777); start=time.time(); done=0
for b in FIELD:
    key=f"{U}|{b}"
    if key in tab: continue
    if time.time()-start>BUDGET: break
    res={"af":{"w":0,"l":0,"d":0},"bf":{"w":0,"l":0,"d":0},"n":50}
    for k in range(50):
        r=game(U,b,deep,heur,rng.randint(1,10**9)); res["af"]["w" if r=="A" else "l" if r=="B" else "d"]+=1
        r2=game(b,U,heur,deep,rng.randint(1,10**9)); res["bf"]["w" if r2=="B" else "l" if r2=="A" else "d"]+=1
    tab[key]=res; done+=1
    json.dump(tab,open(TAB,"w"))
have=sum(1 for b in FIELD if f"{U}|{b}" in tab)
print(f"v2 pairs measured: {have}/30")
if have>=30: print("V2 TABLE COMPLETE")
