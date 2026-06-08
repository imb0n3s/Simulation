# Simulation — log-trained Pokémon TCG engine + AI pilots

A Pokémon Trading Card Game rules engine, machine-readable card database, and AI
pilots reverse-engineered from real PTCG Live game logs. The goal isn't just legal
play — it's **human-like** play: behaviors (and *mistakes*) extracted one log at a
time, including losses and the lessons inside them.

Built for the current Standard metagame (Mega Evolution era): **336 cards**, **74
competitive archetypes**, calibrated to real tournament results.

## What's in the box

| File | What it does |
|---|---|
| `engine.py` | Rules engine: turn structure, energy/retreat/evolution, status, weakness, prizes (Mega ex = 3!), deck-out, the place-vs-do damage distinction, survive-the-hit promotion |
| `cards.json` | 336 cards as structured, machine-readable effects (researched card-by-card from limitlesstcg.com), ACE SPEC flags |
| `effects.py` | ~120 effect ops — every attack/ability/trainer is data, not code |
| `simulate.py` | AI pilots: `HeuristicAgent` (human-pattern library), `DeepSearchAgent` (3-turn branch-and-rollout planner). Includes a log-derived **human-error model** |
| `replay.py` | PTCG Live log verification harness (audits damage/KO math against real games) |
| `build_db.py` | Builds a queryable SQLite card/deck database |
| `decks/` | 74 metagame decklists in PTCG Live export format |
| `logs/` | Real anonymized PTCG Live game logs used as training data |
| `matchup_builder.py` | Builds a first/second-player matchup table from sims (resumable) |
| `calibrate.py`, `feedback_tune.py` | Real-results calibration: Bradley-Terry offsets + top-cut feedback, fit to 1,071 real tournament decks |
| `run_2500.py`, `run100_2500.py`, `run_900.py`, `run100_900.py` | Tournament engines: 2,500-player Swiss + Top-64 cut, all Bo3, 100-event Monte Carlo |

## The AI: a human-pattern library (21 behaviors)

`HeuristicAgent` and the planner carry behaviors traceable to specific logged moments —
each one added because a real game (often a loss) revealed it:

1. Lock-leads & item-lock preference   2. Smart energy routing
3. Sacrificial pivots                  4. Benching discipline (don't gift prizes)
5. Supporter choice discipline …       6. …balanced with situational greed
7. Information before commitment (draw abilities before choosing the supporter)
8. Prize-aware attack selection        9. Spread targeting (skip protected; bench KOs first)
10. Closer promotion                   11. Refrain defence (don't balloon the hand vs hand-punishers)
12. Laundering-aware counter placement (vs Munkidori)
13. Balanced greed (scarcity-weighted keepers)
14. Boss/gust discipline (never gust what counters already reach)
15. Counter conversion (Adrena-Brain finishes KOs first, sets up spread-KOs second)
16. Fastest-win racing (a win this turn beats a win next turn)
17. The answer-card is in your trainers: hand-disruption vs scaling-engine decks
18. Survive-the-hit promotion (wall with a body that lives through the known attack)
19. Succession-aware search (fetch the next attacker + energy when the active is dying)
20. Threat-denial targeting (KO count-scaling attackers like Beedrill — drop their multiplier)
21. Spread bench hygiene (keep chipped 1-prize bodies out of Phantom-Dive range)

### The human-error model

Real tournaments are played by people who slip — so the sim's pilots do too. Five
log-derived mistake modes fire at a tunable rate (5% per decision for the planner,
10% for the field): discard slips (paying a cost with the answer card), gust slips
(bossing the wrong target), hand hoarding, bench greed, and fumbling a found line.
Modeling error made the sim's top-cut composition fit reality *better*, not worse.

## Real-results calibration

Raw win rates over-rate beatsticks and under-rate control/combo. The calibration layer
fits per-archetype strength to **1,071 real tournament decks** and is validated against
the Utrecht / Indianapolis / Aichi Regional top cuts at **0.37% mean error per
archetype** — better than uncalibrated, and better with the human-error model on than off.

## The design idea: place vs. do

The engine's central distinction — learned the hard way from logs — is that **doing
damage** (attacks) and **placing damage counters** (abilities, trainers, spread) are
different events that different cards prevent. Mysterious Rock Inn / Tera bench immunity
stop *attack damage* but not placed counters; Mortal Shuriken and Adrena-Brain sail
through walls that hard-counter ex attackers. Every damage event carries a `_dmg_source`
tag and every prevention checks it; `replay.py` audits the do-vs-place census per game.

## Quick start

```bash
python build_db.py                              # build the card/deck database
python replay.py logs/<somelog>.txt             # verify the engine against a real game
python matchup_builder.py                        # build the matchup table (rerun until COMPLETE)
python run100_2500.py                            # 100 x 2,500-player Bo3 events -> placement distribution
```

## Honest limitations

- Win rates are calibrated estimates, not guarantees; read calibrated tournament output, not raw rows.
- A handful of cards are marked `[approx]` in `cards.json` where full rider text wasn't available.
- Card coverage is the 336 cards used by the included 74 decks, not the full Standard pool.

## Legal

Non-commercial fan project for AI research and deck testing. Pokémon, the Pokémon TCG,
card names, and card text are © The Pokémon Company, Nintendo, Game Freak, and Creatures.
Not produced by, endorsed by, or affiliated with them. Card text referenced from
[Limitless TCG](https://limitlesstcg.com). Code is MIT-licensed; card data remains the
property of its owners.
