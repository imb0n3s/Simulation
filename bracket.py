#!/usr/bin/env python3
"""390-player tournament: 9 Swiss rounds (Bo1) -> Top 64 single-elimination (Bo3).
Reports archetype representation at each cut and the elimination results."""
import glob, os, random, collections
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent
from effects import Effects, SkilledPolicy

HERE=os.path.dirname(os.path.abspath(__file__)); CAT=load_catalog()
fx=Effects(SkilledPolicy()); agent=HeuristicAgent(fx)
rng=random.Random(7777)
MAXT=60

deck_paths=sorted(glob.glob(os.path.join(HERE,"decks","*.txt")))
DECKS={os.path.splitext(os.path.basename(p))[0]:[c.card_id for c in build_deck(p)] for p in deck_paths}
archs=list(DECKS)

def play1(cids_a, cids_b, a_first, seed):
    pa=Player("A",[Card(c,CAT) for c in cids_a]); pb=Player("B",[Card(c,CAT) for c in cids_b])
    p1,p2=(pa,pb) if a_first else (pb,pa)
    g=Game(p1,p2,seed=seed); g.log=lambda *a,**k:None; g.setup()
    while not g.winner and g.turn<MAXT:
        if not g.start_turn(): break
        agent.take_turn(g,g.current); g.end_turn()
    return "A" if g.winner is pa else "B" if g.winner is pb else None

def match(a,b,bo=3):
    """Best-of-bo. Returns winning player id. Ties broken by lower seed (a assumed higher seed)."""
    need=bo//2+1; wa=wb=0; n=0
    while wa<need and wb<need and n<bo+2:
        r=play1(a["cids"],b["cids"], n%2==0, rng.randint(1,10**9)); n+=1
        if r=="A": wa+=1
        elif r=="B": wb+=1
    return a if wa>=wb else b

# --- build field ---
players=[]
for i in range(390):
    name=archs[i%len(archs)]
    players.append({"id":i,"deck":name,"cids":DECKS[name],"w":0,"l":0,"opp":[]})

# --- Swiss ---
ROUNDS=9
for rd in range(ROUNDS):
    order=sorted(players,key=lambda p:(-p["w"], p["l"], rng.random()))
    paired=set(); 
    i=0
    # pair neighbours, light rematch avoidance
    pool=order[:]
    while pool:
        a=pool.pop(0)
        if a["id"] in paired: continue
        # find first opponent not yet played
        opp=None
        for j,b in enumerate(pool):
            if b["id"] in paired: continue
            if b["id"] not in a["opp"]: opp=b; break
        if opp is None:
            opp=next((b for b in pool if b["id"] not in paired), None)
        if opp is None: break
        paired.add(a["id"]); paired.add(opp["id"])
        a["opp"].append(opp["id"]); opp["opp"].append(a["id"])
        w=match(a,opp,bo=3)
        if w is a: a["w"]+=1; opp["l"]+=1
        else: opp["w"]+=1; a["l"]+=1

# resistance (opponents' win %)
wmap={p["id"]:p["w"]/max(1,p["w"]+p["l"]) for p in players}
for p in players:
    p["res"]=sum(wmap[o] for o in p["opp"])/max(1,len(p["opp"]))
standings=sorted(players,key=lambda p:(-p["w"], -p["res"], rng.random()))
for s,p in enumerate(standings,1): p["seed"]=s

def arch_counts(group):
    c=collections.Counter(p["deck"] for p in group)
    return ", ".join(f"{d} x{n}" for d,n in c.most_common())

out=[]
out.append(f"390-player event — {ROUNDS} Swiss rounds (Bo3), then Top 64 single-elim (Bo3) — full Bo3 event\n")
out.append("Swiss top 16 standings:")
out.append(f"{'Seed':<5}{'Rec':<7}{'Res%':<7}Deck")
for p in standings[:16]:
    out.append(f"{p['seed']:<5}{str(p['w'])+'-'+str(p['l']):<7}{100*p['res']:<6.1f} {p['deck']}")

top64=standings[:64]
out.append(f"\n=== TOP 64 — archetype representation ===\n{arch_counts(top64)}")

# --- single elimination from top 64 (standard bracket: 1v64,2v63,...) ---
def run_round(field):
    n=len(field); winners=[]
    for i in range(n//2):
        a=field[i]; b=field[n-1-i]
        winners.append(match(a,b,bo=3))
    return winners

r32=run_round(top64)         # 64 -> 32
out.append(f"\n=== TOP 32 — archetype representation ===\n{arch_counts(r32)}")
r16=run_round(sorted(r32,key=lambda p:p['seed']))
r8 =run_round(sorted(r16,key=lambda p:p['seed']))
out.append(f"\n=== TOP 8 ===")
for p in sorted(r8,key=lambda p:p['seed']): out.append(f"  Seed {p['seed']:<3} {p['w']}-{p['l']} Swiss  {p['deck']}")
out.append(f"\nTop 8 archetypes: {arch_counts(r8)}")
r4=run_round(sorted(r8,key=lambda p:p['seed']))
out.append(f"\n=== SEMIFINALISTS (Top 4) ===")
for p in sorted(r4,key=lambda p:p['seed']): out.append(f"  Seed {p['seed']:<3} {p['deck']}")
r2=run_round(sorted(r4,key=lambda p:p['seed']))
out.append(f"\n=== FINALISTS ===")
for p in sorted(r2,key=lambda p:p['seed']): out.append(f"  Seed {p['seed']:<3} {p['deck']}")
champ=run_round(sorted(r2,key=lambda p:p['seed']))[0]
out.append(f"\n*** CHAMPION: Seed {champ['seed']} — {champ['deck']} (Swiss {champ['w']}-{champ['l']}) ***")

open(os.path.join(HERE,"data","bracket_results.txt"),"w").write("\n".join(out))
print("\n".join(out))
