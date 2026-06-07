#!/usr/bin/env python3
"""Approximate policy iteration. Run: python3 iterate.py <k>
 k=1: generate games with the heuristic policy, fit value_1, benchmark value-greedy(value_1).
 k>1: generate games with value-greedy(value_{k-1}), fit value_k, benchmark.
State persisted in data/policy_iter.json (weights + benchmark trajectory)."""
import glob, os, json, random, sys, math, copy
import numpy as np
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent
from effects import Effects, SkilledPolicy

HERE=os.path.dirname(os.path.abspath(__file__)); CAT=load_catalog()
GEN=1500; MAXT=60
STATE=os.path.join(HERE,"data","policy_iter.json")
DECKS={os.path.splitext(os.path.basename(p))[0]:[c.card_id for c in build_deck(p)]
       for p in sorted(glob.glob(os.path.join(HERE,"decks","*.txt")))}
names=list(DECKS)

def rem(g,m):
    try: return g.effective_max_hp(m)-m.damage
    except Exception: return m.max_hp-m.damage
def feats(g, me, opp):
    opp_ko=sum(1 for m in opp.all_pokemon() if 0<rem(g,m)<=100)
    my_vuln=sum(1 for m in me.all_pokemon() if 0<rem(g,m)<=100)
    ready=sum(1 for m in me.all_pokemon() if len(m.energy)>=2)
    return [1.0, len(me.prizes), len(opp.prizes), len(opp.prizes)-len(me.prizes),
            opp_ko, my_vuln,
            sum(x.damage for x in opp.all_pokemon())/100.0,
            sum(x.damage for x in me.all_pokemon())/100.0,
            ready, len(me.bench), len(opp.bench), g.turn/10.0]
NF=12

fx=Effects(SkilledPolicy())

class ValueAgent(HeuristicAgent):
    def __init__(self, fx, w, mu, sd):
        super().__init__(fx); self.w=np.array(w); self.mu=np.array(mu); self.sd=np.array(sd)
        self._roller=None
    def _val(self, g, pi):
        me=g.players[pi]; opp=g.players[1-pi]
        x=(np.array(feats(g,me,opp))-self.mu)/self.sd
        return 1/(1+math.exp(-float(x@self.w)))
    def _gust_card(self,p):
        if p.supporter_played_this_turn: return None
        for c in p.hand:
            if c.is_trainer and "Supporter" in c.subtypes and any(e.get("op")=="gust_opponent_bench" for e in c.data.get("effects",[])):
                return c
        return None
    def take_turn(self, game, p):
        self._skip_ops={"gust_opponent_bench"}; super().take_turn(game,p); self._skip_ops=set()
    def _attack_step(self, game, p):
        if not (p.active and game.turn>1): return
        pi=game.players.index(p); atks=p.active.card.data.get("attacks",[])
        aff=[i for i in range(len(atks)) if game._cost_met(p.active,atks[i]["cost"],atks[i])]
        if not aff: return
        opp=game.players[1-pi]; gopts=[None]
        if self._gust_card(p) and opp.bench: gopts.append(max(range(len(opp.bench)),key=lambda j:opp.bench[j].damage))
        best=None; bv=-1
        for gidx in gopts:
            for a in aff:
                cl=copy.deepcopy(game); cp=cl.players[pi]
                try:
                    if gidx is not None:
                        gc=self._gust_card(cp)
                        if gc:
                            self.fx.policy._force=gidx
                            try: self.fx.play_trainer(cl,cp,gc)
                            finally: self.fx.policy._force=None
                    cl.attack(cp,a,self.fx)
                except Exception:
                    self.fx.policy._force=None; continue
                v=self._val(cl,pi)
                if v>bv: bv=v; best=(gidx,a)
        if best is None: return
        gidx,a=best
        if gidx is not None:
            gc=self._gust_card(p)
            if gc:
                self.fx.policy._force=gidx; self._try(lambda c=gc: self.fx.play_trainer(game,p,c)); self.fx.policy._force=None
        self._try(lambda i=a: game.attack(p,i,self.fx))

def play(agA,agB,A,B,seed):
    pa=Player("A",[Card(c,CAT) for c in A]); pb=Player("B",[Card(c,CAT) for c in B])
    g=Game(pa,pb,seed=seed); g.log=lambda *x,**k:None; g.setup()
    samples=[]
    ags={id(pa):agA,id(pb):agB}
    while not g.winner and g.turn<MAXT:
        if not g.start_turn(): break
        cur=g.current; opp=g.players[1-g.players.index(cur)]
        ags[id(cur)].take_turn(g,cur); g.end_turn()
        if random.random()<0.3: samples.append((id(cur)==id(pa), feats(g, cur, opp) if False else feats(g, pa if cur is pa else pb, pb if cur is pa else pa)))
    win_a = g.winner is pa
    rows=[(fv, 1 if (is_a==win_a) else 0) for is_a,fv in samples] if g.winner else []
    return ("A" if win_a else "B" if g.winner is pb else None), rows

def logistic(X,y):
    X=np.array(X); y=np.array(y); n,d=X.shape
    mu=X.mean(0); sd=X.std(0); sd[sd==0]=1; mu[0]=0; sd[0]=1
    Xs=(X-mu)/sd; w=np.zeros(d); lr=0.3
    for _ in range(3000):
        p=1/(1+np.exp(-Xs@w)); w-=lr*(Xs.T@(p-y)/n)
    acc=(( (1/(1+np.exp(-Xs@w)))>0.5)==y).mean()
    return w.tolist(), mu.tolist(), sd.tolist(), float(acc)

def main():
    k=int(sys.argv[1])
    state=json.load(open(STATE)) if os.path.exists(STATE) else {"iters":[]}
    rng=random.Random(100+k)
    # generating policy
    if k==1:
        genA=HeuristicAgent(fx); genB=HeuristicAgent(fx)
    else:
        prev=state["iters"][k-2]
        genA=ValueAgent(fx,prev["w"],prev["mu"],prev["sd"]); genB=ValueAgent(fx,prev["w"],prev["mu"],prev["sd"])
    X=[]; y=[]
    for i in range(GEN):
        a,b=rng.sample(names,2)
        r,rows=play(genA,genB,DECKS[a],DECKS[b],rng.randint(1,10**9))
        for fv,lab in rows: X.append(fv); y.append(lab)
    w,mu,sd,acc=logistic(X,y)
    # benchmark value-greedy(value_k) vs heuristic on fixed matchups
    va=ValueAgent(fx,w,mu,sd); he=HeuristicAgent(fx)
    bench=["dragapult_dudunsparce_ex","slowking_box","mega_lucario","ns_zoroark"]
    bw=bt=0
    for d in bench:
        for opp in ["mega_kangaskhan_crustle","meganium_ogerpon","cynthia_garchomp"]:
            for gi in range(6):
                ags=(va,he)
                r,_=play(va,he,DECKS[d],DECKS[opp],rng.randint(1,10**9))
                bw+= (r=="A"); bt+=1
    state["iters"]=state["iters"][:k-1]+[{"k":k,"w":w,"mu":mu,"sd":sd,"acc":acc,
                                          "bench_win":bw,"bench_games":bt,"samples":len(y)}]
    json.dump(state,open(STATE,"w"))
    print(f"iter {k}: value acc {100*acc:.1f}% on {len(y)} samples | value-greedy vs heuristic = {100*bw/bt:.1f}% ({bw}/{bt})")

if __name__=="__main__":
    main()
