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
