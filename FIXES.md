# Fixes in this repository

One line per fix: what it changes, the issue and the pull request. Add a line after each merge.

| Area | Fix | Issue | Pull request | Status |
|---|---|---|---|---|
| Skills / effects | Hit effect for Priest buff skills | (none yet) | [upstream #429](https://github.com/aethai/ro3emu-server/pull/429) | Open upstream, not yet merged here |
| Skills / effects | Hit effect for Blacksmith buff skills | (none yet) | [upstream #430](https://github.com/aethai/ro3emu-server/pull/430) | Open upstream, not yet merged here |
| Skills / effects | Hit effect for Paladin and Royal Guard shield buffs (Auto Guard, Shield Reflect, Defending Aura, Recoil, Battle Chant) | [upstream #431](https://github.com/aethai/ro3emu-server/issues/431) | [upstream #432](https://github.com/aethai/ro3emu-server/pull/432) | Open upstream, not yet merged here |

## Investigated, no change needed

| Report | Finding |
|---|---|
| Madogear Pilot does nothing | Works as designed: the mount is started with the HUD mount button. |
| Merchant NPCs (Milk, Fruit, Shrimp, ...) do not open a shop | The client data gives them a single dialogue line and no shop action; only the Novice Shop NPC, Kafra and the Equipment Merchant open a shop. |
| Frost Diver level 10 rarely freezes | Matches the skill data: 10% chance, 1 second. |
