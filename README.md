# PTCG Sim — a log-trained Pokémon TCG simulator

A Pokémon Trading Card Game rules engine, card database, and AI pilot trained on
real PTCG Live game logs. The goal isn't just legal play — it's **human-like**
play: the bot's behaviors were reverse-engineered from real games, one log at a
time, including losses and the lessons inside them.

Built around the current Standard metagame (Mega Evolution era: TWM → ASC sets,
231 cards, 35 competitive decklists).

## What's in the box

| File | What it does |
|---|---|
| `engine.py` | Core rules engine: turn structure, energy/retreat/evolution, status, weakness, prizes (Mega ex = 3!), win conditions incl. deck-out |
| `cards.json` | 231 cards with structured, machine-readable effects (researched card-by-card from limitlesstcg.com) |
| `effects.py` | ~90 effect ops interpreter — every attack/ability/trainer is data, not code |
| `simulate.py` | The AI pilots: `HeuristicAgent` (human-pattern library), `SearchAgent` (1-ply), `DeepSearchAgent` (rollout search) |
| `replay.py` | PTCG Live log verification harness — checks the engine against real games |
| `build_db.py` | Builds a queryable SQLite card/deck database from `cards.json` + decklists |
| `decks/` | 35 decklists in PTCG Live export format |
| `logs/` | Real anonymized-ish PTCG Live game logs used as training data |
| `matchup_builder.py` | Builds a first/second-player matchup table from real sims (resumable) |
| `tournament.py`, `bracket.py`, `run_900.py`, `run100_900.py` | Tournament tools: round robins, Swiss + Top-64 single-elim events (all Bo3), 100-event Monte Carlo |
| `self_play.py`, `iterate.py`, `measure_v2.py` | Self-play stats, value-model experiments, agent A/B measurement |

## Quick start

Python 3.10+. No dependencies for the core (only `iterate.py` wants `numpy`).

```bash
# build the card/deck database
python build_db.py

# verify the engine against a real game log
python replay.py logs/g_win_vs_venusaur.txt

# watch two decks play one game
python - <<'PY'
from engine import Game, Player, Card, load_catalog
from simulate import build_deck, HeuristicAgent
from effects import Effects, SkilledPolicy

cat = load_catalog()
agent = HeuristicAgent(Effects(SkilledPolicy()))
a = [c.card_id for c in build_deck("decks/greninja_dragapult.txt")]
b = [c.card_id for c in build_deck("decks/dragapult_dudunsparce_ex.txt")]
g = Game(Player("P1", [Card(c, cat) for c in a]),
         Player("P2", [Card(c, cat) for c in b]), seed=42)
g.setup()
while not g.winner and g.turn < 60:
    if not g.start_turn(): break
    agent.take_turn(g, g.current)
    g.end_turn()
print("\n".join(g.log_lines))
PY

# run a full 900-player Bo3 event (Swiss + Top 64 cut, resumable — rerun until FINAL)
python run_900.py

# 100 full events -> placement distribution (needs the matchup table)
python matchup_builder.py   # rerun until it prints TABLE COMPLETE
python run100_900.py
```

## The design idea: place vs. do

The engine's central distinction — learned the hard way from real logs — is that
**doing damage** (attacks) and **placing damage counters** (abilities, trainers,
poison) are different events that different cards prevent:

- Tera bench immunity, Mysterious Rock Inn, Cornerstone's stance block **attack
  damage** — but Mortal Shuriken's placed counters sail through.
- Battle Cage blocks **placed counters** on benches — but Jetting Blow's bench
  snipe goes right past it.

Every damage event carries a `_dmg_source` tag and every prevention effect
checks it. `replay.py` audits this against real logs (it reports a
do-damage vs place-counter census per game).

## The human-pattern library

`HeuristicAgent` carries behaviors extracted from real games, each traceable to
a specific log moment:

1. Lock-leads (open with the item-locker, pivot out when attackers are ready)
2. Early Itchy Pollen item-lock preference over chip damage
3. Smart energy routing (fund the bench attacker once the active is paid up)
4. Sacrificial pivots (feed a cheap 1-prize body to protect the engine)
5. Benching discipline (don't bench what you don't need — gust bait control)
6. Supporter choice discipline (don't Lillie's away a working hand…)
7. …balanced with situational greed (dig when behind, keepers are replaceable, or the opponent is closing)
8. Information before commitment (draw abilities BEFORE choosing the supporter)
9. Prize-aware attack selection (a 2-prize bench double-KO beats a 1-prize active KO)
10. Spread targeting (never waste a hit on a protected body; bench KOs over active KOs; kill evolving engine pieces)
11. Closer promotion (retreat into the bench attacker when it wins now)
12. Refrain defence (vs hand-punish attackers, never balloon your hand)
13. Laundering-aware counter placement (vs Munkidori, place where it can't profitably move)

## Honest limitations

- The bot over-performs straightforward beatstick decks and under-performs
  control/combo relative to real tournament results. `data/calibrated_tier_list.txt`
  re-ranks archetypes by *real* points-per-deck (events 535/544/559, 1071 decks)
  to correct for this — read it before trusting raw win rates.
- Negative results are kept honest: deeper rollouts hit a rollout-policy ceiling,
  and a linear value model + greedy policy iteration *regressed* vs. the
  heuristic (see `iterate.py`). The human-pattern library beat both.
- Card coverage is the 231 cards used by the included 35 decks, not the full set.

## Extending it

- **New cards**: add an entry to `cards.json` (copy a similar card's structure).
  If it needs a new mechanic, add an `_op_<name>` method in `effects.py`.
- **New decks**: drop a PTCG Live export in `decks/`, run `python build_db.py`.
- **Train from your logs**: save a PTCG Live log as text, run
  `python replay.py logs/yourlog.txt` — it flags any card the catalog is
  missing and audits damage math against the engine.

## Legal

This is a non-commercial fan project for AI research and deck testing.
Pokémon, the Pokémon TCG, card names, and card text are © The Pokémon Company,
Nintendo, Game Freak, and Creatures. This project is not produced by, endorsed
by, or affiliated with them. Card text was referenced from the excellent
[Limitless TCG](https://limitlesstcg.com) database. The code is MIT-licensed;
the card data remains the property of its owners.
