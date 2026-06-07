"""
Effect interpreter for the Pokemon TCG simulator.

Reads the structured "effects" specs in cards.json and applies them against a
live Game/Player state from engine.py. Each op is a small primitive; complex
cards are expressed as a list of ops. Card behaviour therefore lives in DATA
(cards.json), and this module is the bridge between that data and the engine's
rule primitives. Add a new op here only when a genuinely new mechanic appears.

The simulator runs with an automatic "policy" (a simple AI) so games can play
to completion headless. Choices (which target, whether to use an optional
ability) are resolved by the Policy object passed in, defaulting to AutoPolicy.
"""
from __future__ import annotations
from engine import Game, Player, PokemonInPlay, Card, RuleError


# --------------------------------------------------------------------------
# Filters: match catalog cards against a spec like {"supertype":"Pokémon",...}
# --------------------------------------------------------------------------
def card_matches(card: Card, filt: dict) -> bool:
    if "any_of" in filt:
        return any(card_matches(card, f) for f in filt["any_of"])
    if "supertype" in filt and card.supertype != filt["supertype"]:
        return False
    sub = filt.get("subtype")
    if sub:
        if sub == "Evolution":
            if not (card.is_pokemon and ("Stage 1" in card.subtypes or "Stage 2" in card.subtypes)):
                return False
        elif sub not in card.subtypes:
            return False
    if "type" in filt:
        prov = card.types + (card.provides() if card.is_energy else [])
        if filt["type"] not in prov:
            return False
    if "hp_max" in filt and card.hp > filt["hp_max"]:
        return False
    if filt.get("no_rule_box") and card.has_rule_box:
        return False
    if filt.get("is_ex") and "ex" not in card.subtypes:
        return False
    if "name_prefix" in filt and not card.name.startswith(filt["name_prefix"]):
        return False
    return True


# --------------------------------------------------------------------------
# Policies: decide optional choices so the sim can run automatically
# --------------------------------------------------------------------------
class AutoPolicy:
    """A deliberately simple decision-maker; swap in a smarter one later."""
    def use_optional_ability(self, game, player, mon, ability): return True
    def choose_search(self, game, player, candidates, filt, count):
        return candidates[:count]
    def choose_pokemon(self, game, candidates):
        return candidates[0] if candidates else None
    def choose_bench_target(self, game, player):
        return player.bench[0] if player.bench else player.active



def _remote_reach(game, player):
    """Max counters/snipe damage we can deliver WITHOUT the active attack landing
    on the target: spread ops, bench snipes, and an unused Munkidori."""
    reach = 0
    for m in player.all_pokemon():
        for a in m.card.data.get("attacks", []):
            if not game._cost_met(m, a["cost"], a): continue
            for e in a.get("effects", []) or []:
                if e.get("op") == "spread_counters_opponent_bench":
                    reach = max(reach, e.get("counters", 0) * 10)
                elif e.get("op") in ("bench_snipe", "mirage_barrage"):
                    reach = max(reach, e.get("amount", 0))
    return reach

def _remaining(game, m):
    try: return game.effective_max_hp(m) - m.damage
    except Exception:
        try: return m.remaining_hp
        except Exception: return 9999

def _prize(m):
    try: return m.prize_value()
    except Exception: return 2 if "ex" in m.card.subtypes else 1

class SkilledPolicy(AutoPolicy):
    """Plays with damage-spread awareness: finish what is closest to a KO,
    value multi-prize threats, and keep attackers energised."""
    _force = None  # when set to an int index, choose_pokemon returns candidates[_force]
    def choose_pokemon(self, game, candidates):
        if not candidates: return None
        if self._force is not None and 0 <= self._force < len(candidates):
            return candidates[self._force]
        # closest to a KO first; break ties toward higher prize value, then energised threats
        return min(candidates, key=lambda m: (_remaining(game, m), -_prize(m), -len(m.energy)))
    def choose_bench_target(self, game, player):
        # bringing up our own attacker: prefer the most energised, healthy Pokemon
        if not player.bench: return player.active
        return max(player.bench, key=lambda m: (len(m.energy), -m.damage))


# --------------------------------------------------------------------------
# Effects engine
# --------------------------------------------------------------------------
class Effects:
    def __init__(self, policy=None):
        self.policy = policy or SkilledPolicy()

    # ---- entry points ----
    def play_trainer(self, game: Game, player: Player, card: Card):
        data = card.data
        if "Supporter" in card.subtypes:
            if player.supporter_played_this_turn:
                raise RuleError("Only one Supporter per turn.")
            if game.turn == 1 and game.players[game.active_idx] is player and _is_first_player_turn1(game):
                # SV rule: first player may play Supporters; only attack is barred. Allowed.
                pass
            player.supporter_played_this_turn = True
        if "Stadium" in card.subtypes:
            return self._play_stadium(game, player, card)
        if "Tool" in card.subtypes:
            raise RuleError("Tools are attached to a Pokemon, not played as one-shot.")
        cond = data.get("condition")
        if cond == "own_pokemon_ko_last_opponent_turn" and player.pokemon_ko_last_opp_turn <= 0:
            raise RuleError(f"{card.name}: condition not met.")
        opp_for_cond = game.players[1 - game.players.index(player)]
        if cond == "opponent_two_prizes" and len(opp_for_cond.prizes) != 2:
            raise RuleError(f"{card.name}: opponent must have exactly 2 Prizes.")
        if cond == "self_has_tera" and not any(m.is_tera for m in player.all_pokemon()):
            raise RuleError(f"{card.name}: requires a Tera Pokémon in play.")
        if cond == "opponent_three_or_fewer_prizes" and len(opp_for_cond.prizes) > 3:
            raise RuleError(f"{card.name}: opponent must have 3 or fewer Prizes.")
        if cond == "self_more_prizes" and len(player.prizes) <= len(opp_for_cond.prizes):
            raise RuleError(f"{card.name}: you must have more Prizes remaining than your opponent.")
        game.log(f"{player.name} plays {card.name}.")
        # Item discard-cost (Ultra Ball) resolves while the card is still in hand.
        for op in data.get("effects", []):
            if op["op"] == "discard_from_hand_cost":
                self._run_op(game, player, op, source=card)
        player.hand.remove(card)
        player.discard.append(card)
        for op in data.get("effects", []):
            if op["op"] != "discard_from_hand_cost":
                self._run_op(game, player, op, source=card)

    def attach_tool(self, game, player, tool_card, target: PokemonInPlay):
        if target.tools:
            raise RuleError("Pokemon already has a Tool.")
        player.hand.remove(tool_card)
        target.tools.append(tool_card)
        game.log(f"{player.name} attaches {tool_card.name} to {target.name}.")

    def attach_energy(self, game, player, energy_card, target: PokemonInPlay):
        game.attach_energy(player, energy_card, target)
        on_attach = energy_card.data.get("on_attach")
        if on_attach:
            cond = on_attach.get("condition")
            ok = True
            if cond and cond.startswith("attached_to_type:"):
                ok = cond.split(":", 1)[1] in target.card.types
            if ok:
                for op in on_attach["effects"]:
                    self._run_op(game, player, op, source=energy_card)

    def use_ability(self, game, player, mon: PokemonInPlay, ability):
        if ability.get("type") == "static":
            return  # static abilities are passive; handled where relevant
        name = ability["name"]
        if ability.get("once_per_turn") and name in mon.ability_used_this_turn:
            raise RuleError(f"{name} already used this turn.")
        if ability.get("condition") == "own_pokemon_ko_last_opponent_turn":
            if player.pokemon_ko_last_opp_turn <= 0:
                raise RuleError("Flip the Script condition not met.")
        if ability.get("condition") == "active_only" and mon is not player.active:
            raise RuleError(f"{name} can only be used from the Active Spot.")
        if ability.get("condition") == "first_turn" and game.turn > 2:
            raise RuleError(f"{name} can only be used on your first turn.")
        if not game.abilities_enabled(mon):
            raise RuleError(f"{name} is disabled (Team Rocket's Watchtower).")
        if ability.get("self_ko") and game.self_ko_abilities_disabled():
            raise RuleError(f"{name} is disabled by Damp.")
        if ability.get("condition") == "tera_in_play":
            if not any("Tera" in m.card.subtypes for m in player.all_pokemon()):
                raise RuleError("Jewel Seeker: no Tera Pokemon in play.")
        if ability.get("condition") == "active_festival_lead":
            act = player.active
            if not (act and any(a.get("static_kind") == "festival_lead"
                                for a in act.card.data.get("abilities", []))):
                raise RuleError("Boom Boom Groove: Active has no Festival Lead.")
        if ability.get("condition") == "self_has_darkness_energy":
            if "Darkness" not in mon.energy_count():
                raise RuleError(f"{name} requires a Darkness Energy attached.")
        if ability.get("condition") == "trade":
            if len(player.hand) < 1:
                raise RuleError("Trade: no card to discard.")
        if ability.get("condition") == "mortal_shuriken":
            if mon is not player.active:
                raise RuleError(f"{name} can only be used from the Active Spot.")
            if not any(c.is_energy and "Basic" in c.subtypes and "Water" in c.provides() for c in player.hand):
                raise RuleError(f"{name}: need a Basic Water Energy to discard.")
        if ability.get("condition") == "lunar_cycle":
            has_solrock = any(m.card.name == "Solrock" for m in player.all_pokemon())
            has_fight = any(c.is_energy and "Basic" in c.subtypes and "Fighting" in c.provides() for c in player.hand)
            if not (has_solrock and has_fight):
                raise RuleError(f"{name}: need Solrock in play and a Basic Fighting Energy to discard.")
        # Psyduck 'Damp' disables self-KO abilities (none in this deck) -> no-op guard
        game.log(f"{player.name}'s {mon.name} uses Ability {name}.")
        prev = getattr(game, "_dmg_source", "attack"); game._dmg_source = "ability"
        try:
            for op in ability.get("effects", []):
                self._run_op(game, player, op, source=mon)
        finally:
            game._dmg_source = prev
        mon.ability_used_this_turn.add(name)

    def trigger_on_bench(self, game, player, pip):
        for ab in pip.card.data.get("abilities", []):
            if ab.get("type") == "on_bench" and game.abilities_enabled(pip):
                game.log(f"{player.name}'s {pip.name} uses Ability {ab['name']}.")
                prev = getattr(game, "_dmg_source", "attack"); game._dmg_source = "ability"
                try:
                    for op in ab.get("effects", []):
                        self._run_op(game, player, op, source=pip)
                finally:
                    game._dmg_source = prev

    def run_attack(self, game, player, mon, atk):
        for op in atk.get("effects", []):
            self._run_op(game, player, op, source=mon)

    def trigger_on_damaged(self, game, def_mon, atk_mon, owner, attacker_owner):
        # fire Tool triggers for tools whose trigger is on_damaged_active
        if def_mon is not owner.active:
            return
        sources = (def_mon.tools if game.tools_active() else []) + def_mon.energy
        for src in sources:
            if src.data.get("tool_trigger") == "on_damaged_active":
                game.log(f"  {src.name} triggers on {def_mon.name}.")
                for op in src.data.get("effects", []):
                    self._run_op(game, owner, op, source=src,
                                 ctx={"attacker": atk_mon, "attacker_owner": attacker_owner})

    # ---- stadium ----
    def _play_stadium(self, game, player, card):
        if game.stadium and game.stadium.name == card.name:
            raise RuleError("Cannot replace a Stadium with the same name.")
        if game.stadium:
            game.stadium_owner.discard.append(game.stadium)
        game.stadium = card
        game.stadium_owner = player
        player.hand.remove(card)
        game.log(f"{player.name} plays Stadium {card.name}.")

    # ---- op dispatcher ----
    def _run_op(self, game, player, op, source=None, ctx=None):
        ctx = ctx or {}
        fn = getattr(self, "_op_" + op["op"], None)
        if fn is None:
            game.log(f"  [unimplemented op: {op['op']}]")
            return
        fn(game, player, op, source, ctx)

    # ---- ops ----
    def _op_draw(self, game, player, op, source, ctx):
        d = player.draw(op["n"]); game.log(f"  draws {len(d)} (hand {len(player.hand)}).")

    def _op_draw_to(self, game, player, op, source, ctx):
        need = max(0, op["n"] - len(player.hand)); player.draw(need)

    def _op_shuffle_deck(self, game, player, op, source, ctx):
        player.shuffle(game.rng)

    def _op_switch_self_with_bench(self, game, player, op, source, ctx):
        if not player.bench: return
        tgt = self.policy.choose_bench_target(game, player)
        idx = player.bench.index(tgt)
        if player.active is not None: player.bench.append(player.active)
        player.active = player.bench.pop(idx)
        player.active._came_active = True
        game.log(f"  switches to {player.active.name}.")

    def _op_search_deck_to_hand(self, game, player, op, source, ctx):
        cands = [c for c in player.deck if card_matches(c, op["filter"])]
        chosen = self.policy.choose_search(game, player, cands, op["filter"], op["count"])
        for c in chosen:
            player.deck.remove(c); player.hand.append(c)
        game.log(f"  searches deck -> hand: {[c.name for c in chosen] or 'nothing'}")

    def _op_search_deck_to_bench(self, game, player, op, source, ctx):
        cands = [c for c in player.deck if card_matches(c, op["filter"])]
        room = 5 - len(player.bench)
        chosen = self.policy.choose_search(game, player, cands, op["filter"], min(op["count"], room))
        for c in chosen:
            player.deck.remove(c); game.play_basic_to_bench_card(player, c) if hasattr(game, "play_basic_to_bench_card") else self._bench_card(game, player, c)
        game.log(f"  searches deck -> bench: {[c.name for c in chosen] or 'nothing'}")

    def _bench_card(self, game, player, card):
        pip = PokemonInPlay(card); pip.turn_played = game.turn; player.bench.append(pip)

    def _op_recover_from_discard(self, game, player, op, source, ctx):
        cands = [c for c in player.discard if card_matches(c, op["filter"])]
        chosen = cands[:op["count"]]
        for c in chosen:
            player.discard.remove(c); player.hand.append(c)
        game.log(f"  recovers from discard: {[c.name for c in chosen] or 'nothing'}")

    def _op_shuffle_from_discard_to_deck(self, game, player, op, source, ctx):
        cands = [c for c in player.discard if card_matches(c, op["filter"])]
        chosen = cands[:op["count"]]
        for c in chosen:
            player.discard.remove(c); player.deck.append(c)
        player.shuffle(game.rng)
        game.log(f"  shuffles {len(chosen)} card(s) from discard into deck.")

    def _op_gust_opponent_bench(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if not opp.bench: return
        f = getattr(self.policy, "_force", None)
        if f is not None and 0 <= f < len(opp.bench):   # planner-explored target
            if opp.active is not None: opp.bench.append(opp.active)
            opp.active = opp.bench.pop(f)
            game.log(f"  gusts up {opp.active.name} to opponent's Active.")
            return
        tgt = None
        if player.active:
            best = max(((a.get("damage") or 0) for a in player.active.card.data.get("attacks", [])), default=0)
            koable = [m for m in opp.bench if 0 < _remaining(game, m) <= best]
            if koable:   # human line: gust up the biggest prize we can actually KO,
                # but NEVER waste the gust on a body our counters/spread already reach
                reach = _remote_reach(game, player)
                for m2 in player.all_pokemon():
                    for ab2 in m2.card.data.get("abilities", []):
                        if any(e2.get("op") == "move_damage_counters_to_opponent"
                               for e2 in ab2.get("effects", [])) and \
                           any("Darkness" in x.provides() for x in m2.energy):
                            own_dmg = max((mm.damage for mm in player.all_pokemon()), default=0)
                            reach = max(reach, min(30, own_dmg))
                unreachable = [m for m in koable if _remaining(game, m) > reach]
                pool2 = unreachable or koable
                tgt = max(pool2, key=lambda m: (_prize(m), -_remaining(game, m)))
        if tgt is None:
            # no KO available: NEVER gust up a ready attacker (the "bossed the wrong
            # Pokemon" lesson) — pull the most STRANDED body to waste their turn
            def stranded(m):
                # "ready" = can PAY for any attack at all — printed damage is
                # irrelevant (copy/scaling attacks print 0 and hit hardest)
                ready = any(game._cost_met(m, a["cost"], a)
                            for a in m.card.data.get("attacks", []))
                return (not ready,                       # can't attack right now
                        m.card.data.get("retreat") or 0, # expensive to escape
                        -len(m.energy))                  # uninvested
            tgt = max(opp.bench, key=stranded)
        tgt = tgt or opp.bench[0]
        idx = opp.bench.index(tgt)
        if opp.active is not None: opp.bench.append(opp.active)
        opp.active = opp.bench.pop(idx)
        game.log(f"  gusts up {opp.active.name} to opponent's Active.")

    def _op_place_counters_per_hand(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        n = len(player.hand) * op["per_card"] * 10  # damage counters are 10 each
        if opp.active:
            opp.active.damage += n
            game.log(f"  places {n//10} counters ({n} dmg) on {opp.active.name}.")

    def _op_damage_any_opponent(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        target = self.policy.choose_pokemon(game, opp.all_pokemon())
        if target:
            if game.damage_prevented(target, source, player):
                game.log(f"  damage to {target.name} is prevented."); return
            target.damage += op["amount"]
            game.log(f"  deals {op['amount']} to {target.name} (any target).")

    def _op_move_energy_opponent_to_opponent(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        froms = [m for m in opp.all_pokemon() if m.energy]
        if not froms or len(opp.all_pokemon()) < 2: return
        src = froms[0]; e = src.energy.pop()
        dst = next(m for m in opp.all_pokemon() if m is not src)
        dst.energy.append(e)
        game.log(f"  moves opponent Energy {src.name} -> {dst.name}.")

    def _op_move_energy_from_attacker_to_their_bench(self, game, player, op, source, ctx):
        atk = ctx.get("attacker"); atk_owner = ctx.get("attacker_owner")
        if not atk or not atk.energy or not atk_owner.bench: return
        e = atk.energy.pop()
        atk_owner.bench[0].energy.append(e)
        game.log(f"  Handheld Fan moves {e.name} off attacker to its Bench.")

    def _op_discard_special_energy_opponent(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        for m in opp.all_pokemon():
            for e in list(m.energy):
                if "Special" in e.subtypes:
                    m.energy.remove(e); opp.discard.append(e)
                    game.log(f"  discards Special Energy {e.name} from {m.name}.")
                    return

    def _op_rare_candy_evolve(self, game, player, op, source, ctx):
        # find a Basic in play with a matching Stage 2 in hand
        for mon in player.all_pokemon():
            if mon.turn_played == game.turn: continue
            top = mon.card
            if "Basic" not in top.subtypes: continue
            for c in player.hand:
                if "Stage 2" in c.subtypes:
                    # stage2 evolves_from is the Stage1; check the Stage1 evolves_from == basic
                    s1_from = _stage1_between(game, c, top)
                    if s1_from:
                        game.evolve(player, c, mon, rare_candy=True)
                        for ab in c.data.get("abilities", []):
                            if ab.get("type") == "on_evolve":
                                try: self.use_ability(game, player, mon, ab)
                                except RuleError: pass
                        return
        game.log("  Rare Candy: no legal target.")

    def _op_shuffle_self_into_deck(self, game, player, op, source, ctx):
        mon = source
        if not isinstance(mon, PokemonInPlay): return
        for c in mon.attached_cards():
            player.deck.append(c)
        if mon is player.active:
            # Run Away Draw from the Active is a FREE pivot (human log pattern:
            # vacate into Budew to re-establish the item lock; else a ready attacker)
            def promo(i_m):
                i, m = i_m
                locker = any(e.get("op") == "opponent_item_lock_next_turn"
                             for a in m.card.data.get("attacks", [])
                             for e in a.get("effects", []) or [])
                ready = any(game._cost_met(m, a["cost"], a)
                            for a in m.card.data.get("attacks", []))
                best = max(((a.get("damage") or 0) for a in m.card.data.get("attacks", [])
                            if game._cost_met(m, a["cost"], a)), default=0)
                return (locker and game.turn <= 10, ready, best,
                        -(m.card.data.get("retreat") or 0))
            if player.bench:
                i, _ = max(enumerate(player.bench), key=promo)
                player.active = player.bench.pop(i)
            else:
                player.active = None
        elif mon in player.bench:
            player.bench.remove(mon)
        player.shuffle(game.rng)
        game.log(f"  {mon.name} and attached cards shuffled into deck.")

    # ---- additional ops (Mega Kangaskhan / Crustle decks) ----
    def _op_coin_flips_until_tails_bonus(self, game, player, op, source, ctx):
        heads = 0
        while game.rng.random() < 0.5:
            heads += 1
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + heads * op["per_heads"]
        game.log(f"  flipped {heads} heads -> +{heads*op['per_heads']} damage.")

    def _op_set_flag_ignore_defender_effects(self, game, player, op, source, ctx):
        game._ignore_effects = True

    def _op_set_flag_ignore_wr_and_effects(self, game, player, op, source, ctx):
        game._ignore_wr = True; game._ignore_effects = True

    def _op_ascension_evolve(self, game, player, op, source, ctx):
        mon = source
        if not isinstance(mon, PokemonInPlay): return
        evo = next((c for c in player.deck if c.evolves_from == mon.card.name), None)
        if not evo:
            game.log("  Ascension: no evolution found."); return
        player.deck.remove(evo); player.hand.append(evo)
        try:
            game.evolve(player, evo, mon)
        except RuleError as e:
            game.log(f"  Ascension evolve failed: {e}")

    def _op_shuffle_hand_draw(self, game, player, op, source, ctx):
        player.deck += player.hand; player.hand = []
        player.shuffle(game.rng)
        n = op["n"]
        if op.get("bonus_if_prizes") is not None and len(player.prizes) == op["bonus_if_prizes"]:
            n = op["bonus_n"]
        player.draw(n)
        game.log(f"  shuffles hand into deck and draws {n}.")

    def _op_ko_opponent_if_exactly_counters(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if opp.active and opp.active.damage == op["counters"] * 10:
            game.log(f"  Terminal Period: {opp.active.name} has exactly {op['counters']} counters and is Knocked Out.")
            game._knock_out(opp, opp.active)

    def _op_opponent_discard_from_hand(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        hits = [c for c in opp.hand if card_matches(c, op["filter"])][:op["count"]]
        for c in hits:
            opp.hand.remove(c); opp.discard.append(c)
        game.log(f"  discards {len(hits)} from opponent's hand: {[c.name for c in hits]}")

    def _op_opponent_discard_down_to(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        while len(opp.hand) > op["n"]:
            opp.discard.append(opp.hand.pop())
        game.log(f"  opponent discards down to {op['n']} cards.")

    def _op_heal_and_clear_status(self, game, player, op, source, ctx):
        tgt = self.policy.choose_pokemon(game, [m for m in player.all_pokemon() if m.damage > 0]) \
              or player.active
        if tgt:
            tgt.damage = max(0, tgt.damage - op["amount"]); tgt.status = set()
            game.log(f"  heals {op['amount']} and clears status on {tgt.name}.")

    def _op_heal_full_if_low(self, game, player, op, source, ctx):
        cands = [m for m in player.all_pokemon() if 0 < m.remaining_hp <= op["threshold"]]
        if cands:
            t = cands[0]; t.damage = 0
            game.log(f"  fully heals {t.name}.")

    def _op_coin_flip_discard_energy_opponent(self, game, player, op, source, ctx):
        if game.rng.random() < 0.5:
            opp = game.players[1 - game.players.index(player)]
            for m in opp.all_pokemon():
                if m.energy:
                    e = m.energy.pop(); opp.discard.append(e)
                    game.log(f"  heads! discards {e.name} from {m.name}."); return
        else:
            game.log("  Crushing Hammer: tails.")

    def _op_heal_active_if_energy(self, game, player, op, source, ctx):
        a = player.active
        if a and len(a.energy) >= op["min_energy"] and a.damage > 0:
            a.damage = max(0, a.damage - op["amount"])
            game.log(f"  heals {op['amount']} from {a.name}.")

    def _op_dig_top_reveal(self, game, player, op, source, ctx):
        top = player.deck[:op["n"]]
        hit = next((c for c in top if card_matches(c, op["filter"])), None)
        if hit:
            player.deck.remove(hit); player.hand.append(hit)
            game.log(f"  Pokégear reveals {hit.name}.")
        else:
            game.log("  Pokégear: no Supporter in top cards.")

    def _op_discard_from_hand_cost(self, game, player, op, source, ctx):
        others = [c for c in player.hand if c is not source]
        for c in others[:op["n"]]:
            player.hand.remove(c); player.discard.append(c)

    def _op_place_counters_on_attacker(self, game, player, op, source, ctx):
        atk = ctx.get("attacker")
        if atk:
            atk.damage += op["counters"] * 10
            game.log(f"  puts {op['counters']} damage counters on {atk.name}.")

    def _op_stadium_team_rocket_factory(self, *a): pass

    # ---- additional ops (Dragapult / Dusknoir decks) ----
    def _op_recon_directive(self, game, player, op, source, ctx):
        top = player.deck[:op["n"]]
        if not top: return
        keep = top[0]; player.deck.remove(keep); player.hand.append(keep)
        for c in top[1:]:
            player.deck.remove(c); player.deck.append(c)  # to bottom
        game.log(f"  Recon Directive keeps {keep.name}.")

    def _op_spread_counters_opponent_bench(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        remaining = op["counters"]
        targets = [m for m in opp.bench
                   if not game.damage_prevented(m, source, player)
                   and not game.bench_counters_blocked(m)]
        # 1) cash counters into actual KOs, cheapest first
        while remaining > 0:
            koable = [m for m in targets if 0 < _remaining(game, m) <= remaining * 10]
            if not koable: break
            m = min(koable, key=lambda x: _remaining(game, x))
            need = _remaining(game, m) // 10
            m.damage += need * 10; remaining -= need
            game.log(f"  spreads {need} counters to KO {m.name}.")
        # 2) bank the rest on the most valuable surviving threat (sets up next turn)
        survivors = [m for m in targets if _remaining(game, m) > 0]
        if remaining > 0 and survivors:
            m = min(survivors, key=lambda x: (_remaining(game, x), -_prize(x)))
            m.damage += remaining * 10
            game.log(f"  banks {remaining} counters on {m.name}.")

    def _op_place_counters_opponent_choice(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        n = op["counters"]
        # human counter-play: if the opponent can launder counters (Adrena-Brain),
        # only place where it KOs NOW — otherwise put them on the launderer itself
        launderers = [m for m in opp.all_pokemon()
                      if any(any(o.get("op") == "move_damage_counters_to_opponent"
                                 for o in ab.get("effects", []))
                             for ab in m.card.data.get("abilities", []))
                      and any("Darkness" in x.provides() for x in m.energy)]
        tgt = None
        if launderers:
            koable = [m for m in opp.all_pokemon() if 0 < _remaining(game, m) <= n * 10]
            tgt = max(koable, key=lambda m: (_prize(m), -_remaining(game, m))) if koable \
                  else min(launderers, key=lambda m: _remaining(game, m))
        tgt = tgt or self.policy.choose_pokemon(game, opp.all_pokemon())
        if tgt and not game.damage_prevented(tgt, source, player) and not game.bench_counters_blocked(tgt):
            tgt.damage += op["counters"] * 10
            game.log(f"  puts {op['counters']} counters on {tgt.name}.")

    def _op_ko_self(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay):
            source.damage = source.max_hp
            game.log(f"  {source.name} Knocks itself Out.")
            game.check_kos()

    def _op_move_damage_counters_to_opponent(self, game, player, op, source, ctx):
        froms = [m for m in player.all_pokemon() if m.damage >= 10]
        opp = game.players[1 - game.players.index(player)]
        if not froms or not opp.all_pokemon(): return
        cap = op["max"] * 10
        legal = [m for m in opp.all_pokemon() if not game.bench_counters_blocked(m)]
        if not legal: return
        # 1) KO conversion: if moving counters FINISHES something, do exactly that
        best = None
        for s in froms:
            mv_max = min(cap, s.damage)
            for t in legal:
                need = _remaining(game, t)
                if 0 < need <= mv_max:
                    key = (_prize(t), -need, s.damage)   # biggest prize, cheapest finish, heal-richest source
                    if best is None or key > best[0]:
                        best = (key, s, t, need)
        if best:
            _, s, t, need = best
            s.damage -= need; t.damage += need
            game.log(f"  moves {need//10} counters from {s.name} to {t.name} — Knocked Out by counters!")
            return
        # 2) no direct KO: set one up. Bank where our spread/snipe can FINISH it
        # this turn (Pecharunt line: Adrena 30 onto Zoroark -> Phantom spread 60 = KO),
        # preferring big prizes; skip their active (the attack handles that body).
        s = max(froms, key=lambda m: m.damage)
        move = min(cap, s.damage)
        reach = _remote_reach(game, player)
        opp_active = opp.active
        def fkey(m):
            rem_after = _remaining(game, m) - move
            converts = 0 < rem_after <= reach
            return (converts, _prize(m) if converts else 0, m is not opp_active, -max(rem_after, 0))
        t = max(legal, key=fkey)
        s.damage -= move; t.damage += move
        game.log(f"  moves {move//10} counters from {s.name} to {t.name}.")

    def _op_coin_status_opponent_active(self, game, player, op, source, ctx):
        if game.rng.random() < 0.5:
            opp = game.players[1 - game.players.index(player)]
            if opp.active:
                opp.active.status.add(op["status"]); game.log(f"  heads! {opp.active.name} is now {op['status']}.")
        else:
            game.log("  Numbing Water: tails.")

    def _op_status_opponent_active(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if opp.active:
            opp.active.status.add(op["status"])
            game.log(f"  {opp.active.name} is now {op['status']}.")

    def _op_opponent_item_lock_next_turn(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        opp.item_locked = True
        game.log("  opponent can't play Items next turn.")

    def _op_return_self_to_hand(self, game, player, op, source, ctx):
        mon = source
        if not isinstance(mon, PokemonInPlay): return
        for c in mon.attached_cards(): player.hand.append(c)
        if mon is player.active:
            player.active = player.bench.pop(0) if player.bench else None
        elif mon in player.bench:
            player.bench.remove(mon)
        game.log(f"  {mon.name} returns to hand.")

    def _op_damage_per_opponent_ex(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        n = sum(1 for m in opp.all_pokemon() if "ex" in m.card.subtypes)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  Tenacious Tail: {n} opposing ex -> {n*op['amount']} damage.")

    def _op_bonus_if_opponent_active_ex(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if opp.active and "ex" in opp.active.card.subtypes:
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + op["amount"]
            game.log(f"  +{op['amount']} (opponent Active is ex).")

    def _op_crispin_energy(self, game, player, op, source, ctx):
        basics, seen = [], set()
        for c in player.deck:
            if c.is_energy and "Basic" in c.subtypes and c.types and c.types[0] not in seen:
                basics.append(c); seen.add(c.types[0])
            if len(basics) == 2: break
        if not basics: return
        # attach first to active, put second (if any) into hand
        attach = basics[0]; player.deck.remove(attach)
        if player.active:
            player.active.energy.append(attach)
            game.log(f"  Crispin attaches {attach.name} to {player.active.name}.")
        if len(basics) > 1:
            handcard = basics[1]; player.deck.remove(handcard); player.hand.append(handcard)
            game.log(f"  Crispin puts {handcard.name} into hand.")

    def _op_each_player_shuffle_draw(self, game, player, op, source, ctx):
        for p in game.players:
            p.deck += p.hand; p.hand = []; p.shuffle(game.rng); p.draw(op["n"])
        game.log(f"  each player shuffles hand and draws {op['n']}.")

    def _op_unfair_stamp(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        for p in game.players:
            p.deck += p.hand; p.hand = []; p.shuffle(game.rng)
        player.draw(op["you"]); opp.draw(op["opp"])
        game.log(f"  Unfair Stamp: you draw {op['you']}, opponent draws {op['opp']}.")

    def _op_opponent_active_cant_retreat(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if opp.active: opp.active._cant_retreat = True

    def _op_stadium_passive(self, *a): pass

    # ---- additional ops (Meganium / Ogerpon Grass deck) ----
    def _op_ability_attach_grass(self, game, player, op, source, ctx):
        e = next((c for c in player.hand if c.is_energy and "Basic" in c.subtypes
                  and "Grass" in c.provides()), None)
        if not e:
            game.log("  no Basic Grass Energy in hand."); return
        if op["target"] == "self" and isinstance(source, PokemonInPlay):
            tgt = source
        else:
            dmg = [m for m in player.all_pokemon() if m.damage > 0]
            tgt = dmg[0] if dmg else player.active
        if not tgt: return
        player.hand.remove(e); tgt.energy.append(e)
        game.log(f"  attaches {e.name} to {tgt.name} (ability).")
        if op.get("then") == "draw":
            player.draw(op.get("amount", 1)); game.log(f"  draws {op.get('amount',1)}.")
        elif op.get("then") == "heal":
            amt = op.get("amount", 0); tgt.damage = max(0, tgt.damage - amt)
            game.log(f"  heals {amt} from {tgt.name}.")

    def _op_damage_per_energy_both_active(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        n = (len(player.active.energy) if player.active else 0) + (len(opp.active.energy) if opp.active else 0)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} Energy on both Active).")

    def _op_damage_per_grass_energy_yours(self, game, player, op, source, ctx):
        n = sum(1 for m in player.all_pokemon() for e in m.energy if "Grass" in e.provides())
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} Grass Energy on your Pokemon).")

    def _op_damage_per_own_bench(self, game, player, op, source, ctx):
        n = len(player.bench)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} Benched).")

    def _op_self_damage(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay):
            source.damage += op["amount"]
            game.log(f"  {source.name} does {op['amount']} to itself.")

    def _op_tutor_to_top(self, game, player, op, source, ctx):
        chosen = player.deck[:op["count"]]
        for c in chosen: player.deck.remove(c)
        player.shuffle(game.rng)
        for c in reversed(chosen): player.deck.insert(0, c)
        game.log(f"  puts {len(chosen)} searched card(s) on top of deck.")

    def _op_dig_top_take_multi(self, game, player, op, source, ctx):
        top = player.deck[:op["n"]]
        hits = [c for c in top if card_matches(c, op["filter"])][:op["max"]]
        for c in hits: player.deck.remove(c); player.hand.append(c)
        player.shuffle(game.rng)
        game.log(f"  takes {[c.name for c in hits] or 'nothing'} from top {op['n']}.")

    def _op_attach_energy_from_discard_to_bench(self, game, player, op, source, ctx):
        typ = op.get("type")
        targets = [m for m in player.bench if (not typ or typ in m.card.types)]
        if not targets: game.log("  Wondrous Patch: no valid Benched target."); return
        en = next((c for c in player.discard if c.is_energy and "Basic" in c.subtypes
                   and (not typ or typ in c.provides())), None)
        if not en: game.log("  Wondrous Patch: no matching Energy in discard."); return
        player.discard.remove(en); targets[0].energy.append(en)
        game.log(f"  Wondrous Patch attaches {en.name} to {targets[0].name}.")

    def _op_ability_attach_energy_from_discard(self, game, player, op, source, ctx):
        en = next((c for c in player.discard if c.is_energy and "Basic" in c.subtypes), None)
        if not en:
            game.log("  no Basic Energy in discard."); return
        tgt = player.active or (player.bench[0] if player.bench else None)
        if not tgt: return
        player.discard.remove(en); tgt.energy.append(en)
        game.log(f"  attaches {en.name} from discard to {tgt.name}.")

    def _op_damage_per_bench_both(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        n = len(player.bench) + len(opp.bench)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} Benched total).")

    def _op_self_cant_attack_next_turn(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay):
            source._cant_attack_next = True

    def _op_torrential_pump(self, game, player, op, source, ctx):
        if not isinstance(source, PokemonInPlay) or len(source.energy) < op["shuffle"]:
            return
        for _ in range(op["shuffle"]):
            player.deck.append(source.energy.pop())
        player.shuffle(game.rng)
        opp = game.players[1 - game.players.index(player)]
        tgt = opp.bench[0] if opp.bench else None
        if tgt and not game.damage_prevented(tgt, source, player):
            tgt.damage += op["bench_damage"]
            game.log(f"  Torrential Pump: {op['bench_damage']} to benched {tgt.name}.")

    def _op_discard_stadium(self, game, player, op, source, ctx):
        if game.stadium and game.stadium_owner:
            game.stadium_owner.discard.append(game.stadium)
            game.log(f"  discards Stadium {game.stadium.name}.")
            game.stadium = None; game.stadium_owner = None

    def _op_return_energy_to_hand_self(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay) and source.energy:
            en = source.energy.pop(); player.hand.append(en)
            game.log(f"  returns {en.name} to hand.")

    def _op_discard_hand_draw(self, game, player, op, source, ctx):
        player.discard += player.hand; player.hand = []
        player.draw(op["n"])
        game.log(f"  discards hand and draws {op['n']}.")

    def _op_discard_basic_energy_for_damage(self, game, player, op, source, ctx):
        discarded = 0
        for m in player.bench:  # keep the attacker's Energy; pull from bench
            for en in list(m.energy):
                if "Basic" in en.subtypes:
                    m.energy.remove(en); player.discard.append(en); discarded += 1
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + discarded * op["per"]
        game.log(f"  discards {discarded} Basic Energy -> {discarded*op['per']} damage.")

    def _op_bonus_if_own_ko_last_turn(self, game, player, op, source, ctx):
        if player.pokemon_ko_last_opp_turn > 0:
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + op["amount"]
            game.log(f"  +{op['amount']} (your Pokemon was KO'd last turn).")

    def _op_harlequin(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        for p in game.players:
            p.deck += p.hand; p.hand = []; p.shuffle(game.rng)
        if game.rng.random() < 0.5:
            player.draw(op["heads_you"]); opp.draw(op["heads_opp"])
            game.log(f"  heads: you {op['heads_you']}, opp {op['heads_opp']}.")
        else:
            player.draw(op["tails_you"]); opp.draw(op["tails_opp"])
            game.log(f"  tails: you {op['tails_you']}, opp {op['tails_opp']}.")

    def _op_energy_switch(self, game, player, op, source, ctx):
        froms = [m for m in player.all_pokemon() if any("Basic" in e.subtypes for e in m.energy)]
        if not froms or len(player.all_pokemon()) < 2: return
        src = froms[0]
        en = next(e for e in src.energy if "Basic" in e.subtypes)
        dst = next((m for m in player.all_pokemon() if m is not src), None)
        if dst:
            src.energy.remove(en); dst.energy.append(en)
            game.log(f"  Energy Switch: {en.name} {src.name} -> {dst.name}.")

    def _op_glass_trumpet(self, game, player, op, source, ctx):
        targets = [m for m in player.bench if "Colorless" in m.card.types][:op["count"]]
        for m in targets:
            en = next((c for c in player.discard if c.is_energy and "Basic" in c.subtypes), None)
            if not en: break
            player.discard.remove(en); m.energy.append(en)
            game.log(f"  Glass Trumpet attaches {en.name} to {m.name}.")

    def _op_attach_basic_energy_from_discard_to_bench_multi(self, game, player, op, source, ctx):
        typ = op.get("type"); n = op.get("count", 1); done = 0
        if not player.bench: game.log("  no Benched Pokemon to attach to."); return
        for _ in range(n):
            en = next((c for c in player.discard if c.is_energy and "Basic" in c.subtypes
                       and (not typ or typ in c.provides())), None)
            if not en: break
            tgt = player.bench[done % len(player.bench)]
            player.discard.remove(en); tgt.energy.append(en); done += 1
            game.log(f"  attaches {en.name} from discard to {tgt.name}.")
        if not done: game.log("  no matching Energy in discard.")

    def _op_cosmic_beam(self, game, player, op, source, ctx):
        need = op.get("requires_bench")
        if need and not any(m.card.name == need for m in player.bench):
            game.log(f"  Cosmic Beam does nothing (no {need} on Bench)."); return
        game._ignore_wr = True
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + op["amount"]
        game.log(f"  Cosmic Beam: {op['amount']} (ignores Weakness/Resistance).")

    def _op_discard_basic_energy_type_from_hand(self, game, player, op, source, ctx):
        typ = op.get("type")
        en = next((c for c in player.hand if c.is_energy and "Basic" in c.subtypes
                   and (not typ or typ in c.provides())), None)
        if en:
            player.hand.remove(en); player.discard.append(en)
            game.log(f"  discards {en.name} (cost).")

    def _op_set_fighting_boost(self, game, player, op, source, ctx):
        player.fighting_boost = op["amount"]
        game.log(f"  Fighting attacks do +{op['amount']} this turn.")

    def _op_lacey_draw(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        player.deck += player.hand; player.hand = []; player.shuffle(game.rng)
        n = op["bonus_n"] if len(opp.prizes) <= op["opp_prizes_max"] else op["n"]
        player.draw(n)
        game.log(f"  Lacey: shuffles hand and draws {n}.")

    def _op_opponent_hand_to_bottom_draw(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        had = len(opp.hand)
        opp.deck = opp.deck + opp.hand; opp.hand = []  # to bottom (no shuffle of deck)
        if had:
            opp.draw(op["n"])
        game.log(f"  opponent puts {had} card(s) on bottom and draws {op['n'] if had else 0}.")

    def _op_mirage_barrage(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay):
            for _ in range(op["discard"]):
                if source.energy: player.discard.append(source.energy.pop())
        opp = game.players[1 - game.players.index(player)]
        # human targeting: never waste a hit on a protected body; take every KO you
        # can (prizes first), then bank the rest on the biggest unprotected threat
        cands = [m for m in opp.all_pokemon() if not game.damage_prevented(m, source, player)]
        def rem(m):
            try: return game.effective_max_hp(m) - m.damage
            except Exception: return m.max_hp - m.damage
        def pz(m):
            try: return m.prize_value()
            except Exception: return 2 if m.card.has_rule_box else 1
        def hit(m): return max(0, op["amount"] - game.damage_reduction(m))
        def stage(m):
            return 2 if "Stage 2" in m.card.subtypes else (1 if "Stage 1" in m.card.subtypes else 0)
        # bench KOs over active KOs (the Active is reachable next turn anyway),
        # evolved engine pieces over naked basics
        kos = sorted([m for m in cands if hit(m) >= rem(m)],
                     key=lambda m: (-pz(m), m is opp.active, -stage(m), rem(m)))
        rest = sorted([m for m in cands if hit(m) < rem(m)],
                      key=lambda m: (-pz(m), m is opp.active, -stage(m), rem(m)))
        picks = (kos + rest)[:op["targets"]]
        for m in picks:
            amt = hit(m)
            m.damage += amt
            game.log(f"  Mirage Barrage: {amt} to {m.name}.")

    def _op_ninja_spinner(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay):
            en = next((x for x in source.energy if op["type"] in x.provides()), None)
            if en:
                source.energy.remove(en); player.hand.append(en)
                game._atk_bonus = getattr(game, "_atk_bonus", 0) + op["bonus"]
                game.log(f"  Ninja Spinner: returns {en.name}, +{op['bonus']} damage.")

    def _op_damage_per_opponent_hand(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        n = len(opp.hand)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} cards in opponent's hand).")

    def _op_rosa_attach(self, game, player, op, source, ctx):
        tgt = next((m for m in player.all_pokemon() if "Stage 2" in m.card.subtypes), None)
        if not tgt:
            game.log("  Rosa's: no Stage 2 Pokemon."); return
        done = 0
        for _ in range(op["count"]):
            en = next((c for c in player.discard if c.is_energy and "Basic" in c.subtypes), None)
            if not en: break
            player.discard.remove(en); tgt.energy.append(en); done += 1
            game.log(f"  Rosa's attaches {en.name} to {tgt.name}.")
        if not done:
            game.log("  Rosa's: no Basic Energy in discard.")

    def _op_punk_up(self, game, player, op, source, ctx):
        typ = op.get("type", "Darkness"); n = op["count"]
        targets = [m for m in player.all_pokemon() if "Marnie's" in m.card.name]
        if not targets: game.log("  Punk Up: no Marnie's Pokemon."); return
        attached = 0
        for _ in range(n):
            en = next((c for c in player.deck if c.is_energy and "Basic" in c.subtypes and typ in c.provides()), None)
            if not en: break
            player.deck.remove(en); targets[attached % len(targets)].energy.append(en); attached += 1
        player.shuffle(game.rng)
        game.log(f"  Punk Up attaches {attached} {typ} Energy to Marnie's Pokemon.")

    def _op_bench_snipe(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        pool = list(opp.bench)
        for _ in range(op.get("targets", 1)):
            tgt = self.policy.choose_pokemon(game, pool) if pool else None
            if not tgt: break
            pool.remove(tgt)
            if not game.damage_prevented(tgt, source, player):
                amt = max(0, op["amount"] - game.damage_reduction(tgt))
                tgt.damage += amt
                game.log(f"  also does {amt} to benched {tgt.name}.")

    def _op_splashing_dodge(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay) and game.rng.random() < 0.5:
            source._dodge_active = True
            game.log("  Splashing Dodge: heads! protected during opponent's next turn.")

    def _op_discard_any_from_hand(self, game, player, op, source, ctx):
        for _ in range(op["n"]):
            if player.hand:
                player.discard.append(player.hand.pop())

    def _op_night_joker(self, game, player, op, source, ctx):
        best = 0
        for m in player.bench:
            if "N's" in m.card.name:
                for atk in m.card.data.get("attacks", []):
                    best = max(best, atk.get("damage") or 0)
        opp = game.players[1 - game.players.index(player)]
        if best and opp.active and not game.damage_prevented(opp.active, source, player):
            opp.active.damage += best
            game.log(f"  Night Joker copies a Benched N's attack for {best}.")

    def _op_damage_per_self_counter(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay):
            n = source.damage // 10
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
            game.log(f"  +{n*op['amount']} ({n} damage counters on self).")

    def _op_damage_per_opponent_prizes_taken(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        taken = 6 - len(opp.prizes)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + taken * op["amount"]
        game.log(f"  +{taken*op['amount']} (opponent took {taken} Prizes).")

    def _op_bonus_if_came_active_this_turn(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay) and getattr(source, "_came_active", False):
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + op["amount"]
            game.log(f"  +{op['amount']} (moved to Active this turn).")

    def _op_subjugating_chains(self, game, player, op, source, ctx):
        cands = [m for m in player.bench if "Darkness" in m.card.types and "Pecharunt" not in m.card.name]
        if not cands: return
        new = cands[0]; idx = player.bench.index(new)
        if player.active is not None: player.bench.append(player.active)
        player.active = player.bench.pop(idx)
        player.active.status.add("Poisoned"); player.active._came_active = True
        game.log(f"  Subjugating Chains: {player.active.name} is Active and Poisoned.")

    def _op_attach_basic_energy_from_discard_to_bench_named(self, game, player, op, source, ctx):
        name = op.get("name", "")
        targets = [m for m in player.bench if name in m.card.name]
        if not targets: game.log("  no valid Benched target."); return
        en = next((c for c in player.discard if c.is_energy and "Basic" in c.subtypes), None)
        if not en: game.log("  no Basic Energy in discard."); return
        player.discard.remove(en); targets[0].energy.append(en)
        game.log(f"  attaches {en.name} from discard to {targets[0].name}.")

    def _op_set_ex_boost(self, game, player, op, source, ctx):
        player.ex_boost = op["amount"]
        game.log(f"  your attacks do +{op['amount']} to opponent's Active ex this turn.")

    def _op_damage_per_named_in_play(self, game, player, op, source, ctx):
        nm = op["name"]
        n = sum(1 for m in player.all_pokemon() if nm in m.card.name)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} {nm} in play).")

    def _op_draw_per_opponent_bench(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        n = len(opp.bench); player.draw(n)
        game.log(f"  draws {n} (opponent's Benched count).")

    def _op_redeemable_ticket(self, game, player, op, source, ctx):
        n = len(player.prizes)
        cards = player.prizes; player.prizes = []
        game.rng.shuffle(cards); player.deck += cards  # to bottom
        new = []
        for _ in range(n):
            if player.deck: new.append(player.deck.pop(0))
        player.prizes = new
        game.log(f"  Redeemable Ticket: re-draws {n} Prize cards.")

    def _op_wallys_compassion(self, game, player, op, source, ctx):
        megs = [m for m in player.all_pokemon() if "Mega" in m.card.subtypes]
        target = next((m for m in megs if m.damage > 0), None)
        if not target:
            game.log("  Wally's Compassion: no damaged Mega ex."); return
        target.damage = 0
        for en in list(target.energy):
            target.energy.remove(en); player.hand.append(en)
        game.log(f"  Wally's Compassion fully heals {target.name} and returns its Energy to hand.")

    def _op_discard_whole_hand(self, game, player, op, source, ctx):
        player.discard += player.hand; player.hand = []

    def _op_damage_per_opponent_active_counter(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if opp.active:
            n = opp.active.damage // 10
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
            game.log(f"  +{n*op['amount']} ({n} counters on opponent's Active).")

    def _op_discard_own_energy(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay):
            for _ in range(op.get("n", 1)):
                if source.energy: player.discard.append(source.energy.pop())

    def _op_salvatore_evolve(self, game, player, op, source, ctx):
        for mon in player.all_pokemon():
            evo = next((c for c in player.deck if c.evolves_from == mon.card.name
                        and not c.data.get("abilities")), None)
            if evo:
                player.deck.remove(evo)
                prev = mon.card; mon.stack.append(evo)
                mon.turn_played = game.turn; mon.status = set()
                player.shuffle(game.rng)
                game.log(f"  Salvatore evolves {prev.name} -> {evo.name}.")
                return
        game.log("  Salvatore: no legal evolution found.")

    def _op_set_flag_ignore_wr(self, game, player, op, source, ctx):
        game._ignore_wr = True

    def _op_draw_until(self, game, player, op, source, ctx):
        need = op["n"] - len(player.hand)
        if need > 0:
            player.draw(need); game.log(f"  draws up to {op['n']} cards in hand (+{need}).")

    def _op_discard_all_energy_self(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay):
            n = len(source.energy)
            for en in list(source.energy): source.energy.remove(en); player.discard.append(en)
            if n: game.log(f"  discards all {n} Energy from {source.name}.")

    def _op_damage_per_counter_on_bench_named(self, game, player, op, source, ctx):
        total = sum(m.damage for m in player.bench if m.card.name.startswith(op["prefix"]))
        n = total // 10
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} counters on benched {op['prefix']} Pokemon).")

    def _op_heal_self(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay) and source.damage > 0:
            source.damage = max(0, source.damage - op["amount"])
            game.log(f"  heals {op['amount']} from {source.name}.")

    def _op_solar_transfer(self, game, player, op, source, ctx):
        # Move one Basic [G] Energy from a Pokemon that doesn't need it toward the Active attacker.
        dst = player.active
        if not dst: return
        for m in player.bench:
            en = next((x for x in m.energy if "Basic" in x.subtypes and "Grass" in x.provides()), None)
            if en:
                m.energy.remove(en); dst.energy.append(en)
                game.log(f"  Solar Transfer moves {en.name} from {m.name} to {dst.name}.")
                return

    def _op_heal_team_if_self_grass(self, game, player, op, source, ctx):
        if not (isinstance(source, PokemonInPlay) and any("Grass" in x.provides() for x in source.energy)):
            game.log("  Fermented Juice: no Grass Energy attached."); return
        dmg = [m for m in player.all_pokemon() if m.damage > 0]
        tgt = max(dmg, key=lambda m: m.damage) if dmg else None
        if not tgt:
            game.log("  Fermented Juice: nothing to heal."); return
        tgt.damage = max(0, tgt.damage - op["amount"])
        game.log(f"  Fermented Juice heals {op['amount']} from {tgt.name}.")

    def _op_attach_basic_energy_from_discard_to_self(self, game, player, op, source, ctx):
        if not isinstance(source, PokemonInPlay): return
        en = next((c for c in player.discard if c.is_energy and "Basic" in c.subtypes), None)
        if not en: game.log("  Charging Up: no Basic Energy in discard."); return
        player.discard.remove(en); source.energy.append(en)
        game.log(f"  Charging Up attaches {en.name} to {source.name}.")

    def _op_discard_bench_energy_for_damage(self, game, player, op, source, ctx):
        discarded = 0
        for m in player.bench:
            if discarded >= op["max"]: break
            while m.energy and discarded < op["max"]:
                player.discard.append(m.energy.pop()); discarded += 1
        if discarded:
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + discarded * op["per"]
            game.log(f"  Erasure Ball discards {discarded} Energy -> +{discarded*op['per']} damage.")

    def _op_bonus_if_team_rocket_energy(self, game, player, op, source, ctx):
        if isinstance(source, PokemonInPlay) and any(x.data.get("team_rocket_energy") for x in source.energy):
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + op["amount"]
            game.log(f"  +{op['amount']} (Team Rocket's Energy attached).")

    def _op_bench_snipe_per_counter(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        cands = [m for m in opp.bench if m.damage > 0]
        if not cands: game.log("  Strike the Sleeper: no damaged Benched Pokemon."); return
        tgt = max(cands, key=lambda m: m.damage)
        if game.damage_prevented(tgt, source, player): return
        dmg = (tgt.damage // 10) * op["amount"]
        tgt.damage += dmg
        game.log(f"  Strike the Sleeper: {dmg} to benched {tgt.name}.")

    def _op_ariana_draw(self, game, player, op, source, ctx):
        inplay = player.all_pokemon()
        all_tr = bool(inplay) and all("Team Rocket's" in m.card.name for m in inplay)
        goal = 8 if all_tr else 5
        need = goal - len(player.hand)
        if need > 0: player.draw(need)
        game.log(f"  Ariana draws up to {goal} ({'all TR' if all_tr else 'mixed'}).")

    def _op_team_rocket_archer(self, game, player, op, source, ctx):
        if player.pokemon_ko_last_opp_turn <= 0:
            game.log("  Archer: no Team Rocket's Pokemon KO'd last turn."); return
        opp = game.players[1 - game.players.index(player)]
        for p in game.players:
            p.deck += p.hand; p.hand = []; p.shuffle(game.rng)
        player.draw(op["you"]); opp.draw(op["opp"])
        game.log(f"  Archer: you draw {op['you']}, opponent draws {op['opp']}.")

    def _op_damage_per_energy_in_discard(self, game, player, op, source, ctx):
        n = sum(1 for c in player.discard if c.is_energy)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} Energy in discard).")

    def _op_search_discard_from_deck(self, game, player, op, source, ctx):
        cands = [c for c in player.deck if card_matches(c, op["filter"])][:op["count"]]
        for c in cands:
            player.deck.remove(c); player.discard.append(c)
        game.log(f"  searches deck and discards: {[c.name for c in cands] or 'nothing'}.")

    def _op_explorers_guidance(self, game, player, op, source, ctx):
        top = player.deck[:op["look"]]
        for c in top: player.deck.remove(c)
        take = top[:op["take"]]
        for c in take: player.hand.append(c)
        for c in top[op["take"]:]: player.discard.append(c)
        game.log(f"  Explorer's Guidance takes {[c.name for c in take]}, discards {len(top)-len(take)}.")

    def _op_bonus_if_stadium(self, game, player, op, source, ctx):
        if game.stadium:
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + op["amount"]
            game.log(f"  +{op['amount']} (a Stadium is in play).")

    def _op_return_pokemon_from_discard_to_hand(self, game, player, op, source, ctx):
        mon = next((c for c in player.discard if c.is_pokemon), None)
        if mon:
            player.discard.remove(mon); player.hand.append(mon)
            game.log(f"  returns {mon.name} from discard to hand.")
        else:
            game.log("  Dangle Tail: no Pokemon in discard.")

    def _op_seek_inspiration(self, game, player, op, source, ctx):
        if not player.deck:
            game.log("  Seek Inspiration: deck empty."); return
        top = player.deck.pop(0); player.discard.append(top)
        game.log(f"  Seek Inspiration discards {top.name}.")
        if not (top.is_pokemon and not top.has_rule_box):
            game.log("  Seek Inspiration: not a non-rule-box Pokemon."); return
        atks = top.data.get("attacks", [])
        best = max((a.get("damage") or 0 for a in atks), default=0)
        opp = game.players[1 - game.players.index(player)]
        if best and opp.active and not game.damage_prevented(opp.active, source, player):
            opp.active.damage += best
            game.log(f"  Seek Inspiration copies {top.name}'s attack for {best}.")

    def _op_damage_multi_opponent(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        targets = ([opp.active] if opp.active else []) + list(opp.bench)
        for m in targets[:op["targets"]]:
            if not game.damage_prevented(m, source, player):
                m.damage += op["amount"]
                game.log(f"  Trifrost: {op['amount']} to {m.name}.")

    def _op_search_basic_energy_to_bench(self, game, player, op, source, ctx):
        typ = op.get("type")
        if not player.bench: game.log("  no Bench to attach to."); return
        tgt = player.bench[0]; done = 0
        for _ in range(op["count"]):
            en = next((c for c in player.deck if c.is_energy and "Basic" in c.subtypes
                       and (not typ or typ in c.provides())), None)
            if not en: break
            player.deck.remove(en); tgt.energy.append(en); done += 1
        if done: game.log(f"  Delightful Kiss attaches {done} {typ} Energy to {tgt.name}.")

    def _op_discard_energy_type_for_bonus(self, game, player, op, source, ctx):
        if not isinstance(source, PokemonInPlay): return
        typ = op.get("type"); have = [e for e in source.energy if (not typ or typ in e.provides())]
        if len(have) >= op["count"]:
            for e in have[:op["count"]]:
                source.energy.remove(e); player.discard.append(e)
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + op["amount"]
            game.log(f"  discards {op['count']} {typ} -> +{op['amount']} damage.")

    def _op_move_all_damage_bench_to_opponent(self, game, player, op, source, ctx):
        srcs = [m for m in player.bench if m.damage > 0]
        if not srcs: game.log("  no damaged Benched Pokemon."); return
        opp = game.players[1 - game.players.index(player)]
        tgt = self.policy.choose_pokemon(game, opp.all_pokemon())
        if not tgt or game.bench_counters_blocked(tgt): return
        mv = srcs[0]; tgt.damage += mv.damage
        game.log(f"  moves {mv.damage} damage from {mv.name} to {tgt.name}.")
        mv.damage = 0

    def _op_draw_to_hand_size(self, game, player, op, source, ctx):
        need = op["n"] - len(player.hand)
        if need > 0: player.draw(need)
        game.log(f"  draws up to {op['n']} cards in hand.")

    def _op_max_rod(self, game, player, op, source, ctx):
        got = []
        for c in list(player.discard):
            if len(got) >= op["count"]: break
            if c.is_pokemon or (c.is_energy and "Basic" in c.subtypes):
                player.discard.remove(c); player.hand.append(c); got.append(c.name)
        game.log(f"  Max Rod returns {got or 'nothing'} from discard to hand.")

    def _op_damage_per_own_hand(self, game, player, op, source, ctx):
        n = len(player.hand)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} cards in your hand).")

    def _op_damage_per_own_damaged_bench(self, game, player, op, source, ctx):
        n = sum(1 for m in player.bench if m.damage > 0)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} of your Benched Pokemon have damage counters).")

    def _op_damage_per_opponent_active_energy(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        n = len(opp.active.energy) if opp.active else 0
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} Energy on opponent's Active).")

    def _op_rocket_feathers(self, game, player, op, source, ctx):
        tr = [c for c in player.hand if c.supertype == "Trainer" and "Supporter" in c.subtypes
              and c.name.startswith("Team Rocket's")]
        for c in tr:
            player.hand.remove(c); player.discard.append(c)
        n = len(tr)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  Rocket Feathers discards {n} Team Rocket Supporter(s) -> {n*op['amount']} damage.")

    def _op_damage_per_tr_supporter_in_discard(self, game, player, op, source, ctx):
        n = sum(1 for c in player.discard if c.supertype == "Trainer" and "Supporter" in c.subtypes
                and c.name.startswith("Team Rocket's"))
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  R Command: {n} Team Rocket Supporter(s) in discard -> {n*op['amount']} damage.")

    def _op_energy_retrieval(self, game, player, op, source, ctx):
        got=[]
        for c in list(player.discard):
            if len(got)>=op["count"]: break
            if c.is_energy and "Basic" in c.subtypes:
                player.discard.remove(c); player.hand.append(c); got.append(c.name)
        game.log(f"  Energy Retrieval returns {got or 'nothing'} to hand.")

    def _op_briar_arm(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if len(opp.prizes) == 2:
            player.briar_active = True
            game.log("  Briar armed: next KO this turn takes an extra Prize.")

    def _op_damage_per_heads(self, game, player, op, source, ctx):
        heads = sum(1 for _ in range(op["flips"]) if game.rng.random() < 0.5)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + heads * op["amount"]
        game.log(f"  {heads} heads -> {heads*op['amount']} damage.")

    def _op_opponent_shuffle_random_from_hand(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        for _ in range(op.get("count", 1)):
            if not opp.hand: break
            card = game.rng.choice(opp.hand)
            opp.hand.remove(card); opp.deck.append(card); opp.shuffle(game.rng)
            game.log(f"  {card.name} is shuffled from {opp.name}'s hand into their deck.")

    def _op_haughty_order(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        top = opp.deck[:op.get("look", 10)]
        best, best_dmg = None, -1
        for card in top:
            if card.is_pokemon:
                for atk in card.data.get("attacks", []):
                    d = atk.get("damage") or 0
                    for e in atk.get("effects", []) or []:
                        if e.get("op") == "damage_per_own_hand": d += len(player.hand)*e.get("amount",0)
                    if d > best_dmg: best, best_dmg = (card, atk), d
        game.rng.shuffle(opp.deck)
        if not best or best_dmg <= 0:
            game.log("  Haughty Order finds no usable attack."); return
        card, atk = best
        game.log(f"  Haughty Order copies {card.name}'s {atk['name']}.")
        if opp.active and not game.damage_prevented(opp.active, source, player):
            dmg = atk.get("damage") or 0
            for e in atk.get("effects", []) or []:
                if e.get("op") == "damage_per_own_hand": dmg += len(player.hand)*e.get("amount",0)
                elif e.get("op") == "status_opponent_active": opp.active.status.add(e["status"])
            dmg = max(0, dmg - game.damage_reduction(opp.active))
            opp.active.damage += dmg
            game.log(f"  {opp.active.name} takes {dmg}.")

    def _op_reconstitute(self, game, player, op, source, ctx):
        if len(player.hand) < 2: return
        # fuel-aware: if anyone in play has R Command, feed TR Supporters to the discard
        synergy = any(any(e.get("op") == "damage_per_tr_supporter_in_discard"
                          for a in m.card.data.get("attacks", []) for e in a.get("effects", []) or [])
                      for m in player.all_pokemon())
        if not synergy and len(player.hand) < 6: return   # card disadvantage; don't shred a thin hand
        def fuel(card):
            return card.supertype == "Trainer" and "Supporter" in card.subtypes \
                   and card.name.startswith("Team Rocket's")
        picks = sorted(player.hand, key=lambda x: (not fuel(x), x.is_pokemon))[:2]
        for card in picks:
            player.hand.remove(card); player.discard.append(card)
        player.draw(1)
        game.log(f"  Reconstitute discards {[x.name for x in picks]}, draws 1.")

    def _op_status_self(self, game, player, op, source, ctx):
        from engine import PokemonInPlay
        if isinstance(source, PokemonInPlay):
            source.status.add(op["status"])
            game.log(f"  {source.name} is now {op['status']}.")

    def _op_damage_per_opponent_bench(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        n = len(opp.bench)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} opponent Benched Pokemon).")

    def _op_foul_play(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if not opp.active: return
        best, bd = None, -1
        for atk in opp.active.card.data.get("attacks", []):
            d = atk.get("damage") or 0
            if d > bd: best, bd = atk, d
        if not best or bd <= 0:
            game.log("  Foul Play: no usable attack to copy."); return
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + bd
        game.log(f"  Foul Play copies {best['name']} for {bd}.")

    def _op_both_actives_ko(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        for side, m in ((player, player.active), (opp, opp.active)):
            if m is not None:
                try: m.damage = game.effective_max_hp(m)
                except Exception: m.damage = m.max_hp
        game.log("  Destined Fight: both Active Pokemon are Knocked Out.")

    def _op_garland_ray(self, game, player, op, source, ctx):
        from engine import PokemonInPlay
        if not isinstance(source, PokemonInPlay): return
        n = 0
        while n < op.get("max", 2) and source.energy:
            player.discard.append(source.energy.pop()); n += 1
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op.get("per", 120)
        game.log(f"  Garland Ray discards {n} Energy -> {n*op.get('per',120)} damage.")

    def _op_heal_pokemon_of_type(self, game, player, op, source, ctx):
        typ = op.get("type")
        cands = [m for m in player.all_pokemon() if m.damage > 0 and typ in m.card.types]
        if not cands:
            game.log(f"  nothing to heal."); return
        tgt = max(cands, key=lambda m: m.damage)
        healed = min(tgt.damage, op["amount"]); tgt.damage -= healed
        game.log(f"  heals {healed} from {tgt.name}.")

    def _op_bonus_if_opponent_active_damaged(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if opp.active and opp.active.damage > 0:
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + op["amount"]
            game.log(f"  +{op['amount']} (opponent's Active is damaged).")

    def _op_recoil(self, game, player, op, source, ctx):
        from engine import PokemonInPlay
        if isinstance(source, PokemonInPlay):
            source.damage += op["amount"]
            game.log(f"  {source.name} does {op['amount']} damage to itself.")

    def _op_damage_per_own_energy(self, game, player, op, source, ctx):
        from engine import PokemonInPlay
        if isinstance(source, PokemonInPlay):
            n = len(source.energy)
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
            game.log(f"  +{n*op['amount']} ({n} Energy attached).")

    def _op_torrential_heart(self, game, player, op, source, ctx):
        from engine import PokemonInPlay
        if not isinstance(source, PokemonInPlay): return
        source.damage += 50; player.th_bonus = getattr(player, "th_bonus", 0) + 120
        game.log(f"  Torrential Heart: 5 counters on {source.name}, attacks do +120 this turn.")

    def _op_metal_maker(self, game, player, op, source, ctx):
        top = player.deck[:4]
        ens = [c for c in top if c.is_energy and "Basic" in c.subtypes and "Metal" in c.provides()]
        rest = [c for c in top if c not in ens]
        for c in top: player.deck.remove(c)
        mons = player.all_pokemon()
        for i, en in enumerate(ens):
            mons[i % len(mons)].energy.append(en) if mons else player.discard.append(en)
        game.rng.shuffle(rest); player.deck += rest
        game.log(f"  Metal Maker attaches {len(ens)} Metal Energy (top 4 checked).")

    def _op_protect_charge(self, game, player, op, source, ctx):
        from engine import PokemonInPlay
        if isinstance(source, PokemonInPlay):
            source._reduce_next = op.get("amount", 30)
            game.log(f"  Protect Charge: takes {op.get('amount',30)} less next turn.")

    def _op_fill_bench_from_deck(self, game, player, op, source, ctx):
        put = []
        for c in list(player.deck):
            if not game.bench_has_room(player): break
            if c.is_basic_pokemon:
                player.deck.remove(c); player.hand.append(c)
                if game.play_basic_to_bench(player, c): put.append(c.name)
                else: player.hand.remove(c); player.deck.append(c); break
        player.shuffle(game.rng)
        game.log(f"  Precious Trolley benches {put or 'nothing'}.")

    def _op_energy_recycler_shuffle(self, game, player, op, source, ctx):
        got = []
        for c in list(player.discard):
            if len(got) >= op.get("count", 5): break
            if c.is_energy and "Basic" in c.subtypes:
                player.discard.remove(c); player.deck.append(c); got.append(c.name)
        player.shuffle(game.rng)
        game.log(f"  Energy Recycler shuffles {len(got)} Energy into the deck.")

    def _op_discard_tools_in_play(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        n = 0
        for side in (opp, player):           # opponent's tools first (that's the play)
            for m in side.all_pokemon():
                while m.tools and n < op.get("count", 2):
                    t = m.tools.pop(); side.discard.append(t); n += 1
                    game.log(f"  Tool Scrapper discards {t.name} from {m.name}.")
                if n >= op.get("count", 2): break
            if n >= op.get("count", 2): break
        if n == 0: game.log("  Tool Scrapper: no Tools in play.")

    def _op_kieran(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if opp.active and ("ex" in opp.active.card.subtypes or "V" in opp.active.card.subtypes):
            player.ex_boost = max(getattr(player, "ex_boost", 0), 30)
            game.log("  Kieran: +30 vs the opponent's Active ex this turn.")
        elif player.bench:
            best = max(range(len(player.bench)),
                       key=lambda i: max((a.get("damage") or 0) for a in player.bench[i].card.data.get("attacks",[{"damage":0}])))
            player.bench.append(player.active); player.active = player.bench.pop(best)
            player.active._came_active = True
            game.log(f"  Kieran switches {player.active.name} into the Active Spot.")

    def _op_spill_the_tea(self, game, player, op, source, ctx):
        n = 0
        for m in player.all_pokemon():
            for en in list(m.energy):
                if n >= op.get("max", 3): break
                if "Grass" in en.provides():
                    m.energy.remove(en); player.discard.append(en); n += 1
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op.get("per", 70)
        game.log(f"  Spill the Tea: discards {n} [G] -> {n*op.get('per',70)} damage.")

    def _op_rebrew(self, game, player, op, source, ctx):
        ens = [x for x in player.discard if x.is_energy and "Basic" in x.subtypes and "Grass" in x.provides()]
        if not ens: game.log("  Re-Brew: no [G] Energy in discard."); return
        opp = game.players[1 - game.players.index(player)]
        tgt = self.policy.choose_pokemon(game, opp.all_pokemon())
        if tgt and not game.bench_counters_blocked(tgt):
            tgt.damage += 20 * len(ens)
            game.log(f"  Re-Brew: {20*len(ens)} counters on {tgt.name}.")
        for x in ens: player.discard.remove(x); player.deck.append(x)
        player.shuffle(game.rng)

    def _op_heal_team_each(self, game, player, op, source, ctx):
        for m in player.all_pokemon():
            m.damage = max(0, m.damage - op["amount"])
        game.log(f"  heals {op['amount']} from each of your Pokemon.")

    def _op_heal_all_each(self, game, player, op, source, ctx):
        for pl in game.players:
            for m in pl.all_pokemon():
                m.damage = max(0, m.damage - op["amount"])
        game.log(f"  heals {op['amount']} from every Pokemon in play.")

    def _op_minus_per_self_counter(self, game, player, op, source, ctx):
        from engine import PokemonInPlay
        if isinstance(source, PokemonInPlay):
            game._atk_bonus = getattr(game, "_atk_bonus", 0) - (source.damage // 10) * op.get("per", 10)

    def _op_mill_opponent(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        for _ in range(op.get("n", 1)):
            if opp.deck: opp.discard.append(opp.deck.pop(0))
        game.log(f"  discards top {op.get('n',1)} of opponent's deck.")

    def _op_search_basic_energy_attach_bench_multi(self, game, player, op, source, ctx):
        typ = op.get("type"); n = 0
        for _ in range(op.get("count", 3)):
            en = next((x for x in player.deck if x.is_energy and "Basic" in x.subtypes
                       and (not typ or typ in x.provides())), None)
            if not en or not player.bench: break
            player.deck.remove(en)
            tgt = max(player.bench, key=lambda m: max((a.get("damage") or 0) for a in m.card.data.get("attacks",[{"damage":0}])))
            tgt.energy.append(en); n += 1
        player.shuffle(game.rng)
        if n: game.log(f"  attaches {n} Energy to the Bench.")

    def _op_excited_turbo(self, game, player, op, source, ctx):
        if not any("Mega" in m.card.subtypes and "Fire" in m.card.types for m in player.all_pokemon()):
            from engine import RuleError; raise RuleError("Excited Turbo: no [R] Mega in play.")
        n = 0
        for en in [x for x in list(player.hand) if x.is_energy and "Basic" in x.subtypes and "Fire" in x.provides()]:
            tgt = next((m for m in player.bench if "Fire" in m.card.types), None)
            if not tgt: break
            player.hand.remove(en); tgt.energy.append(en); n += 1
        if n: game.log(f"  Excited Turbo attaches {n} [R] Energy.")

    def _op_ninetailed_transfer(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        srcs = [m for m in player.bench if m.damage > 0]
        if not srcs or not opp.active: game.log("  Nine-Tailed Transfer: nothing to move."); return
        mv = max(srcs, key=lambda m: m.damage)
        opp.active.damage += mv.damage
        game.log(f"  moves {mv.damage} damage from {mv.name} to {opp.active.name}.")
        mv.damage = 0

    def _op_damage_per_own_basics(self, game, player, op, source, ctx):
        n = sum(1 for m in player.all_pokemon() if "Basic" in m.card.subtypes)
        game._atk_bonus = getattr(game, "_atk_bonus", 0) + n * op["amount"]
        game.log(f"  +{n*op['amount']} ({n} of your Basic Pokemon).")

    def _op_ko_if_exact_counters(self, game, player, op, source, ctx):
        opp = game.players[1 - game.players.index(player)]
        if opp.active and opp.active.damage == op.get("n", 6) * 10:
            try: opp.active.damage = game.effective_max_hp(opp.active)
            except Exception: opp.active.damage = opp.active.max_hp
            game.log("  Terminal Period: exactly 6 counters — Knocked Out!")
        else:
            game.log("  Terminal Period: condition not met.")

    def _op_power_press(self, game, player, op, source, ctx):
        from engine import PokemonInPlay
        if isinstance(source, PokemonInPlay) and len(source.energy) >= 2 + op.get("extra", 2):
            game._atk_bonus = getattr(game, "_atk_bonus", 0) + op["amount"]
            game.log(f"  +{op['amount']} (extra Energy attached).")

    def _op_electric_streamer(self, game, player, op, source, ctx):
        n = 0
        for en in [x for x in list(player.hand) if x.is_energy and "Basic" in x.subtypes and "Lightning" in x.provides()]:
            tgt = next((m for m in player.all_pokemon() if "Iono's" in m.card.name), None)
            if not tgt: break
            player.hand.remove(en); tgt.energy.append(en); n += 1
        if n: game.log(f"  Electric Streamer attaches {n} [L] Energy.")
        else:
            from engine import RuleError; raise RuleError("Electric Streamer: nothing to attach.")

    def _op_flashing_draw(self, game, player, op, source, ctx):
        from engine import PokemonInPlay, RuleError
        if not isinstance(source, PokemonInPlay): raise RuleError("no source")
        en = next((x for x in source.energy if "Lightning" in x.provides() and "Basic" in x.subtypes), None)
        if not en: raise RuleError("Flashing Draw: no [L] Energy to discard.")
        source.energy.remove(en); player.discard.append(en)
        need = op.get("n", 6) - len(player.hand)
        if need > 0: player.draw(need)
        game.log(f"  Flashing Draw: discards {en.name}, draws to {op.get('n',6)}.")

    def _op_roto_stick(self, game, player, op, source, ctx):
        top = player.deck[:op.get("look", 4)]
        sups = [x for x in top if x.supertype == "Trainer" and "Supporter" in x.subtypes]
        for x in sups: player.deck.remove(x); player.hand.append(x)
        player.shuffle(game.rng)
        game.log(f"  Roto-Stick takes {[x.name for x in sups] or 'nothing'}.")

    def _op_supporters_from_discard(self, game, player, op, source, ctx):
        got = []
        for x in list(player.discard):
            if len(got) >= op.get("count", 2): break
            if x.supertype == "Trainer" and "Supporter" in x.subtypes:
                player.discard.remove(x); player.hand.append(x); got.append(x.name)
        game.log(f"  Miracle Headset returns {got or 'nothing'}.")

    def _op_coin_gust(self, game, player, op, source, ctx):
        if game.rng.random() < 0.5:
            self._op_gust_opponent_bench(game, player, op, source, ctx)
        else:
            game.log("  Pokemon Catcher: tails.")

    def _op_static_lock_acespec_if_tool(self, *a): pass
    def _op_static_disable_self_ko_abilities(self, *a): pass
    def _op_stadium_tera_attack_tax(self, *a): pass


def _stage1_between(game, stage2_card: Card, basic: Card):
    """True if stage2 evolves from a Stage 1 that evolves from `basic`."""
    s1_name = stage2_card.evolves_from
    for cid, data in game_catalog(game).items():
        if data["name"] == s1_name and "Stage 1" in data.get("subtypes", []):
            if data.get("evolves_from") == basic.name:
                return True
    return False


def game_catalog(game):
    # all cards in play share the same catalog dict via their .data; rebuild a view
    cat = {}
    for p in game.players:
        for c in p.deck + p.hand + p.discard + p.prizes:
            cat[c.card_id] = c.data
        for m in p.all_pokemon():
            for c in m.stack: cat[c.card_id] = c.data
    return cat


def _is_first_player_turn1(game):
    return game.turn == 1
