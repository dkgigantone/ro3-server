# Fixes in this repository

One line per fix: what it changes, the issue and the pull request. Add a line after each merge. Every fix and every custom change goes through an issue and a pull request in this repository.

| Area | Fix | Issue | Pull request | Status | Upstream |
|---|---|---|---|---|---|
| Skills / effects | Hit effect for Priest buff skills | [#3](https://github.com/dkgigantone/ro3emu-plus/issues/3) | [#4](https://github.com/dkgigantone/ro3emu-plus/pull/4) | Merged | [aethai/ro3emu-server#429](https://github.com/aethai/ro3emu-server/pull/429) (open) |
| Skills / effects | Hit effect for Merchant, Blacksmith and Whitesmith buff skills | [#5](https://github.com/dkgigantone/ro3emu-plus/issues/5) | [#6](https://github.com/dkgigantone/ro3emu-plus/pull/6) | Merged | [aethai/ro3emu-server#430](https://github.com/aethai/ro3emu-server/pull/430) (open) |
| Skills / effects | Hit effect for Paladin and Royal Guard shield buffs (Auto Guard, Shield Reflect, Defending Aura, Recoil, Battle Chant) | [#7](https://github.com/dkgigantone/ro3emu-plus/issues/7) | [#8](https://github.com/dkgigantone/ro3emu-plus/pull/8) | Merged | [aethai/ro3emu-server#432](https://github.com/aethai/ro3emu-server/pull/432) (open) |
| Client hooks / config editor | Edited card effects show in the client card window (ConfigOverlay reads ro3emu/custom/config-overlay.tsv written by the config editor; card tables only) | [#10](https://github.com/dkgigantone/ro3emu-plus/issues/10) | [#11](https://github.com/dkgigantone/ro3emu-plus/pull/11) | Merged | Plus only, not sent upstream |

## Custom content

| Area | Change | Issue | Branch | Status |
|---|---|---|---|---|
| Custom hats | Custom hat pipeline (client hooks, client tools patcher, `tools/custom-items` scripts). Game assets are not committed. | [#9](https://github.com/dkgigantone/ro3emu-plus/issues/9) | `custom-hats` | Work in progress, no pull request yet |

## Investigated, no change needed

| Report | Finding |
|---|---|
| Madogear Pilot does nothing | Works as designed: the mount is started with the HUD mount button. |
| Merchant NPCs (Milk, Fruit, Shrimp, ...) do not open a shop | The client data gives them a single dialogue line and no shop action; only the Novice Shop NPC, Kafra and the Equipment Merchant open a shop. Behavior of the original game is not verified. |
| Frost Diver level 10 rarely freezes | Matches the skill data: 10% chance, 1 second. |
