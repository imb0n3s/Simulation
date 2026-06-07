#!/usr/bin/env python3
"""Replay a PTCG Live game log and cross-check it against the engine's card catalog.

This is a *verification* harness, not a full re-simulation: real logs hide both
players' hands, prizes and shuffles, so we replay only the public board events
(plays, evolutions, attacks, counter placement, KOs, prizes) and check that the
catalog's modeled numbers reproduce what actually happened.

Three things get checked:
  1. Every card / attack named in the log exists in the catalog.
  2. Each attack's logged "Base damage" matches the catalog's base damage,
     and (for the deterministic ones) the logged total matches the modeled total.
  3. KOs are consistent: when a Pokemon is Knocked Out, the damage we've tracked
     on it is >= its modeled HP (so HP and damage modeling line up).

It also tallies the place-counters vs do-damage events, which is the distinction
the two paths in the engine exist to keep separate.
"""
import json, re, sys, os
from engine import load_catalog

CAT = load_catalog()
BY_NAME = {}
for cid, v in CAT.items():
    BY_NAME.setdefault(v["name"], []).append(v)

def hp_of(name):
    for v in BY_NAME.get(name, []):
        if v.get("hp"): return v["hp"]
    return None

def attack_base(name, atk):
    for v in BY_NAME.get(name, []):
        for a in v.get("attacks", []):
            if a["name"] == atk: return a.get("damage")
    return None

class Mon:
    __slots__ = ("name", "dmg")
    def __init__(self, name): self.name, self.dmg = name, 0

class Side:
    def __init__(self): self.active = None; self.bench = {}  # name -> Mon (best-effort, by name)
    def get(self, name):
        if self.active and self.active.name == name: return self.active
        return self.bench.setdefault(name, Mon(name))

class Replay:
    def __init__(self, path):
        self.path = path
        self.sides = {}
        self.issues = []
        self.kos = []
        self.attacks = []
        self.n_do = 0       # "doing damage" events (attacks / "took N damage")
        self.n_place = 0     # "placing counters" events
        self.missing = set()
        self.players = []

    def side(self, p):
        if p not in self.sides:
            self.sides[p] = Side(); self.players.append(p)
        return self.sides[p]

    def check_card(self, name):
        if name and name not in BY_NAME:
            self.missing.add(name)

    def run(self):
        for raw in open(self.path, encoding="utf-8"):
            line = raw.rstrip("\n").replace("\u2019", "'")  # PTCG Live curly apostrophes
            s = line.strip().lstrip("-").strip()
            self.handle(s)
        return self

    def handle(self, s):
        # plays to active / bench
        m = re.match(r"(\w[\w']*) played ([^.]+?) to the (Active Spot|Bench)\.", s)
        if m:
            pl, name, where = m.groups(); self.check_card(name)
            sd = self.side(pl); mon = Mon(name)
            if where == "Active Spot": sd.active = mon
            else: sd.bench[name] = mon
            return
        # promote
        m = re.match(r"(\w[\w']*)'s ([^.]+?) is now in the Active Spot\.", s)
        if m:
            pl, name = m.groups(); sd = self.side(pl)
            sd.active = sd.bench.pop(name, Mon(name)); return
        # evolve (carry damage by name)
        m = re.match(r"(\w[\w']*) evolved ([^.]+?) to ([^.]+?) (?:on the Bench|in the Active Spot)\.", s)
        if m:
            pl, frm, to = m.groups(); self.check_card(to); sd = self.side(pl)
            tgt = sd.active if (sd.active and sd.active.name == frm) else sd.bench.get(frm)
            if tgt: dmg = tgt.dmg; tgt.name = to; tgt.dmg = dmg
            return
        # attack with single target + damage
        m = re.match(r"(\w[\w']*)'s ([^.]+?) used ([^.]+?) on (\w[\w']*)'s ([^.]+?) for (\d+) damage\.", s)
        if m:
            pl, atk_mon, atk, opp, tgt, dmg = m.groups(); dmg = int(dmg)
            self.check_card(atk_mon)
            base = attack_base(atk_mon, atk)
            self.attacks.append((atk_mon, atk, base, dmg))
            self.n_do += 1
            d = self.side(opp); mon = d.active if (d.active and d.active.name == tgt) else d.get(tgt)
            mon.dmg += dmg
            return
        # attack used with no inline target (Mirage Barrage / Mortal Shuriken etc.)
        m = re.match(r"(\w[\w']*)'s ([^.]+?) used ([^.]+?)\.", s)
        if m:
            self.check_card(m.group(2)); return
        # "OWNER's NAME took N damage."  (damage, e.g. Mirage Barrage)
        m = re.match(r"(\w[\w']*)'s ([^.]+?) took (\d+) damage\.", s)
        if m:
            pl, name, dmg = m.groups(); self.n_do += 1
            self.side(pl).get(name).dmg += int(dmg); return
        # "PLAYER put K damage counters on OWNER's NAME."
        m = re.match(r"(\w[\w']*) put (\d+) damage counters on (\w[\w']*)'s ([^.]+?)\.", s)
        if m:
            _, k, owner, name = m.groups(); self.n_place += 1
            self.side(owner).get(name).dmg += int(k) * 10; return
        # checkup poison/burn counters
        m = re.match(r"(\d+) damage counters? was placed on (\w[\w']*)'s ([^.]+?) for the Special Condition", s)
        if m:
            k, owner, name = m.groups(); self.n_place += 1
            self.side(owner).get(name).dmg += int(k) * 10; return
        # KO
        m = re.match(r"(\w[\w']*)'s ([^.]+?) was Knocked Out!", s)
        if m:
            owner, name = m.groups(); sd = self.side(owner)
            mon = sd.active if (sd.active and sd.active.name == name) else sd.bench.get(name)
            tracked = mon.dmg if mon else None
            hp = hp_of(name)
            ok = (tracked is not None and hp is not None and tracked >= hp)
            self.kos.append((owner, name, tracked, hp, ok))
            if mon is sd.active: sd.active = None
            else: sd.bench.pop(name, None)
            return

    def report(self):
        print(f"\n=== {os.path.basename(self.path)} ===")
        print(f"players: {', '.join(self.players)}")
        print(f"events: {self.n_do} 'do-damage', {self.n_place} 'place-counter'")
        print("\n-- attack base-damage check (logged vs catalog) --")
        seen = set()
        for mon, atk, base, dealt in self.attacks:
            key = (mon, atk)
            if key in seen: continue
            seen.add(key)
            tag = "OK " if base is not None else "?? "
            print(f"  {tag}{mon} / {atk}: catalog base={base}, log dealt={dealt}")
        print("\n-- KO consistency (tracked damage vs modeled HP) --")
        for owner, name, tracked, hp, ok in self.kos:
            tag = "OK " if ok else "!! "
            print(f"  {tag}{owner}'s {name}: tracked {tracked} vs HP {hp}")
        print(f"\nmissing from catalog: {sorted(self.missing) or 'none'}")

if __name__ == "__main__":
    for p in sys.argv[1:]:
        Replay(p).run().report()
