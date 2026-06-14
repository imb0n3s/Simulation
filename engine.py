"""
Pokemon TCG Simulator - core rules engine.

Implements the Scarlet & Violet-era ruleset (as documented in the official
CRI rulebook saved at data/rulebook.txt): setup & mulligans, turn structure,
energy attachment (once per turn), evolution timing, retreat, attacking with
Weakness/Resistance, Knock Outs, prize cards, and the three win conditions.

Card-specific behaviour (abilities, attacks, trainer/energy effects) is NOT
hard-coded here. The engine exposes primitive operations and calls into
effects.py, which interprets the structured effect specs stored in cards.json.
This keeps the engine data-driven so new cards/decks can be "learned" just by
adding catalog entries.
"""
from __future__ import annotations
import json, random, os
from dataclasses import dataclass, field
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))

def load_catalog(path: str | None = None) -> dict:
    path = path or os.path.join(HERE, "cards.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_NAME_ATTACKS = None
def _name_attacks_index():
    """name -> {'attacks': {atkname: atkdict}, 'evolves_from': name}, built once from the
    catalog. Resolves Memory Dive (Relicanth): an evolved Pokemon may use the attacks of
    its previous Evolutions."""
    global _NAME_ATTACKS
    if _NAME_ATTACKS is None:
        cat = load_catalog(); idx = {}
        for d in cat.values():
            if d.get("supertype") != "Pok\u00e9mon": continue
            nm = d.get("name")
            slot = idx.setdefault(nm, {"attacks": {}, "evolves_from": d.get("evolves_from")})
            if slot["evolves_from"] is None and d.get("evolves_from"):
                slot["evolves_from"] = d.get("evolves_from")
            for a in d.get("attacks", []) or []:
                slot["attacks"].setdefault(a["name"], a)
        _NAME_ATTACKS = idx
    return _NAME_ATTACKS


# --------------------------------------------------------------------------
# Card instances
# --------------------------------------------------------------------------
class Card:
    """A single physical card instance in a game (one per copy)."""
    _seq = 0
    def __init__(self, card_id: str, catalog: dict):
        Card._seq += 1
        self.uid = Card._seq
        self.card_id = card_id
        self.data = catalog[card_id]

    def __deepcopy__(self, memo):
        c = Card.__new__(Card)
        c.uid = self.uid; c.card_id = self.card_id; c.data = self.data  # share immutable card text
        return c

    @property
    def name(self): return self.data["name"]
    @property
    def supertype(self): return self.data["supertype"]
    @property
    def subtypes(self): return self.data.get("subtypes", [])
    @property
    def types(self): return self.data.get("types", [])
    @property
    def hp(self): return self.data.get("hp", 0)
    @property
    def is_pokemon(self): return self.supertype == "Pokémon"
    @property
    def is_trainer(self): return self.supertype == "Trainer"
    @property
    def is_energy(self): return self.supertype == "Energy"
    @property
    def is_basic_pokemon(self): return self.is_pokemon and "Basic" in self.subtypes
    @property
    def has_rule_box(self): return self.data.get("rule_box", False)
    @property
    def evolves_from(self): return self.data.get("evolves_from")
    def provides(self): return self.data.get("provides", [self.types[0]] if self.types else ["Colorless"])
    def __repr__(self): return f"{self.name}({self.card_id}#{self.uid})"


class PokemonInPlay:
    """A Pokemon on the field, with its evolution stack, energy, tools, damage."""
    def __init__(self, card: Card):
        self.stack = [card]          # bottom..top; top is the current Pokemon
        self.energy: list[Card] = []
        self.tools: list[Card] = []
        self.damage = 0              # hit points of damage taken
        self.turn_played = None      # turn number it (or its current top) entered
        self.status = set()          # 'Asleep','Burned','Confused','Paralyzed','Poisoned'
        self.ability_used_this_turn = set()

    @property
    def card(self): return self.stack[-1]
    @property
    def name(self): return self.card.name
    @property
    def max_hp(self):
        bonus = 0
        for t in self.tools:
            hb = t.data.get("hp_bonus", 0) or 0
            pref = t.data.get("hp_bonus_prefix")
            if hb and (not pref or self.name.startswith(pref)):
                bonus += hb
        for e in self.energy:
            hb = e.data.get("hp_bonus", 0) or 0
            if hb:
                need = e.data.get("hp_bonus_type")
                if not need or need in self.card.types:
                    bonus += hb
        return self.card.hp + bonus
    @property
    def remaining_hp(self): return self.max_hp - self.damage
    @property
    def is_knocked_out(self): return self.damage >= self.max_hp
    @property
    def is_ex(self): return "ex" in self.card.subtypes
    @property
    def is_tera(self): return "Tera" in self.card.subtypes
    def attached_cards(self):
        return self.energy + self.tools + self.stack
    def energy_count(self):
        # counts energy provided (special energy may provide >1 in real cards; here 1 each)
        total = []
        for e in self.energy:
            total += e.provides()
        return total
    def prize_value(self):
        if any("Mega" in c.subtypes for c in self.stack):
            return 3
        return 2 if self.has_rule_box() else 1
    def has_rule_box(self):
        return any(c.has_rule_box for c in self.stack)
    def __repr__(self):
        return f"<{self.name} {self.remaining_hp}/{self.max_hp}hp E:{len(self.energy)}>"


# --------------------------------------------------------------------------
# Player & game state
# --------------------------------------------------------------------------
class Player:
    def __init__(self, name: str, deck: list[Card]):
        self.name = name
        self.deck: list[Card] = deck
        self.hand: list[Card] = []
        self.discard: list[Card] = []
        self.prizes: list[Card] = []
        self.active: Optional[PokemonInPlay] = None
        self.bench: list[PokemonInPlay] = []
        self.lost_zone: list[Card] = []
        # per-turn flags
        self.energy_attached_this_turn = False
        self.supporter_played_this_turn = False
        self.retreated_this_turn = False
        self.pokemon_ko_last_opp_turn = 0  # set by opponent's turn
        self.item_locked = False           # set by opponent (Budew)
        self.briar_active = False          # Briar prize bonus armed this turn
        self.fighting_boost = 0            # Premium Power Pro (+dmg this turn)
        self.ex_boost = 0                  # Black Belt's Training (+dmg vs ex this turn)

    def all_pokemon(self):
        return ([self.active] if self.active else []) + list(self.bench)

    def shuffle(self, rng): rng.shuffle(self.deck)

    def draw(self, n=1):
        drawn = []
        for _ in range(n):
            if not self.deck: break
            drawn.append(self.deck.pop(0))
        self.hand += drawn
        return drawn


class Game:
    STATUSES = {"Asleep", "Burned", "Confused", "Paralyzed", "Poisoned"}

    def __init__(self, p1: Player, p2: Player, seed: int | None = None, log=None):
        self.players = [p1, p2]
        self.rng = random.Random(seed)
        self.turn = 0
        self.active_idx = 0
        self.winner: Optional[Player] = None
        self.stadium: Optional[Card] = None
        self.stadium_owner: Optional[Player] = None
        self.log_lines: list[str] = []
        self._log_cb = log
        self._legacy_used = set()
        self._dmg_source = "attack"
        self.win_reason = ""

    # -- logging -----------------------------------------------------------
    def log(self, msg):
        line = f"T{self.turn} | {msg}"
        self.log_lines.append(line)
        if self._log_cb: self._log_cb(line)

    @property
    def current(self): return self.players[self.active_idx]
    @property
    def opponent(self): return self.players[1 - self.active_idx]

    # -- setup -------------------------------------------------------------
    def setup(self):
        first = self.rng.randint(0, 1)
        self.active_idx = first
        self.log(f"Coin flip: {self.players[first].name} goes first.")
        for p in self.players:
            self._setup_player_hand(p)
        # place active + bench basics, lay 6 prizes
        for p in self.players:
            basics = [c for c in p.hand if c.is_basic_pokemon]
            # human-like lead: open with an item-lock basic (Budew) when you have one
            def _is_lock(c):
                return any(e.get("op") == "opponent_item_lock_next_turn"
                           for a in c.data.get("attacks", []) for e in a.get("effects", []))
            lead = next((c for c in basics if _is_lock(c)), basics[0])
            basics.remove(lead); basics.insert(0, lead)
            p.active = PokemonInPlay(basics[0]); p.hand.remove(basics[0])
            p.active.turn_played = 0
            for b in basics[1:6]:
                pip = PokemonInPlay(b); pip.turn_played = 0
                p.bench.append(pip); p.hand.remove(b)
            for _ in range(6):
                if p.deck: p.prizes.append(p.deck.pop(0))
            self.log(f"{p.name} opens with {p.active.name} active, "
                     f"{len(p.bench)} on bench, {len(p.prizes)} prizes.")

    def _setup_player_hand(self, p: Player):
        mulligans = 0
        while True:
            p.deck += p.hand; p.hand = []
            p.shuffle(self.rng)
            p.draw(7)
            if any(c.is_basic_pokemon for c in p.hand):
                break
            mulligans += 1
            self.log(f"{p.name} mulligans (no Basic). #{mulligans}")
            if mulligans > 15:  # safety
                break
        if mulligans:
            # opponent may draw 1 per mulligan (simplified: draw at setup end)
            self._pending_mulligan_draw = getattr(self, "_pending_mulligan_draw", {})

    # -- turn loop ---------------------------------------------------------
    def start_turn(self):
        self.turn += 1
        p = self.current
        p.energy_attached_this_turn = False
        p.supporter_played_this_turn = False
        p.retreated_this_turn = False
        p.stadium_switch_used = False; p.garden_used = False; p.th_bonus = 0; p.grand_tree_used = False; p.levincia_used = False
        for _m in p.all_pokemon(): _m._reduce_next = 0
        for mon in p.all_pokemon():
            mon.ability_used_this_turn = set()
            mon._dodge_active = False
            mon._came_active = False
        self.log(f"--- {p.name}'s turn ---")
        # draw step (skip for very first turn's player? No: first player draws too in SV)
        if not p.deck:
            self.win(self.opponent, reason=f"{p.name} cannot draw")
            return False
        drew = p.draw(1)
        self.log(f"{p.name} draws ({len(p.hand)} in hand).")
        return True

    def end_turn(self):
        # between-turns: simplified status checks (Poison/Burn) on active
        p = self.current
        if self.stadium and self.stadium.data.get("stadium_kind") == "festival_grounds":
            for pl in self.players:
                for m in pl.all_pokemon():
                    if m.energy and m.status:
                        m.status = set()
        # Powerglass: at end of your turn, Active holder pulls a Basic Energy from discard
        if p.active and any(t.data.get("tool_kind") == "powerglass" for t in p.active.tools):
            en = next((c for c in p.discard if c.is_energy and "Basic" in c.subtypes), None)
            if en:
                p.discard.remove(en); p.active.energy.append(en)
                self.log(f"Powerglass attaches {en.name} to {p.active.name}.")
        for mon in [p.active]:
            if not mon: continue
            if "Poisoned" in mon.status:
                extra = 0
                other = self.players[1 - self.players.index(p)]
                if other.active:
                    for ab in other.active.card.data.get("abilities", []):
                        if ab.get("static_kind") == "toxic_subjugation" and self.abilities_enabled(other.active):
                            extra = 50
                mon.damage += 10 + extra
                self.log(f"{mon.name} takes {10+extra} from Poison" + (" (Toxic Subjugation)" if extra else "") + ".")
            if "Burned" in mon.status:
                mon.damage += 20; self.log(f"{mon.name} takes 20 from Burn.")
        for mon in p.all_pokemon():
            for e in list(mon.energy):
                if e.data.get("discard_end_of_turn"):
                    mon.energy.remove(e); p.discard.append(e)
                    self.log(f"{e.name} on {mon.name} is discarded (end of turn).")
        # Froslass "Freezing Shroud": Pokemon Checkup damages Ability-holders
        shroud = any(ab.get("static_kind") == "freezing_shroud"
                     for pl in self.players for m in pl.all_pokemon()
                     for ab in m.card.data.get("abilities", []) if self.abilities_enabled(m))
        if shroud:
            for pl in self.players:
                for m in pl.all_pokemon():
                    if m.card.name != "Froslass" and m.card.data.get("abilities"):
                        m.damage += 10
            self.log("  Freezing Shroud places a damage counter on each Ability Pokemon.")
        self.check_kos()
        # reset opponent's "ko last turn" tracker handoff
        self.opponent.pokemon_ko_last_opp_turn = 0
        p.item_locked = False  # the lock only applied for this turn
        p.briar_active = False
        p.fighting_boost = 0
        p.ex_boost = 0
        self.active_idx = 1 - self.active_idx

    # -- core primitives used by effects ----------------------------------
    def bench_limit(self, p):
        if self.stadium and self.stadium.data.get("stadium_kind") == "tera_bench_8":
            if any(m.is_tera for m in p.all_pokemon()):
                return 8
        return 5
    def bench_has_room(self, p): return len(p.bench) < self.bench_limit(p)

    def attach_energy(self, p, energy_card: Card, target: PokemonInPlay):
        if p.energy_attached_this_turn:
            raise RuleError("Already attached Energy this turn.")
        p.hand.remove(energy_card)
        target.energy.append(energy_card)
        p.energy_attached_this_turn = True
        self.log(f"{p.name} attaches {energy_card.name} to {target.name}.")
        n = energy_card.data.get("draw_on_attach")
        if n:
            p.draw(n); self.log(f"  {energy_card.name}: draws {n}.")

    def play_basic_to_bench(self, p, card: Card):
        if not self.bench_has_room(p): raise RuleError("Bench full.")
        pip = PokemonInPlay(card); pip.turn_played = self.turn
        p.bench.append(pip); p.hand.remove(card)
        self.log(f"{p.name} benches {card.name}.")
        self.after_bench(p, pip)
        return pip

    def evolve(self, p, evo_card: Card, target: PokemonInPlay, rare_candy=False):
        # timing checks
        if target.turn_played == self.turn:
            grass_rush = (self.stadium and self.stadium.data.get("stadium_kind") == "grass_rush_evo"
                          and self.turn > 1 and "Grass" in target.card.types and "Grass" in evo_card.types)
            if not grass_rush:
                raise RuleError("Cannot evolve a Pokemon that came into play this turn.")
        if self.turn <= 1 and not rare_candy_allowed_first_turn():
            pass
        expected_from = evo_card.evolves_from
        if not rare_candy and target.name != expected_from:
            raise RuleError(f"{evo_card.name} does not evolve from {target.name}.")
        p.hand.remove(evo_card)
        prev_top = target.card
        target.stack.append(evo_card)
        target.turn_played = self.turn
        target.status = set()  # special conditions end on evolution
        self.log(f"{p.name} evolves {prev_top.name} -> {evo_card.name}"
                 + (" (Rare Candy)" if rare_candy else ""))
        return prev_top

    def retreat(self, p, bench_index: int):
        if p.retreated_this_turn: raise RuleError("Already retreated this turn.")
        cost = p.active.card.data.get("retreat", 0)
        if self.tools_active():
            cost = max(0, cost - sum(t.data.get("retreat_reduction", 0) for t in p.active.tools))
        # Latias ex Skyliner: your Basic Pokemon have no Retreat Cost
        if "Basic" in p.active.card.subtypes:
            for m in p.all_pokemon():
                for ab in m.card.data.get("abilities", []):
                    if ab.get("static_kind") == "basics_no_retreat" and self.abilities_enabled(m):
                        cost = 0
        # N's Castle: N's Pokemon have no Retreat Cost
        if self.stadium and self.stadium.data.get("stadium_kind") == "ns_no_retreat" and "N's" in p.active.card.name:
            cost = 0
        if len(p.active.energy) < cost: raise RuleError("Not enough Energy to retreat.")
        for _ in range(cost):
            p.discard.append(p.active.energy.pop())
        p.active.status = set()
        p.bench.append(p.active)
        p.active = p.bench.pop(bench_index)
        p.retreated_this_turn = True
        p.active._came_active = True
        self.log(f"{p.name} retreats; {p.active.name} is now Active.")

    # -- attacking ---------------------------------------------------------
    def attacks_for(self, mon):
        """Effective attacks: a Pokemon's own attacks plus, if a Memory Dive (Relicanth)
        enabler is in the same player's play AND this Pokemon is evolved, the attacks of its
        previous Evolutions. Own attacks come first so existing attack indices never shift.
        Energy cost is still checked separately when the attack is used."""
        own = list(mon.card.data.get("attacks", []) or [])
        if not mon.card.data.get("evolves_from"):
            return own
        owner = next((p for p in self.players if mon in p.all_pokemon()), None)
        if owner is None:
            return own
        has_md = any(ab.get("static_kind") == "memory_dive" and self.abilities_enabled(m)
                     for m in owner.all_pokemon()
                     for ab in m.card.data.get("abilities", []) or [])
        if not has_md:
            return own
        idx = _name_attacks_index()
        seen = {a["name"] for a in own}; borrowed = []
        ef = mon.card.data.get("evolves_from"); guard = set()
        while ef and ef not in guard:
            guard.add(ef)
            slot = idx.get(ef)
            if not slot: break
            for an, atk in slot["attacks"].items():
                if an not in seen:
                    seen.add(an); borrowed.append(atk)
            ef = slot.get("evolves_from")
        return own + borrowed

    def attack(self, attacker_player, attack_index: int, effects_engine):
        atk_mon = attacker_player.active
        atk = self.attacks_for(atk_mon)[attack_index]
        # first player may not attack on the very first turn of the game
        if self.turn == 1:
            raise RuleError("The player going first cannot attack on turn 1.")
        for ab in atk_mon.card.data.get("abilities", []):
            if ab.get("static_kind") == "needs_4_team_rocket" and self.abilities_enabled(atk_mon):
                if sum(1 for m in attacker_player.all_pokemon() if "Team Rocket's" in m.card.name) < 4:
                    raise RuleError("Power Saver: need 4+ Team Rocket's Pokemon in play to attack.")
        if not self._cost_met(atk_mon, atk["cost"], atk):
            raise RuleError(f"Energy cost not met for {atk['name']}.")
        self.log(f"{attacker_player.name}'s {atk_mon.name} uses {atk['name']}.")
        base = atk.get("damage", 0) or 0
        self._atk_bonus = 0
        self._ignore_wr = False
        self._ignore_effects = False
        self._dmg_source = "attack"
        # structured non-damage / special-damage effects
        effects_engine.run_attack(self, attacker_player, atk_mon, atk)
        total = base + getattr(self, "_atk_bonus", 0)
        if total:
            self.deal_attack_damage(atk_mon, self.opponent.active, total, effects_engine,
                                    attacker_player)
        self.check_kos()

    def deal_attack_damage(self, atk_mon, def_mon, amount, effects_engine, atk_player):
        if def_mon is None: return
        if self.damage_prevented(def_mon, atk_mon, atk_player):
            self.log(f"  damage to {def_mon.name} is prevented.")
            return
        dmg = amount
        # Pre-Weakness boosts: Premium Power Pro (Fighting) and Maximum Belt (vs ex)
        if dmg > 0:
            if atk_player.fighting_boost and "Fighting" in atk_mon.card.types and def_mon is self.opponent.active:
                dmg += atk_player.fighting_boost
            if "ex" in def_mon.card.subtypes and self.tools_active():
                dmg += sum(t.data.get("damage_boost_vs_ex", 0) for t in atk_mon.tools)
                if not atk_mon.card.has_rule_box:
                    dmg += sum(t.data.get("damage_boost_vs_ex_no_rulebox", 0) for t in atk_mon.tools)
            if "Hop's" in atk_mon.card.name and def_mon is self.opponent.active:
                if self.stadium and self.stadium.data.get("stadium_kind") == "postwick_boost":
                    dmg += 30
                if any(ab.get("static_kind") == "extra_helpings"
                       for m in atk_player.all_pokemon() for ab in m.card.data.get("abilities", [])
                       if self.abilities_enabled(m)):
                    dmg += 30
                if self.tools_active():
                    dmg += sum(t.data.get("damage_boost", 0) for t in atk_mon.tools if t.data.get("hops_choice_band"))
            if def_mon is self.opponent.active and atk_mon.card.name.startswith("Cynthia's"):
                if any(ab.get("static_kind") == "cynthia_cheer"
                       for m in atk_player.all_pokemon() for ab in m.card.data.get("abilities", [])
                       if self.abilities_enabled(m)):
                    dmg += 30
            if "ex" in def_mon.card.subtypes and atk_player.ex_boost and def_mon is self.opponent.active:
                dmg += atk_player.ex_boost
            if getattr(atk_player, "th_bonus", 0) and def_mon is self.opponent.active:
                dmg += atk_player.th_bonus
            if def_mon is self.opponent.active and \
               any(t in ("Grass", "Fire") for t in atk_mon.card.types):
                for m in atk_player.all_pokemon():
                    for ab in m.card.data.get("abilities", []):
                        if ab.get("static_kind") == "sunny_day" and self.abilities_enabled(m):
                            dmg += ab.get("amount", 20)
            if self.tools_active() and "Poisoned" in atk_mon.status and def_mon is self.opponent.active:
                dmg += sum(t.data.get("poison_damage_boost", 0) for t in atk_mon.tools)
        # Weakness / Resistance on the Active (defending) Pokemon
        if not getattr(self, "_ignore_wr", False):
            weak = def_mon.card.data.get("weakness")
            resist = def_mon.card.data.get("resistance")
            atype = atk_mon.card.types[0] if atk_mon.card.types else "Colorless"
            # Lillie's Clefairy ex Fairy Zone: opponent's Dragon Pokemon are weak to Psychic
            if "Dragon" in def_mon.card.types and self._fairy_zone_active(atk_player):
                weak = "Psychic"
            if weak and weak == atype: dmg *= 2
            if resist and resist == atype: dmg = max(0, dmg - 30)
        dmg = max(0, dmg - self.damage_reduction(def_mon))
        def_mon.damage += dmg
        self.log(f"  {def_mon.name} takes {dmg} ({def_mon.remaining_hp}/{def_mon.max_hp} left).")
        # defender tool triggers (Handheld Fan / Lucky Helmet) fire on being damaged while Active
        effects_engine.trigger_on_damaged(self, def_mon, atk_mon, owner=self.opponent,
                                          attacker_owner=atk_player)

    def _energy_pool(self, mon):
        """Energy a Pokemon provides, accounting for Meganium's Wild Growth."""
        try:
            owner = next(p for p in self.players
                         if mon is p.active or mon in p.bench)
        except StopIteration:
            owner = None
        wild = False
        if owner:
            for m in owner.all_pokemon():
                for ab in m.card.data.get("abilities", []):
                    if ab.get("static_kind") == "basic_grass_provides_double" and self.abilities_enabled(m):
                        wild = True
        pool = []
        for e in mon.energy:
            ev = e.data.get("evolution_provides")
            if e.data.get("stage2_any2") and "Stage 2" in mon.card.subtypes:
                provs = ["Any", "Any"]
            elif ev and any(st in mon.card.subtypes for st in ("Stage 1", "Stage 2")):
                provs = ev
            elif e.data.get("prism_energy"):
                provs = ["Any"] if "Basic" in mon.card.subtypes else ["Colorless"]
            else:
                provs = e.provides()
            for t in provs:
                pool.append(t)
                if wild and "Basic" in e.subtypes and t == "Grass":
                    pool.append("Grass")  # Wild Growth: basic G provides GG
        return pool

    def _cost_met(self, mon, cost, attack=None):
        pool = self._energy_pool(mon)
        need = list(cost)
        for req in [c for c in need if c != "Colorless"]:
            if req in pool: pool.remove(req)
            elif "Any" in pool: pool.remove("Any")
            else: return False
        colorless = sum(1 for c in need if c == "Colorless")
        if self.stadium and self.stadium.card_id == "ASC-197" and mon.is_tera:
            colorless += self.stadium.data["effects"][0]["amount"]
        # cost reductions
        red = 0
        if self.tools_active() and "Hop's" in mon.card.name:
            red += sum(t.data.get("cost_reduction", 0) for t in mon.tools if t.data.get("hops_choice_band"))
        if attack and attack.get("name") == "Blood Moon":
            if any(ab.get("static_kind") == "seasoned_skill" for ab in mon.card.data.get("abilities", [])):
                try:
                    opp = next(p for p in self.players if mon not in p.all_pokemon())
                    red += (6 - len(opp.prizes))
                except StopIteration:
                    pass
        if self.tools_active():
            cg = sum(t.data.get("counter_gain_reduction", 0) for t in mon.tools)
            if cg:
                try:
                    me = next(p for p in self.players if mon in p.all_pokemon())
                    opp = next(p for p in self.players if p is not me)
                    if len(me.prizes) > len(opp.prizes): red += cg
                except StopIteration:
                    pass
        colorless = max(0, colorless - red)
        return len(pool) >= colorless

    def abilities_enabled(self, mon):
        """Team Rocket's Watchtower turns off Abilities of [C] Pokemon."""
        if self.stadium and self.stadium.data.get("stadium_kind") == "colorless_no_abilities":
            if "Colorless" in mon.card.types:
                return False
        return True

    def self_ko_abilities_disabled(self):
        """Psyduck's Damp disables Abilities that Knock Out the user (e.g. Cursed Blast)."""
        for p in self.players:
            for m in p.all_pokemon():
                for ab in m.card.data.get("abilities", []):
                    if ab.get("name") == "Damp":
                        return True
        return False

    def after_bench(self, player, pip):
        """Risky Ruins: placing a Basic non-[D] Pokemon on the Bench damages it."""
        if self.stadium and self.stadium.data.get("stadium_kind") == "bench_punish_non_dark":
            if "Basic" in pip.card.subtypes and "Darkness" not in pip.card.types:
                pip.damage += 20
                self.log(f"  Risky Ruins puts 2 damage counters on {pip.name}.")

    def damage_reduction(self, defender):
        """Static 'takes N less damage from attacks' (Exoskeleton). Attack damage only;
        placed counters bypass it."""
        if getattr(self, "_dmg_source", "attack") != "attack":
            return 0
        red = getattr(defender, "_reduce_next", 0)   # Protect Charge etc.
        for ab in defender.card.data.get("abilities", []):
            if ab.get("static_kind") == "reduce_damage_taken" and self.abilities_enabled(defender):
                red += ab.get("amount", 0)
        return red

    def damage_prevented(self, defender, attacker, attacker_player):
        """Damage-prevention from ATTACKS only. Counters placed by Abilities/Trainers
        (Mortal Shuriken, Munkidori, poison, etc.) are NOT 'damage from an attack' and
        therefore bypass every prevention below."""
        if getattr(self, "_dmg_source", "attack") != "attack":
            return False
        owner2 = next((pl for pl in self.players if defender in pl.bench), None)
        if owner2 is not None:
            for ab in defender.card.data.get("abilities", []):
                if ab.get("static_kind") == "bench_attack_immunity" and self.abilities_enabled(defender):
                    self.log(f"  {defender.name} is protected on the Bench (Ability).")
                    return True
        for ab in defender.card.data.get("abilities", []):
            if ab.get("static_kind") == "flip_prevent_attack_damage" and self.abilities_enabled(defender):
                if self.rng.random() < 0.5:
                    self.log(f"  (Ability) {ab.get('name','Smooth Coat')}: heads — damage prevented!")
                    return True
        if getattr(defender, "_dodge_active", False):
            return True
        try:
            owner = next(p for p in self.players
                         if defender is p.active or defender in p.bench)
        except StopIteration:
            owner = None
        # Tera bench immunity (Cornerstone Mask Ogerpon ex while Benched)
        if owner and defender in owner.bench and defender.card.data.get("tera_bench_immunity"):
            return True
        for ab in defender.card.data.get("abilities", []):
            kind = ab.get("static_kind")
            if kind == "prevent_damage_from_ex" and "ex" in attacker.card.subtypes:
                return True
            if kind == "prevent_damage_from_ability_mon" and attacker.card.data.get("abilities"):
                return True
        # Rabsca's Spherical Shield: prevent opponent's ATTACK damage/effects to your Bench
        if owner and attacker_player is not owner and defender in owner.bench:
            for m in owner.all_pokemon():
                for ab in m.card.data.get("abilities", []):
                    if ab.get("static_kind") == "spherical_shield" and self.abilities_enabled(m):
                        return True
        # Shaymin's Flower Curtain: protect own Benched non-rule-box Pokemon
        if owner and defender in owner.bench and not defender.has_rule_box():
            for m in owner.all_pokemon():
                for ab in m.card.data.get("abilities", []):
                    if ab.get("static_kind") == "protect_bench_no_rulebox" and self.abilities_enabled(m):
                        return True
        return False

    def _fairy_zone_active(self, player):
        for m in player.all_pokemon():
            for ab in m.card.data.get("abilities", []):
                if ab.get("static_kind") == "opp_dragon_weakness_psychic" and self.abilities_enabled(m):
                    return True
        return False

    def bench_counters_blocked(self, target):
        """Battle Cage: no damage counters placed on a Benched Pokemon by the opponent's effects."""
        if self.stadium and self.stadium.data.get("stadium_kind") == "block_bench_counters":
            try:
                owner = next(p for p in self.players if target is p.active or target in p.bench)
            except StopIteration:
                owner = None
            if owner and target in owner.bench:
                return True
        return False

    # -- KOs, prizes, win conditions --------------------------------------
    def tools_active(self):
        return not (self.stadium and self.stadium.data.get("stadium_kind") == "tools_disabled")

    def effective_max_hp(self, mon):
        hp = mon.max_hp
        if not self.tools_active():
            hp -= sum(t.data.get("hp_bonus", 0) for t in mon.tools)
        if self.stadium and self.stadium.data.get("stadium_kind") == "stage2_minus30":
            if "Stage 2" in mon.card.subtypes:
                hp -= 30
        return hp

    def check_kos(self):
        for pi, p in enumerate(self.players):
            for mon in list(p.all_pokemon()):
                if mon and mon.damage >= self.effective_max_hp(mon):
                    self._knock_out(p, mon)

    def _knock_out(self, owner, mon):
        taker = self.players[1 - self.players.index(owner)]
        n = mon.prize_value()
        for en in mon.energy:
            if en.data.get("legacy_prize") and owner.name not in self._legacy_used:
                n = max(0, n - 1); self._legacy_used.add(owner.name)
                self.log("  Legacy Energy: opponent takes 1 fewer Prize.")
                break
        for t in (mon.tools if self.tools_active() else []):
            pr = t.data.get("prize_reduction", 0)
            if pr and mon.name.startswith(t.data.get("holder_prefix", "")):
                n = max(0, n - pr)
                self.log(f"  {t.name}: opponent takes {pr} fewer Prize(s).")
        self.log(f"{mon.name} is Knocked Out! {taker.name} takes {n} prize(s).")
        # move card(s) to discard
        for c in mon.attached_cards(): owner.discard.append(c)
        if mon is owner.active: owner.active = None
        elif mon in owner.bench: owner.bench.remove(mon)
        # track KO for Fezandipiti "Flip the Script"
        owner.pokemon_ko_last_opp_turn += 1
        # take prizes
        if getattr(taker, "briar_active", False):
            n += 1; taker.briar_active = False
            self.log("  Briar: take 1 extra Prize card.")
        for _ in range(n):
            if taker.prizes:
                taker.hand.append(taker.prizes.pop())
        if not taker.prizes:
            self.win(taker, reason="took all prizes"); return
        # promote a benched Pokemon if active was KO'd.
        # Human pattern (the Munkidori-wall play): prefer a body that SURVIVES the
        # opponent's best available hit — denying prize tempo — and among
        # survivors prefer damage-launderers (Adrena-Brain ammo) over engine pieces.
        if owner.active is None:
            if owner.bench:
                opp = self.players[1 - self.players.index(owner)]
                threat = 60
                if opp.active:
                    for a in opp.active.card.data.get("attacks", []):
                        amt = a.get("damage") or 0
                        for e in a.get("effects", []) or []:
                            amt = max(amt, e.get("amount", 0) or 0)
                        threat = max(threat, amt)
                def promo_key(m):
                    hp_left = (m.card.hp or 0) - m.damage
                    survives = hp_left > threat
                    launders = any(e.get("op") == "move_damage_counters_to_opponent"
                                   for ab in m.card.data.get("abilities", [])
                                   for e in ab.get("effects", []))
                    ready = any(self._cost_met(m, a["cost"], a)
                                for a in m.card.data.get("attacks", []))
                    pz = 3 if "Mega" in m.card.subtypes else (2 if "ex" in m.card.subtypes else 1)
                    return (survives, ready, survives and launders, -pz, hp_left)
                pick = max(owner.bench, key=promo_key)
                owner.bench.remove(pick); owner.active = pick
                self.log(f"{owner.name} promotes {owner.active.name} to Active.")
            else:
                self.win(taker, reason=f"{owner.name} has no Pokemon in play")

    def win(self, player, reason=""):
        if self.winner: return
        self.winner = player
        self.win_reason = reason
        self.log(f"*** {player.name} WINS — {reason} ***")


class RuleError(Exception):
    pass

def rare_candy_allowed_first_turn(): return False
