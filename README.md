# Simulation — Version 0.01

A log-trained Pokémon TCG simulator.

A Pokémon Trading Card Game rules engine, card database, and AI pilot trained on
real PTCG Live game logs. The goal isn't just legal play — it's **human-like**
play: the bot's behaviors were reverse-engineered from real games, one log at a
time, including losses and the lessons inside them.

Built around the current Standard metagame (Mega Evolution era: TWM → ASC sets,
301 cards, 74 competitive decklists).

## What's new in 0.01

74 decks, 301 cards, a real-results calibration layer (`calibrate.py` + `feedback_tune.py`) that fits per-archetype strength to 1,071 real tournament decks, validated against the Utrecht/Indianapolis/Aichi top cuts (mean error 0.51% per archetype), and a 2,500-player Bo3 tournament pipeline.

### Build update (June 2026): the multi-turn planner

`DeepSearchAgent` is now a true multi-turn planner: it branches over **every
gust target x every affordable attack x bench-promotion lines**, rolls each
branch 3 turns into the future (3 sampled rollouts, piloted by the full
human-pattern heuristic), and ranks branches by **(worst-case, mean)** — taking
guaranteed wins by the FASTEST route. This overturned an earlier negative
result: with a strong rollout policy, depth pays (+67 wins/400 vs the
heuristic alone in A/B).

Two lessons trained straight from real misplays in the logs:

- **"Bossed up the wrong Pokemon"** — the gust never wastes itself on a body
  that counters/spread already reach (`_remote_reach`); it pulls up what ONLY
  the gust can reach, and lets Munkidori/spread finish the damaged one.
  A reconstructed game state (Boss + Munkidori + Phantom Dive, near-dead
  Zoroark + healthy Pecharunt benched) is the regression test: both agents now
  find the 4-prize, win-a-turn-earlier line.
- **"Could have won a turn earlier"** — guaranteed wins race each other:
  branch value is `1e6 - turn`, so a win NOW always outranks a win next turn.

Munkidori's Adrena-Brain is conversion-aware: it first checks whether moving
counters *finishes* a KO; otherwise it banks them where this turn's spread can
finish the job.

## What's in the box

| File | What it does |
|---|---|
| `engine.py` | Core rules engine: turn structure, energy/retreat/evolution, status, weakness, prizes (Mega ex = 3!), win conditions incl. deck-out |
| `cards.json` | 301 cards with structured, machine-readable effects (researched card-by-card from limitlesstcg.com) |
| `effects.py` | ~115 effect ops interpreter — every attack/ability/trainer is data, not code |
| `simulate.py` | The AI pilots: `HeuristicAgent` (human-pattern library), `SearchAgent` (1-ply), `DeepSearchAgent` (3-turn branch-and-rollout planner) |
| `replay.py` | PTCG Live log verification harness — checks the engine against real games |
| `build_db.py` | Builds a queryable SQLite card/deck database from `cards.json` + decklists |
| `decks/` | 74 decklists in PTCG Live export format |
| `logs/` | Real anonymized-ish PTCG Live game logs used as training data |
| `matchup_builder.py` | Builds a first/second-player matchup table from real sims (resumable) |
| `tournament.py`, `bracket.py`, `run_2500.py`, `run100_2500.py` | Tournament tools: round robins, 2,500-player Swiss + Top-64 single-elim events (all Bo3), 100-event Monte Carlo |
| `calibrate.py`, `feedback_tune.py` | Real-results calibration: Bradley-Terry offsets fit to 1,071 real tournament decks + top-cut feedback |
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

# run a full 2,500-player Bo3 event (Swiss + Top 64 cut, resumable — rerun until FINAL)
python run_2500.py

# 100 full events -> placement distribution (needs the matchup table)
python matchup_builder.py   # rerun until it prints TABLE COMPLETE
python run100_2500.py
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
14. Gust discipline (never Boss up what counters already reach; pull what only the gust reaches)
15. Counter conversion (Adrena-Brain finishes KOs first, sets up spread-KOs second)
16. Fastest-win racing (a win this turn always beats a win next turn)

## Honest limitations

- Raw sim win rates over-perform beatsticks and under-perform control/combo;
  the calibration layer (`calibrate.py` + `feedback_tune.py`) corrects archetype
  strength against 1,071 real tournament decks and is validated to a 0.51% mean
  abs error per archetype vs three real Regional top cuts. Use calibrated
  tournament results, not raw win rates.
- Negative results are kept honest — and revisited: an early "rollout-policy
  ceiling" result was overturned once the rollout policy got strong (the
  multi-turn planner now beats the heuristic +67/400). A linear value model +
  greedy policy iteration still *regressed* vs. the heuristic (see `iterate.py`).
- Card coverage is the 301 cards used by the included 74 decks, not the full set.

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
