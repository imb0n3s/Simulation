"""
Headless game runner / demo.

Loads a deck list, instantiates real Card objects from cards.json, sets up a
match, and plays it with a simple heuristic agent so you can watch the rules
engine and every card's effect fire end-to-end. Prints a full game log.

Usage:
    python simulate.py                       # mirror match, fixed seed
    python simulate.py --seed 7 --max-turns 30
    python simulate.py --deck decks/alakazam_dudunsparce.txt
"""
import argparse, json, os, re, sys
from engine import Game, Player, Card, PokemonInPlay, RuleError, load_catalog
from effects import Effects

HERE = os.path.dirname(os.path.abspath(__file__))
CAT = load_catalog()
LINE_RE = re.compile(r"^(\d+)\s+(.*?)\s+([A-Z]{2,4})\s+(\w+)$")

def build_deck(path):
    cards = []
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line.split(":")[0] in ("Pokémon", "Pokemon", "Trainer", "Energy") and ":" in line:
            continue
        m = LINE_RE.match(line)
        if not m: continue
        n, _, setc, num = m.groups()
        cid = f"{setc}-{num}"
        if cid not in CAT:
            print("skip (not in catalog):", line); continue
        for _ in range(int(n)):
            cards.append(Card(cid, CAT))
    return cards


class HeuristicAgent:
    """Plays a turn with a fixed priority order. Best-effort, never crashes."""
    def __init__(self, fx: Effects):
        self.fx = fx
        self._skip_ops = set()  # op names whose trainer cards dev should defer

    def _try(self, fn):
        try: fn(); return True
        except RuleError: return False
        except Exception as e:  # keep the demo robust
            return False

    def _defer(self, card):
        # hand-disruption bullets (Special Red Card / Unfair Stamp): vs a
        # Powerful-Hand-style scaler, fire only when their hand is BIG (>=7);
        # vs everyone else keep the old behavior (play freely).
        if any(e.get("op") in ("opponent_hand_to_bottom_draw", "unfair_stamp")
               for e in card.data.get("effects", [])):
            p = getattr(self, "_cur_player", None)
            g = getattr(self, "_cur_game", None)
            if p is not None and g is not None and self._opp_own_hand_scaler(g, p):
                opp = g.players[1 - g.players.index(p)]
                if len(opp.hand) < 7:
                    return True   # hold the bullet until it deletes a real hand
        # never switch our own ready attacker out with a Switch-style item
        if any(e.get("op") == "switch_self_with_bench" for e in card.data.get("effects", [])):
            act = getattr(self, "_cur_player", None) and self._cur_player.active
            if act and any((a.get("damage") or 0) >= 50 for a in act.card.data.get("attacks", [])) \
               and not self._is_utility_lead(act):
                return True
        if not self._skip_ops: return False
        return any(e.get("op") in self._skip_ops for e in card.data.get("effects", []))

    @staticmethod
    def _is_lock_attack(atk):
        return any(e.get("op") == "opponent_item_lock_next_turn" for e in atk.get("effects", []))

    def _is_utility_lead(self, mon):
        atks = mon.card.data.get("attacks", [])
        return (mon.card.hp or 0) <= 40 and any(self._is_lock_attack(x) for x in atks)

    @staticmethod
    def _best_dmg(mon):
        atks = mon.card.data.get("attacks", [])
        return max(((x.get("damage") or 0) for x in atks), default=0)

    @staticmethod
    def _threat_next_turn(mon, opp_board=None):
        """Damage the opponent's Active can plausibly do next turn (they attach +1).
        Understands scaling attacks like Rumbling Bees (damage-per-named-in-play)."""
        if not mon: return 0
        e = len(mon.energy) + 1
        best = 0
        for x in mon.card.data.get("attacks", []):
            if len(x.get("cost", [])) > e: continue
            d = x.get("damage") or 0
            for ef in x.get("effects", []):
                if ef.get("op") == "damage_per_named_in_play" and opp_board is not None:
                    d += ef.get("amount", 0) * sum(1 for m in opp_board if ef.get("name","") in m.card.name)
                elif ef.get("op") in ("damage_per_own_bench",):
                    d += ef.get("amount", 0) * 3   # rough
            best = max(best, d)
        return best

    def _active_is_valuable(self, game, p):
        m = p.active
        if m is None: return False
        try: pv = m.prize_value()
        except Exception: pv = 2 if "ex" in m.card.subtypes else 1
        evolving = any(c.is_pokemon and c.evolves_from == m.card.name for c in p.hand)
        return pv >= 2 or evolving or len(m.energy) >= 2 or self._best_dmg(m) >= 100

    RESET_OPS = {"shuffle_hand_draw", "discard_hand_draw", "each_player_shuffle_draw",
                 "discard_whole_hand", "lacey_draw"}

    def _keeper_value(self, game, p):
        """What a reset would really cost: keepers weighted by scarcity.
        A keeper with 2+ copies still in deck is cheap to shuffle away (0.4);
        the last copy is precious (1.0)."""
        in_play = {m.card.name for m in p.all_pokemon()}
        v = 0.0
        for c in p.hand:
            if c.is_pokemon and c.evolves_from in in_play:
                left = sum(1 for d in p.deck if d.name == c.name)
                v += 0.4 if left >= 2 else 1.0
        if any(c.card_id == "MEG-125" for c in p.hand) and \
           any(c.is_pokemon and "Stage 2" in c.subtypes for c in p.hand):
            v += 0.6
        if self._opp_own_hand_scaler(game, p):
            # the Alakazam lesson: hand disruption is the answer — never reset it away
            for c in p.hand:
                if c.is_trainer and any(e.get("op") in self._DISRUPT_OPS
                                        for e in c.data.get("effects", [])):
                    v += 1.2
        return v

    def _racing_pressure(self, game, p):
        """How badly we need raw volume RIGHT NOW (0 = even, higher = dig)."""
        opp = game.players[1 - game.players.index(p)]
        pr = len(p.prizes) - len(opp.prizes)          # >0 means they are ahead on prizes
        def ready(side):
            return max((self._best_dmg(m) for m in side.all_pokemon()
                        if any(game._cost_met(m, x["cost"], x)
                               for x in m.card.data.get("attacks", []))), default=0)
        pressure = max(0, pr)
        if ready(opp) >= 100 and ready(p) < 60: pressure += 1   # losing the board race
        if len(opp.prizes) <= 2: pressure += 2                  # they are closing — find outs NOW
        return pressure

    def _play_supporter(self, game, p):
        if p.supporter_played_this_turn: return
        sups = [c for c in p.hand if c.is_trainer and "Supporter" in c.subtypes and not self._defer(c)]
        if not sups: return
        def ops(c): return {e.get("op") for e in c.data.get("effects", [])}
        gusts    = [c for c in sups if "gust_opponent_bench" in ops(c)]
        resets   = [c for c in sups if ops(c) & self.RESET_OPS]
        additive = [c for c in sups if c not in gusts and c not in resets]
        # 1) take a conversion gust when it's there
        if gusts and p.active:
            opp = game.players[1 - game.players.index(p)]
            best = self._best_dmg(p.active)
            def rem(m):
                try: return game.effective_max_hp(m) - m.damage
                except Exception: return m.max_hp - m.damage
            if any(0 < rem(m) <= best for m in opp.bench):
                self._try(lambda c=gusts[0]: self.fx.play_trainer(game, p, c)); return
        # 1.5) Refrain defence: every card held is 50 incoming — never balloon the hand
        if self._opp_hand_punisher(game, p):
            if additive:
                self._try(lambda c=additive[0]: self.fx.play_trainer(game, p, c))
            elif resets and len(p.hand) <= 2:
                self._try(lambda c=resets[0]: self.fx.play_trainer(game, p, c))
            return
        # 1.6) the Alakazam lesson: vs a Powerful-Hand-style scaler, a held
        # disruption bullet (Special Red Card / Unfair Stamp) wins the late game.
        # Once their prizes are low enough that the bullet is (nearly) live,
        # NEVER reset it away — additive supporters only.
        if self._opp_own_hand_scaler(game, p):
            opp = game.players[1 - game.players.index(p)]
            has_bullet = any(c.is_trainer and any(e.get("op") in self._DISRUPT_OPS
                                                  for e in c.data.get("effects", []))
                             for c in p.hand)
            if has_bullet and len(opp.prizes) <= 4:
                if additive:
                    self._try(lambda c=additive[0]: self.fx.play_trainer(game, p, c))
                return
        # 2) balanced greed: dig when the race demands it, protect when the hand is working
        kv = self._keeper_value(game, p)
        pressure = self._racing_pressure(game, p)
        if resets and pressure - kv >= 1.0:
            # behind / they are closing / keepers replaceable -> volume is correct
            self._try(lambda c=resets[0]: self.fx.play_trainer(game, p, c)); return
        if kv >= 1.5:
            # hand is genuinely working: additive only, hold the reset
            if additive: self._try(lambda c=additive[0]: self.fx.play_trainer(game, p, c))
            return
        if additive:
            self._try(lambda c=additive[0]: self.fx.play_trainer(game, p, c))
        elif resets and (len(p.hand) <= 5 or pressure >= 1):
            self._try(lambda c=resets[0]: self.fx.play_trainer(game, p, c))
        elif resets:
            self._try(lambda c=resets[0]: self.fx.play_trainer(game, p, c))

    def _should_bench(self, game, p, card):
        """Humans bench with a plan: insurance, evolution lines, attackers, needed utility.
        They do NOT gift multi-prize liabilities or overfill the bench."""
        board = (1 if p.active else 0) + len(p.bench)
        if board <= 2: return True                       # insurance vs getting swept
        try: pv = 2 if "ex" in card.subtypes else 1
        except Exception: pv = 1
        evolving = any(c.is_pokemon and c.evolves_from == card.name for c in p.hand) or \
                   any(c.is_pokemon and c.evolves_from == card.name for c in p.deck)
        if evolving: return len(p.bench) < 5             # line piece: always part of the plan
        has_lock = any(self._is_lock_attack(x) for x in card.data.get("attacks", []))
        if (card.hp or 0) <= 40 and has_lock and game.turn > 3:
            return False                                  # late lock-lead = spread food
        has_util = bool(card.data.get("abilities"))
        best = max(((x.get("damage") or 0) for x in card.data.get("attacks", [])), default=0)
        if has_util:
            if pv == 1: return len(p.bench) < 4          # cheap engine (Munkidori etc.)
            # 2-prize utility (Meowth ex...): only when we actually need the gas
            return len(p.hand) <= 4 and len(p.bench) < 4
        if best >= 60: return len(p.bench) < 4           # genuine attacker
        return len(p.bench) < 2                          # pure filler only on a thin board

    def _sacrifice_index(self, p):
        """Cheapest expendable benched body: 1-prize, no abilities (keep engines), least invested."""
        best = None
        for i, m in enumerate(p.bench):
            try: pv = m.prize_value()
            except Exception: pv = 2 if "ex" in m.card.subtypes else 1
            if pv != 1: continue
            key = (1 if m.card.data.get("abilities") else 0, len(m.energy), self._best_dmg(m), m.card.hp or 0)
            if best is None or key < best[0]: best = (key, i)
        return best[1] if best else None

    def _opp_hand_punisher(self, game, p):
        """True when the OPPONENT has an affordable attacker that scales with OUR
        hand size (Resentful Refrain): every card we hoard is 50 damage incoming."""
        opp = game.players[1 - game.players.index(p)]
        for mon in opp.all_pokemon():
            for atk in mon.card.data.get("attacks", []):
                if any(e.get("op") == "damage_per_opponent_hand"
                       for e in atk.get("effects", []) or []) \
                   and game._cost_met(mon, atk["cost"], atk):
                    return True
        return False

    def _opp_own_hand_scaler(self, game, p):
        """True when the OPPONENT has an attacker that scales with THEIR OWN hand
        (Powerful Hand): their ballooning hand is incoming placed damage, and
        hand disruption (Special Red Card / Judge / Unfair Stamp / Lucian) is
        the counter-play."""
        opp = game.players[1 - game.players.index(p)]
        for mon in opp.all_pokemon():
            for atk in mon.card.data.get("attacks", []):
                for e in atk.get("effects", []) or []:
                    if e.get("op") in ("damage_per_own_hand", "place_counters_per_hand"):
                        return True
        return False

    _DISRUPT_OPS = ("opponent_hand_to_bottom_draw", "each_player_shuffle_draw",
                    "unfair_stamp", "lucian_reset")

    def _play_items(self, game, p):
        for card in list(p.hand):
            if card.is_trainer and "Item" in card.subtypes \
               and "Stadium" not in card.subtypes and "Tool" not in card.subtypes:
                if p.item_locked or self._defer(card):
                    continue
                if card in p.hand:
                    self._try(lambda c=card: self.fx.play_trainer(game, p, c))

    def take_turn(self, game: Game, p: Player):
        self._cur_player = p
        self._cur_game = game
        # 1) play Items (the Supporter waits until after draw abilities — info first)
        self._play_items(game, p)
        # 2) play a stadium if we have one and none is ours
        for card in list(p.hand):
            if card.is_trainer and "Stadium" in card.subtypes and game.stadium is None:
                self._try(lambda c=card: self.fx.play_trainer(game, p, c))
                break
        # 3) bench basics — deliberately, not by reflex
        for card in list(p.hand):
            if card.is_basic_pokemon and game.bench_has_room(p):
                if not self._should_bench(game, p, card):
                    continue
                if self._try(lambda c=card: game.play_basic_to_bench(p, c)):
                    benched = p.bench[-1]
                    self._try(lambda m=benched: self.fx.trigger_on_bench(game, p, m))
        # 4) evolve (normal lines) + Psychic Draw ability on evolve
        self._do_evolutions(game, p)
        # 5) Rare Candy
        for card in list(p.hand):
            if card.card_id == "MEG-125":
                self._try(lambda c=card: self.fx.play_trainer(game, p, c))
        self._do_evolutions(game, p)
        # 5.5) gather information BEFORE committing the supporter: use draw/search
        #      abilities first (Recon Directive, Last-Ditch Catch, Summoning Jutsu...)
        for mon in p.all_pokemon():
            for ab in mon.card.data.get("abilities", []):
                if ab.get("type") == "activated":
                    self._try(lambda m=mon, a=ab: self.fx.use_ability(game, p, m, a))
        # 5.6) NOW choose the one Supporter, with full knowledge of the hand
        self._play_supporter(game, p)
        # 5.7) second pass: use what the abilities/supporter found
        self._play_items(game, p)
        for card in list(p.hand):
            if card.is_basic_pokemon and game.bench_has_room(p) and self._should_bench(game, p, card):
                if self._try(lambda c=card: game.play_basic_to_bench(p, c)):
                    self._try(lambda m=p.bench[-1]: self.fx.trigger_on_bench(game, p, m))
        self._do_evolutions(game, p)
        for card in list(p.hand):
            if card.card_id == "MEG-125":
                self._try(lambda c=card: self.fx.play_trainer(game, p, c))
        self._do_evolutions(game, p)
        # 6) attach a tool to the active if useful
        for card in list(p.hand):
            if card.is_trainer and "Tool" in card.subtypes and p.active and not p.active.tools:
                self._try(lambda c=card: self.fx.attach_tool(game, p, c, p.active))
                break
        # 7) attach one energy where a human would: fund the lock-lead's retreat once,
        #    otherwise the strongest attacker that still needs energy
        for card in list(p.hand):
            if card.is_energy and not p.energy_attached_this_turn:
                tgt = p.active
                if p.active and self._is_utility_lead(p.active):
                    if len(p.active.energy) >= 1 and p.bench:
                        tgt = max(p.bench, key=lambda m: (self._best_dmg(m), len(m.energy)))
                elif p.active and p.bench:
                    need = max((len(x["cost"]) for x in p.active.card.data.get("attacks", [])), default=0)
                    if len(p.active.energy) >= need:
                        cands = [m for m in p.bench
                                 if len(m.energy) < max((len(x["cost"]) for x in m.card.data.get("attacks", [])), default=0)]
                        if cands:
                            tgt = max(cands, key=lambda m: self._best_dmg(m))
                self._try(lambda c=card, t=tgt: self.fx.attach_energy(game, p, c, t))
        # 7.5) pivot the utility lead out once our attacker is ready (human Budew line)
        if p.active and self._is_utility_lead(p.active) and p.bench and not p.retreated_this_turn:
            ready = [i for i, m in enumerate(p.bench)
                     if any((x.get("damage") or 0) >= 60 and game._cost_met(m, x["cost"], x)
                            for x in m.card.data.get("attacks", []))]
            if ready and game.turn >= 3:
                best = max(ready, key=lambda i: self._best_dmg(p.bench[i]))
                self._try(lambda i=best: game.retreat(p, i))
        # 7.51) Grand Tree: once per turn, evolve a Basic from the deck (then Stage 2)
        if (game.stadium and game.stadium.data.get("stadium_kind") == "grand_tree"
                and not getattr(p, "grand_tree_used", False) and game.turn > 1):
            for mon in p.all_pokemon():
                if mon.turn_played >= game.turn: continue
                evo = next((x for x in p.deck if x.evolves_from == mon.card.name), None)
                if evo:
                    p.deck.remove(evo); prev = mon.card.name
                    mon.stack.append(evo); mon.turn_played = game.turn; mon.status = set()
                    game.log(f"{p.name} uses Grand Tree: {prev} -> {evo.name}.")
                    evo2 = next((x for x in p.deck if x.evolves_from == evo.name), None)
                    if evo2:
                        p.deck.remove(evo2); mon.stack.append(evo2)
                        game.log(f"  ...and {evo.name} -> {evo2.name}!")
                    p.shuffle(game.rng); p.grand_tree_used = True
                    break
        # 7.52) Levincia: once per turn, 2 Basic [L] Energy from discard to hand
        if (game.stadium and game.stadium.data.get("stadium_kind") == "levincia"
                and not getattr(p, "levincia_used", False)):
            got = 0
            for x in list(p.discard):
                if got >= 2: break
                if x.is_energy and "Basic" in x.subtypes and "Lightning" in x.provides():
                    p.discard.remove(x); p.hand.append(x); got += 1
            if got:
                p.levincia_used = True
                game.log(f"{p.name} uses Levincia: returns {got} [L] Energy to hand.")
        # 7.53) Mystery Garden: discard an energy to draw up to your Psychic count
        if (game.stadium and game.stadium.data.get("stadium_kind") == "psychic_garden"
                and not getattr(p, "garden_used", False)):
            n_psy = sum(1 for m in p.all_pokemon() if "Psychic" in m.card.types)
            spare = [x for x in p.hand if x.is_energy]
            if n_psy - (len(p.hand) - 1) >= 2 and spare:
                p.hand.remove(spare[0]); p.discard.append(spare[0])
                need = n_psy - len(p.hand)
                if need > 0: p.draw(need)
                p.garden_used = True
                game.log(f"{p.name} uses Mystery Garden: discards {spare[0].name}, draws {max(0,need)}.")
        # 7.55) Surfing Beach: free once-per-turn Water switch (humans use it as a free pivot)
        if (game.stadium and game.stadium.data.get("stadium_kind") == "water_switch"
                and p.active and "Water" in p.active.card.types and p.bench
                and not getattr(p, "stadium_switch_used", False)):
            try: rem = game.effective_max_hp(p.active) - p.active.damage
            except Exception: rem = p.active.max_hp - p.active.damage
            unready = not any(game._cost_met(p.active, x["cost"], x)
                              for x in p.active.card.data.get("attacks", []))
            if unready or rem <= 60 or self._is_utility_lead(p.active):
                ready = [i for i, m in enumerate(p.bench) if "Water" in m.card.types and
                         any((x.get("damage") or 0) >= 60 and game._cost_met(m, x["cost"], x)
                             for x in m.card.data.get("attacks", []))]
                if ready:
                    i = max(ready, key=lambda j: self._best_dmg(p.bench[j]))
                    p.bench.append(p.active); p.active = p.bench.pop(i)
                    p.active._came_active = True; p.stadium_switch_used = True
                    game.log(f"{p.name} uses Surfing Beach; {p.active.name} is now Active.")
        # 7.6) sacrificial pivot: if our valuable Active will be KO'd and can't trade back,
        #      feed a cheap 1-prize body instead (protects attackers and engines)
        if p.active and p.bench and not p.retreated_this_turn and game.turn > 1:
            opp = game.players[1 - game.players.index(p)]
            threat = self._threat_next_turn(opp.active, opp.all_pokemon())
            try: my_rem = game.effective_max_hp(p.active) - p.active.damage
            except Exception: my_rem = p.active.max_hp - p.active.damage
            opp_rem = 999
            if opp.active:
                try: opp_rem = game.effective_max_hp(opp.active) - opp.active.damage
                except Exception: opp_rem = opp.active.max_hp - opp.active.damage
            can_trade = any((x.get("damage") or 0) >= opp_rem and game._cost_met(p.active, x["cost"], x)
                            for x in p.active.card.data.get("attacks", []))
            if threat >= my_rem and not can_trade and self._active_is_valuable(game, p):
                sac = self._sacrifice_index(p)
                if sac is not None and not self._is_utility_lead(p.bench[sac]):
                    self._try(lambda i=sac: game.retreat(p, i))
        # 7.7) closer promotion: if a benched attacker takes more prizes RIGHT NOW
        #      than the Active can, pay the retreat and promote it (Starmie->Froslass)
        if p.active and p.bench and not p.retreated_this_turn:
            def best_prizes(mon):
                return max((self._attack_prizes(game, p, a, mon)[0]
                            for a in mon.card.data.get("attacks", [])
                            if game._cost_met(mon, a["cost"], a)), default=0)
            cur = best_prizes(p.active)
            cands = [(best_prizes(m), i) for i, m in enumerate(p.bench)]
            bp, bi = max(cands, default=(0, -1))
            opp = game.players[1 - game.players.index(p)]
            wins_now = bp >= len(p.prizes)
            if bi >= 0 and bp > cur and (wins_now or bp - cur >= 2):
                self._try(lambda i=bi: game.retreat(p, i))
        # 8) use activated abilities (Run Away Draw, Flip the Script)
        for mon in p.all_pokemon():
            for ab in mon.card.data.get("abilities", []):
                if ab.get("type") == "activated":
                    self._try(lambda m=mon, a=ab: self.fx.use_ability(game, p, m, a))
        # 9) attack (subclasses may override the chooser)
        self._attack_step(game, p)

    def _attack_prizes(self, game, p, atk, mon=None):
        """Estimate (prizes this attack takes now, total damage) — counts bench
        multi-KOs so a 2-prize line-snipe outranks a 1-prize active KO."""
        opp = game.players[1 - game.players.index(p)]
        def rem(m):
            try: return game.effective_max_hp(m) - m.damage
            except Exception: return m.max_hp - m.damage
        def pz(m):
            try: return m.prize_value()
            except Exception: return 2 if m.card.has_rule_box else 1
        dmg = atk.get("damage") or 0
        for e in atk.get("effects", []) or []:
            op, amt = e.get("op"), e.get("amount", 0)
            if op == "damage_per_own_hand":
                dmg += len(p.hand) * amt
            elif op == "damage_per_opponent_hand":
                dmg += len(opp.hand) * amt
            elif op == "damage_per_named_in_play":
                dmg += sum(1 for m in p.all_pokemon() if e.get("name","") in m.card.name) * amt
            elif op == "damage_per_self_counter" and mon is not None:
                dmg += (mon.damage // 10) * amt
        prizes, total = 0, dmg
        if dmg > 0 and opp.active and dmg >= rem(opp.active):
            prizes += pz(opp.active)
        for e in atk.get("effects", []) or []:
            op = e.get("op")
            if op == "mirage_barrage":
                amt, n = e.get("amount", 0), e.get("targets", 2)
                pool = sorted([m for m in opp.all_pokemon()
                               if amt - game.damage_reduction(m) >= rem(m)],
                              key=lambda m: -pz(m))[:n]
                prizes += sum(pz(m) for m in pool)
                total += amt * n
            elif op in ("bench_snipe", "damage_multi_opponent"):
                amt = e.get("amount", 0)
                n = e.get("targets", 1)
                pool = sorted([m for m in opp.bench
                               if amt - game.damage_reduction(m) >= rem(m)],
                              key=lambda m: -pz(m))[:n]
                prizes += sum(pz(m) for m in pool)
                total += amt * n
        return prizes, total

    def _attack_step(self, game, p):
        """Default: most prizes taken NOW, then highest damage; early game a human
        takes the item-lock line (Itchy Pollen) unless a KO is available."""
        if p.active and game.turn > 1:
            atks = p.active.card.data.get("attacks", [])
            score = {i: self._attack_prizes(game, p, atks[i], p.active) for i in range(len(atks))}
            order = sorted(range(len(atks)), key=lambda i: (-score[i][0], -score[i][1]))
            opp = game.players[1 - game.players.index(p)]
            opp_rem = (game.effective_max_hp(opp.active) - opp.active.damage) if opp.active else 999
            if game.turn <= 5:
                locks = [i for i in range(len(atks)) if self._is_lock_attack(atks[i])
                         and game._cost_met(p.active, atks[i]["cost"], atks[i])]
                ko = any((atks[i].get("damage") or 0) >= opp_rem and
                         game._cost_met(p.active, atks[i]["cost"], atks[i]) for i in order)
                if locks and not ko:
                    order = locks + [i for i in order if i not in locks]
            for i in order:
                if game._cost_met(p.active, atks[i]["cost"], atks[i]):
                    self._try(lambda i=i: game.attack(p, i, self.fx))
                    # Festival Lead: attack twice while Festival Grounds is up
                    if (not game.winner and p.active and game.stadium
                            and game.stadium.data.get("stadium_kind") == "festival_grounds"
                            and any(ab.get("static_kind") == "festival_lead"
                                    for ab in p.active.card.data.get("abilities", []))
                            and game.abilities_enabled(p.active)
                            and game._cost_met(p.active, atks[i]["cost"], atks[i])):
                        game.log(f"{p.name}'s {p.active.name} attacks AGAIN (Festival Lead).")
                        self._try(lambda i=i: game.attack(p, i, self.fx))
                    break

    def _do_evolutions(self, game, p):
        for card in list(p.hand):
            if card.is_pokemon and card.evolves_from:
                for mon in p.all_pokemon():
                    if mon.card.name == card.evolves_from and mon.turn_played != game.turn:
                        if self._try(lambda c=card, m=mon: self._evolve_with_ability(game, p, c, m)):
                            break

    def _evolve_with_ability(self, game, p, card, mon):
        game.evolve(p, card, mon)
        for ab in card.data.get("abilities", []):
            if ab.get("type") == "on_evolve":
                try: self.fx.use_ability(game, p, mon, ab)
                except RuleError: pass



class SearchAgent(HeuristicAgent):
    """1-ply look-ahead at the attack step. Develops the board like the heuristic
    agent, then clones the game and tries every affordable attack, keeping the line
    that scores best. The score rewards prizes taken AND total damage left on the
    opponent's board — so spreading damage (Phantom Dive, Mortal Shuriken) that sets
    up future KOs is valued, not just hitting the Active."""
    import copy as _copy

    def _score(self, clone, pi):
        me = clone.players[pi]; opp = clone.players[1 - pi]
        if clone.winner is me: return 10**9
        if clone.winner is opp: return -10**9
        prizes_taken = 6 - len(me.prizes)            # KOs we converted to prizes
        board_dmg = sum(m.damage for m in opp.all_pokemon())   # spread counts here
        # value pressure on multi-prize threats a bit extra
        threat = sum(m.damage for m in opp.all_pokemon() if "ex" in m.card.subtypes)
        my_exposure = (me.active.damage if me.active else 0)
        return prizes_taken * 1000 + board_dmg + 0.3 * threat - 0.05 * my_exposure

    def _attack_step(self, game, p):
        if not (p.active and game.turn > 1): return
        atks = p.active.card.data.get("attacks", [])
        affordable = [i for i in range(len(atks))
                      if game._cost_met(p.active, atks[i]["cost"], atks[i])]
        if not affordable: return
        pi = game.players.index(p)
        best_i, best = None, -10**12
        for i in affordable:
            clone = SearchAgent._copy.deepcopy(game)
            try:
                clone.attack(clone.players[pi], i, self.fx)
            except Exception:
                continue
            sc = self._score(clone, pi)
            if sc > best:
                best, best_i = sc, i
        if best_i is not None:
            self._try(lambda i=best_i: game.attack(p, i, self.fx))



class DeepSearchAgent(HeuristicAgent):
    """Rollout-based turn search. Develops the board (deferring Boss's Orders),
    then evaluates each finisher line {gust target x attack} by cloning and playing
    several turns forward with a fast policy, scoring on the multi-turn prize swing.
    This makes *spreading damage now* valuable, because the rollout converts that
    spread into KOs on later turns."""
    import copy as _copy
    def __init__(self, fx, depth=3, samples=3):
        super().__init__(fx)
        self.depth = depth; self.samples = samples
        self._roller = HeuristicAgent(fx)   # fast policy used inside rollouts

    def take_turn(self, game, p):
        self._skip_ops = {"gust_opponent_bench"}   # defer Boss's; search decides it
        super().take_turn(game, p)                 # develops, then calls _attack_step
        self._skip_ops = set()

    def _gust_card(self, p):
        if p.supporter_played_this_turn: return None
        for c in p.hand:
            if c.is_trainer and "Supporter" in c.subtypes and \
               any(e.get("op") == "gust_opponent_bench" for e in c.data.get("effects", [])):
                return c
        return None

    def _rollout(self, clone, pi, seed):
        import random
        clone.rng = random.Random(seed)
        turns = 0
        while not clone.winner and turns < self.depth:
            if not clone.start_turn(): break
            self._roller.take_turn(clone, clone.current)
            clone.end_turn(); turns += 1
        me = clone.players[pi]; opp = clone.players[1 - pi]
        if clone.winner is me: return 1e6 - clone.turn
        if clone.winner is opp: return -1e6 + clone.turn
        my_taken = 6 - len(me.prizes); opp_taken = 6 - len(opp.prizes)
        board = sum(m.damage for m in opp.all_pokemon()) - sum(m.damage for m in me.all_pokemon())
        return (my_taken - opp_taken) * 1000 + board

    def _eval(self, game, pi, g, a, promote=None):
        vals = []
        for s in range(self.samples):
            clone = DeepSearchAgent._copy.deepcopy(game)
            cp = clone.players[pi]
            try:
                if promote is not None:
                    clone.retreat(cp, promote)
                if g is not None:
                    gc = self._gust_card(cp)
                    if gc:
                        self.fx.policy._force = g
                        try: self.fx.play_trainer(clone, cp, gc)
                        finally: self.fx.policy._force = None
                clone.attack(cp, a, self.fx)
                clone.end_turn()
            except Exception:
                self.fx.policy._force = None
                continue
            vals.append(self._rollout(clone, pi, seed=(hash((g, a, s, promote)) & 0x7fffffff)))
        if not vals: return -1e17, -1e17
        return sum(vals) / len(vals), min(vals)   # (mean, worst-case)

    def _attack_step(self, game, p):
        if not (p.active and game.turn > 1): return
        pi = game.players.index(p)
        opp = game.players[1 - pi]
        # branch set: EVERY gust target (the "bossed the wrong Pokemon" lesson),
        # every affordable attack, and promotion lines for ready bench attackers
        gust_opts = [None]
        if self._gust_card(p) and opp.bench:
            gust_opts += list(range(len(opp.bench)))[:5]
        promo_opts = [None]
        ready = [j for j, m in enumerate(p.bench)
                 if any(game._cost_met(m, x["cost"], x) and (x.get("damage") or 0) >= 60
                        for x in m.card.data.get("attacks", []))]
        promo_opts += sorted(ready, key=lambda j: -self._best_dmg(p.bench[j]))[:2]
        branches = []
        for pr in promo_opts:
            mon = p.active if pr is None else p.bench[pr]
            atks = mon.card.data.get("attacks", [])
            aff = [i for i in range(len(atks)) if game._cost_met(mon, atks[i]["cost"], atks[i])]
            for g in gust_opts:
                for a in aff:
                    branches.append((g, a, pr))
        if not branches: return
        best, bestval = None, (-1e18, -1e18)
        for (g, a, pr) in branches:
            mean, worst = self._eval(game, pi, g, a, promote=pr)
            # value already encodes speed (1e6 - turn): a win NOW beats a win
            # next turn — the "could have won a turn earlier" lesson. No early
            # break: rank every guaranteed win and take the fastest.
            if (worst, mean) > bestval: bestval, best = (worst, mean), (g, a, pr)
            if worst > 9e5 and bestval[0] >= 1e6 - game.turn - 1:
                break                        # immediate win found — nothing beats it
        g, a, pr = best
        if pr is not None:
            self._try(lambda i=pr: game.retreat(p, i))
        if g is not None:
            gc = self._gust_card(p)
            if gc:
                self.fx.policy._force = g
                self._try(lambda c=gc: self.fx.play_trainer(game, p, c))
                self.fx.policy._force = None
        self._try(lambda i=a: game.attack(p, i, self.fx))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", default=os.path.join(HERE, "decks", "alakazam_dudunsparce.txt"))
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    fx = Effects()
    p1 = Player("Ash", build_deck(args.deck))
    p2 = Player("Gary", build_deck(args.deck))
    game = Game(p1, p2, seed=args.seed)
    agent = HeuristicAgent(fx)

    game.setup()
    while not game.winner and game.turn < args.max_turns:
        if not game.start_turn():
            break
        agent.take_turn(game, game.current)
        game.end_turn()
    if not game.winner:
        game.log(f"(Demo stopped at turn cap {args.max_turns}.)")

    text = "\n".join(game.log_lines)
    out = args.out or os.path.join(HERE, "data", "last_game_log.txt")
    try:
        import tempfile, shutil
        tmp = tempfile.mktemp(suffix=".txt"); open(tmp, "w").write(text); shutil.copy(tmp, out)
    except Exception:
        open(out, "w", encoding="utf-8").write(text)
    print(text)
    print(f"\n[log saved to {out}]")

if __name__ == "__main__":
    main()
