# Phase 2: learned policy

The heuristic bot in `sts2bot/heuristic.py` writes every decision it makes
to `data/trajectories/*.jsonl` via `sts2bot/logger.py` — one JSON line per
decision point: the full raw game state, the action taken, and which
`state_type` it was. That's the dataset for training a model that
eventually replaces the heuristic.

## Feature encoding (built)

Raw state has natural-language fields (`"Deal 32 damage."`) that a network
shouldn't have to parse. The fix: card/enemy/relic/potion identity and the
small closed-vocabulary fields (`type`, `rarity`, `target_type`, keyword
names, intent `type`) become integer ids, meant for `nn.Embedding` lookups
— `description` text is dropped entirely, it was only ever there for
human/LLM readability.

- `ml/vocab.py` — `Vocab`: token <-> int id, with `<pad>`=0 and `<unk>`=1
  always reserved. `encode()` never raises on an unseen token.
- `ml/build_vocab.py` — scans `data/trajectories/*.jsonl` and writes one
  vocab file per categorical field to `ml/vocab/*.json`. Only covers
  what's been seen in play so far; re-run after more sessions (or once
  there's a compendium dump handy) to widen coverage — embedding table
  sizes just need to grow to match on the next training run.
- `ml/encode.py` — `encode_combat_state(state, vocabs)` turns one
  monster/elite/boss state into fixed-size numpy arrays (hand ids/cost/
  type/target/keywords + a mask for padding, same for enemies, plus
  player scalars). `encode_combat_action(action, state)` maps the
  heuristic's chosen action to `(card_slot, target_slot)` labels aligned
  with those same array slots. The one bit of text parsing left is
  extracting a damage/block *magnitude* via a small regex
  (`_first_int`) — everything else is structured fields, no NLP.
- `ml/build_dataset.py` — runs the above over every logged trajectory and
  saves one `data/dataset/combat.npz` (numpy arrays, no JSON/text) ready
  to hand to a `torch.utils.data.Dataset`.

Only combat states are covered so far, since that's where "a hand of
cards" lives. Map/shop/rewards/etc. would follow the same pattern
(id/categorical fields -> Vocab, numeric fields stay numeric) if it's
worth a learned policy there too — those screens are simpler action
spaces and the current heuristic rules are already fairly serviceable.

To rebuild after more play sessions:
```
python -m ml.build_vocab
python -m ml.build_dataset
```

## Everything past this point is still just the plan

1. **Behavior cloning first.** Train a model to predict the heuristic's
   `card_slot`/`target_slot` from `encode_combat_state`'s output — this
   gets a working DNN without needing a reward signal or a training loop
   against the live game. `data/dataset/combat.npz` is that training set.
2. **Model shape.** Hand and enemies are small variable-length sets — a
   shared embedding + small self-attention or even just a masked-mean
   encoder over hand/enemy slots, feeding two heads (card_slot logits
   incl. an "end_turn" sentinel slot, and target_slot logits gated to
   only fire when the chosen card targets an enemy). Not built yet.
3. **Per-state-type heads, or one shared model?** Combat decisions (which
   card, which target) are a very different shape of problem than map
   routing or shop choices, if those get encoders too later. Likely
   cleanest as separate small heads rather than one monolithic model.
4. **Once behavior cloning matches or beats the heuristic**, look at
   fine-tuning against actual run outcomes (win/loss, floor reached, HP
   retained) — `menu_select`/`game_over` handling in `sts2bot/heuristic.py`
   is currently a stub, so closing that loop (auto-restart runs) is a
   prerequisite for any self-play/RL-style improvement.
