# Fixes in this repository

One line per fix: what it changes, the issue and the pull request. Add a line after each merge. Every fix and every custom change goes through an issue and a pull request in this repository.

| Area | Fix | Issue | Pull request | Status | Upstream |
|---|---|---|---|---|---|
| Skills / effects | Hit effect for Priest buff skills | [#3](https://github.com/dkgigantone/ro3emu-plus/issues/3) | [#4](https://github.com/dkgigantone/ro3emu-plus/pull/4) | Merged | [aethai/ro3emu-server#429](https://github.com/aethai/ro3emu-server/pull/429) (open) |
| Skills / effects | Hit effect for Merchant, Blacksmith and Whitesmith buff skills | [#5](https://github.com/dkgigantone/ro3emu-plus/issues/5) | [#6](https://github.com/dkgigantone/ro3emu-plus/pull/6) | Merged | [aethai/ro3emu-server#430](https://github.com/aethai/ro3emu-server/pull/430) (open) |
| Skills / effects | Hit effect for Paladin and Royal Guard shield buffs (Auto Guard, Shield Reflect, Defending Aura, Recoil, Battle Chant) | [#7](https://github.com/dkgigantone/ro3emu-plus/issues/7) | [#8](https://github.com/dkgigantone/ro3emu-plus/pull/8) | Merged | [aethai/ro3emu-server#432](https://github.com/aethai/ro3emu-server/pull/432) (open) |
| Client hooks / config editor | Edited card effects show in the client card window (ConfigOverlay reads ro3emu/custom/config-overlay.tsv written by the config editor; card tables only) | [#10](https://github.com/dkgigantone/ro3emu-plus/issues/10) | [#11](https://github.com/dkgigantone/ro3emu-plus/pull/11) | Merged | Plus only, not sent upstream |
| Skills / effects | Hunter Blitz Beat shows the falcon flight (the cast goes out on the high-level routes with the falcon effect 13102200) | [#13](https://github.com/dkgigantone/ro3emu-plus/issues/13) | [#16](https://github.com/dkgigantone/ro3emu-plus/pull/16) | Merged | [aethai/ro3emu-server#442](https://github.com/aethai/ro3emu-server/pull/442) (open) |
| Skills / effects | Hunter Blitz Beat automatic proc (Basic Attack chance) shows only the falcon, flying once with its hit sound and no cast pose; Blitz Beat damage checked against the data for levels 1-5 | [#18](https://github.com/dkgigantone/ro3emu-plus/issues/18) | [#19](https://github.com/dkgigantone/ro3emu-plus/pull/19) | Merged | [aethai/ro3emu-server#443](https://github.com/aethai/ro3emu-server/pull/443) (open, stacked on #442) |
| Skills / effects | Ranger Warg Strike shows the wolf charge and plays the wolf hit sound (the cast goes out on the high-level routes with the wolf bullet 1331605 and effect 1331602, hit effect 1331204); the automatic cast from Focused Arrow Strike (every 2.5 s) shows the wolf too, and plain normal attacks do not trigger it | [#22](https://github.com/dkgigantone/ro3emu-plus/issues/22) | [#23](https://github.com/dkgigantone/ro3emu-plus/pull/23) | Merged | [aethai/ro3emu-server#444](https://github.com/aethai/ro3emu-server/pull/444) (open, stacked on #443) |
| Skills / effects | Ranger Unlimited Shooting: while the buff is active the Focused Arrow Strike slot shows the Unlimited Shooting version (mode 1 row) with the buff countdown and highlight, and returns to normal when it ends; the skill bar data (5025) is re-sent when a buff-selected skill mode starts, ends or is refreshed, with a cooldown entry for that group and mode | [#26](https://github.com/dkgigantone/ro3emu-plus/issues/26) | [#27](https://github.com/dkgigantone/ro3emu-plus/pull/27) | Merged | [aethai/ro3emu-server#445](https://github.com/aethai/ro3emu-server/pull/445) (open) |

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
