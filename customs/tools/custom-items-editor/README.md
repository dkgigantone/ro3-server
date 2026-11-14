# Custom Items Editor

A small browser tool for our custom wardrobe items (hats, face items ...). Start it with `Start-Editor.bat`
(or `RUN CUSTOM ITEMS EDITOR.bat` in the RO3-Studio folder). It opens http://127.0.0.1:8766.

It does not replace the build tools; it drives them (they sit in `..\custom-items`):

| Button | What it runs |
| --- | --- |
| Save changes | writes `custom-items.csv` (a backup of the last 20 copies is kept in `state\backups`) |
| Preview build | `build_custom_items.py --check` (writes nothing) |
| Save & Build | `build_custom_items.py` (server tables in `data\config`, `items.tsv`, aliases, default hat settings) |
| Import files from Origin | `import_hat.py <origin name> --slot <slot>` in its own window |
| Run with live editor | build, then `RUN RO3 DEV SERVER.bat` (server + game), then opens the Live tuning tab |

Live tuning is the old HatStudio in the browser: the sliders write `<client>\ro3emu\hats\<Key>M/F.txt` at once and the
running game picks the file up by itself.

Each item shows a checklist (list, server tables, items.tsv, model, settings, icons, aliases) so you can see what is missing.
The game folder defaults to `C:\Games\Ragnarok3-Official`; click the path in the header to change it.
Standard library only (Python 3.10+). Listens on 127.0.0.1 only.
