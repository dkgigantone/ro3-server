# RO3 Plus Config Editor

A small local web app for editing RO3Emu game data without touching JSON.
- **Skills** page: cast time, fixed cast time, action time, attack-speed effect, cooldown and shared (global) cooldowns.
- **Items** page (Equipment tab): base stat Min/Max per stat (add/remove stats), level needed, enchant slots. Random extra stats and special effects are shown read-only. - **Items page, Cards tab**: stats of each card at each of its 4 stars, plus the shared level-up bonuses (shared by every card of the same slot and quality). Special effects are view only. Usable & other items come next.
- **Shops** page: Fashion, Shop and Point Mall tabs. Edit purchase limit, limit period, currency and price of existing rows (and the item id of normal item rows). Export/import a CSV for manual editing.

## Run
Double-click `start.bat` (needs Python 3.10+; nothing else to install). Your browser opens at http://127.0.0.1:8765.
It edits the game folder shown in the top bar (default: `RO3 OFFICIAL SERVER FILES V2`). Use **Change folder** to pick another.

## How it works
1. Pick a skill, change the numbers (seconds). Edits are saved at once to `overrides/skills.json`, a small readable file. **That file is what you commit to git.**
2. Press **Apply to game**. The tool writes `data/config/SkillConfig.json` = untouched original + your edits (a backup is made first).
3. Restart the server so it loads the new values.

The untouched original lives in `state/` (not committed). **Put the untouched original back** and **Discard all my edits** are under *More*.

## Safety
- Only the five timing fields and the shared-cooldown times can be changed. Everything else in the file is left alone.
- Values are checked (whole numbers in range) before they are saved.
- If `SkillConfig.json` is changed by something else (a server update, another tool), Apply is paused until you choose *Use current file as new original*. Your edits are kept and laid on top of the new file.
- Listens on 127.0.0.1 only and refuses requests from other websites.

## Notes
- Some skills have **alternate versions** (a talent or mount switches the skill to another row with its own timings). Pick the version above the level chips.
- *Apply my edits to all levels* applies only to the levels of the version you are looking at.
- The live preview uses the formulas read from the server code and `PublicParamConfig.json`: cast speed = 2 per DEX point (cap 400), variable cast reduced by `3*cspd/(800+cspd)`, action time divided by `1 + (ASPD-1) * influence`. AGI to ASPD (2 per point) is an estimate.

## Items notes
- Edits are kept in `overrides/items.json` (commit this too). Apply writes only the files you edited: `ItemConfig`, `EquipConfig`, `CardConfig`, `CardUpgradeConfig` (backups first).
- Each item a player receives rolls a value between Min and Max when it is created. Changes only affect items created after the server restarts; items players already own keep their old rolls.
- Percent stats use 100 = 1% (LIKELY; the page shows the converted percentage).
- Card stats are read live from `CardConfig` / `CardUpgradeConfig`, so card edits apply to cards players already own after the restart (CONFIRMED in CharacterService.EntityData).

## Shops notes
- Edits are kept in `overrides/shops.json` (commit this too). Apply writes only `data/config/ShopCommodityConfig.json` (backup first; the last 5 are kept in `state/`). Restart the server afterwards.
- The server charges from this file: price = list price x discount (basis points) rounded up. The **Pays** column shows that result for discounted rows.
- Limit 0 = unlimited. A limit above 0 needs a period (1 daily, 2 weekly, 3 monthly, 4 lifetime, 5 season); the page flags a limit with period 0.
- Fashion rows sell a look (WardRobe id); their look cannot be changed here. Fashion class labels are our best reading of the data.
- **Export CSV** gives one line per row. In **Import CSV** leave a cell blank to keep the current value; a preview lists every change and nothing is imported unless all lines are valid.
- Apply also writes the shop lines (price, limit, period) into `client-overlay/config-overlay.tsv`, the same file the Items page uses for cards (each page replaces only its own lines). Copy it to `<client>\ro3emu\custom\` and restart the client so the shop list, buy popup and tooltip show the edited values. Needs the client hook build that accepts `ShopCommodityConfig`.
- **+ Add item** (bar above each item tab): enter an item ID (its English name shows live), currency, price, limit and period. The editor copies the tab's last item row, gives it the next row ID and the next sort position, and stores it under `Added` in `overrides/shops.json`. Apply writes the row into `ShopCommodityConfig.json` and `@clone` lines into the client overlay; the client hook adds the row to the tab when the shop data loads. Only items the client already knows (ItemConfig) work; fashion tabs and custom items are not supported yet. The red cross removes an added item.

## Descriptions notes
- The **Descriptions** page changes the item description the game client shows. Search an item by ID or English name, edit the text, press Save. Edits live in `overrides/texts.json` (commit it).
- Client only: Save writes `Text` and `ItemConfig _iDescription` lines into `client-overlay/config-overlay.tsv` and copies it into `<client>\ro3emu\custom\`. Restart the client to see the change. The server is not touched.
- An item with no description gets a new text id (99920000000 + item id). An item that already has one keeps its id, so every item that shares that text shows the new words (the page says how many).
- **Skills** mode of the same page edits a skill's description. A skill has one text template shared by all its level rows (and sometimes by other skills; the page says how many). The template keeps `${n}` (numbers from the level row, filled in by the client) and `^{n}` (colour tags); the Preview list shows the result for each level. Edits live in `overrides/texts.json` under `Skills`. Only skills that already have a description can be edited (2,393 skill rows have none). Needs the client hook build that fills `${n}` and `^{n}` for own texts.

Suggest button: on the Descriptions page, Suggest fills the box from the item's data (restore amount, buff text, cooldown, required level). It only knows a few effect types (restore HP/SP, buffs, unlock appearance/mount/emoji, world horn); for anything else it says so and you write the text by hand. Nothing is saved until you press Save.

## Mobs notes
- The **Mobs** page lists the 2,289 monster rows of `NPCConfig`. Filter by name or id, level range, normal / boss type / MVP list (`MVPConfig`), race, size and element; sort by level, name, HP or id. Pick a monster to edit it; edits live in `overrides/mobs.json` (commit it).
- Editable: HP, SP, Atk, Matk, Pdef, Mdef, Hit, Flee, Crit, Base EXP, element, race, size, walk speed and chase range (m). A stats row is shared by up to 240 monsters, so Apply never changes it: the edited monster gets its own `AttriMonsterConfig` row (800000000 + npc id) holding the final numbers and an empty `_kAttriScale`. A chase edit gets its own `AIConfig` row (810000000 + npc id). Monsters on the aggressive AI 20000004 cannot have their chase range edited (the server keys aggression to that AI id).
- **Base EXP / Job EXP:** Job EXP is not a per-monster value: the server pays 20 % of the Base EXP (`PublicParamConfig.BaseToJobConversionPercent`), so the two fields on the page are linked and either one sets the Base EXP. The number is the Base EXP per kill before the killer's level-gap correction and any team or field-rate bonus. The monster gets its own copy of its drop chain (new rows in `MultiverseGroupConfig`, `NPCDropConfig`, `NpcNormalDropConfig`, `DropPoolConfig`, `DropGroupConfig`, ids from 900000000 up) with the exp rows scaled; 2,082 of the 2,289 monster rows give no Base EXP today, and for those a number above 0 adds a new exp drop. Bonus Time rows are not changed.
- Rows with no English name (test and template rows) are hidden unless you tick *show unnamed rows*.
- Read-only: attack range, attack interval (ASPD) and skill range (from the monster's skills in `SkillConfig`), and view range. Race 7 (Fish) and 10 (Dragon) names are our best reading.
- Apply rebuilds `NPCConfig.json`, `AttriMonsterConfig.json`, `AIConfig.json` and the five drop tables from the untouched originals plus your edits, with a backup of all three first. Server only: restart the server. Nothing is copied to the client.
- **Item drops (editable):** each drop shows its chance per roll (pool chance times row weight; approximate when a pool picks several rows), rolls per kill, quantity range, the player level range it drops in, and its level penalty plan. You can change them, remove a drop, or add an item by name or id. *Save drops* stores the whole list for that monster. On Apply the monster's own copy of its drop pools loses its item slots (Base EXP slots stay) and the list is written as new `NpcNormalDropConfig` rows (ids from 1500000000 up), one pool slot and one single-row group per entry, so every entry rolls on its own: a one-of-several group becomes separate rolls on the edited monster. Quest and map limits a drop already had are kept. Shared rows and Bonus Time are not changed.
- **Level penalty (`DropAttenuationConfig`):** a drop row with a penalty plan has its quantity scaled by the gap between the player's level and the monster's level (plan 1: 100 % from -9 to 10 either side then 70 / 30 / 0 %, plan 2: 100 % from -9 to 10, 50 % further out, then 0 %). Type a player level in *Check at player level* to see which drops apply at that level and what the penalty does. Only three client rows use a plan today; you can set a plan on any drop. The plans themselves are edited on the **Level penalty** page.
- **Spawn maps (editable):** the **Spawn maps** section of a monster lists the public maps it spawns on (from `data/sceneeditor/<scene>_sceneeditor.json`, only maps listed in `MapConfig`). In a **pool group** each monster spawns a random number from min to max when the map line loads; you can change min/max, remove the monster from a group, or add it to any pool group of any map (pick the map and group, set min and max). A group with fewer spawn points than the monsters ask for spawns fewer, shown with a `!`. A killed monster returns after the group's respawn time. **Fixed groups** (each point has its own monster) only allow changing how many points are filled at once; points are not added or removed. `Ro3Emu:SpawnLimits` in the server settings can still lower a pool count.
- Spawn edits are saved in `overrides/spawns.json` and have their own **Apply spawns to game**, **Restore original spawn files** and **Use current scene files as new original** buttons (separate from Apply to game above). Apply rewrites only the scene files you touched from an untouched copy, with a backup first (5 kept per file); values are identical but numbers may print shorter. Restart the server after applying. Nothing goes to the client.
- Not in this version: adding or removing fixed spawn points, new map positions, dungeon/instance spawns, attack range and ASPD.

## Level penalty page

How much a kill is worth when the monster and the player are far apart in level, written like rAthena's `level_penalty` table: a short list of steps, each a **Difference** and a **Rate**.

- **Difference** = monster level minus player level (+10: the monster is 10 levels higher, -10: 10 levels lower). The server itself stores the gap the other way round (player level minus monster level, `_iBaseLvGap`), the page converts.
- **Rate** = percent of the normal reward. 100 is full, 0 is nothing, above 100 is a bonus (useful for EXP).
- A step with Difference 0 or more holds from its Difference up to the next higher step; a step below 0 holds from its Difference down to the next lower step. The last step on each side holds for everything beyond. Before the first step on each side the rate is 100. The right-hand table shows the result range by range, and *Check one case* answers "level X player, level Y monster".
- **EXP tab** edits `LvExpCorrectionConfig.json` (401 rows, one per gap -200..200). The server multiplies the Base EXP of a kill by the row's `_iLvDifferenceCorrection` / 10000 (`RewardService.ResolveKill`), Job EXP follows Base EXP, and the team bonus, field rate and catch-up bonus come after it. Only that one field is changed, the other columns stay. Rates up to 1000 are accepted.
- **Drop tab** edits the existing plans of `DropAttenuationConfig.json` (plan 1 and plan 2). A drop row that chooses a plan has its item count scaled by the plan's rate; a drop without a plan is never reduced, so this is not a global drop penalty like rAthena's. The page shows which `DropGroupConfig` rows use each plan. Choose the plan of a drop on the Mobs page. No new plans can be created here. An edited plan is rewritten as one row per run of equal rate (old row ids reused first, new ones above the table's highest).
- Edits are saved in `overrides/levelpenalty.json`. **Apply to game** rewrites only the table you edited from the untouched original plus your steps, with a backup first (5 kept per table); with no edits left the original file is put back. Restart the server after applying. Nothing goes to the client.
- Buttons under **More**: put the untouched originals back, discard all my edits, use the current files as the new originals (if a table was changed outside the tool).
