#!/usr/bin/env python3
"""Empirical feedback: nudge family offsets toward the REAL top-cut composition
using the sim's own observed top-cut shares. Run after each run100_2500 pass."""
import json, collections, math, re, os, sys
HERE=os.path.dirname(os.path.abspath(__file__))
sim=json.load(open(os.path.join(HERE,'data/sim_top64.json')))
real=json.load(open(os.path.join(HERE,'data/real_topcut.json')))["shares"]
off=json.load(open(os.path.join(HERE,'data/calibration_offsets.json')))
src=open(os.path.join(HERE,'calibrate.py')).read()
ns={}; exec(re.search(r'FAM=\{.*?\n\}', src, re.S).group(0), {}, ns); FAM=ns['FAM']
simfam=collections.Counter()
for d,n in sim.items(): simfam[FAM.get(d,"Other")]+=n
tot=sum(simfam.values())
LR=float(sys.argv[1]) if len(sys.argv)>1 else 0.45
adj={}
for fam,r in real.items():
    s=100*simfam.get(fam,0)/tot
    delta=LR*math.log(max(r,0.3)/max(s,0.3))
    adj[fam]=max(-0.6,min(0.6,delta))
for d in off:
    if d=="user_greninja": continue
    fam=FAM.get(d)
    if fam in adj: off[d]=round(off[d]+adj[fam],4)
json.dump(off,open(os.path.join(HERE,'data/calibration_offsets.json'),'w'),indent=1)
big=sorted(adj.items(),key=lambda kv:-abs(kv[1]))[:6]
print("nudges:",", ".join(f"{k} {v:+.2f}" for k,v in big))
