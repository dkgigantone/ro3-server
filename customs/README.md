# customs — everything we added on top of the official RO3Emu files

This folder is the ONE place for our own work. The rest of the repository stays as close to the official RO3Emu code as
possible, so updates can still be merged. If it is ours and not official, it lives here.

## What is in here

| Folder | What it is |
| --- | --- |
| `ClientHooks/` | C# code that runs INSIDE the game client: custom assets loader (`CustomAssets`, `CustomPack`), custom hats (`HatSwap`, `HatAnim`, `HatOptions`, `HatStudio`), wardrobe additions (`WardrobeCustom`, `WardrobeProbe`), diagnostics (`ResourceProbe`, `BodyProbe`). Compiled into `Ro3Emu.ClientHooks.dll` through `src/Ro3Emu.ClientHooks/Ro3Emu.ClientHooks.csproj`. |
| `ClientTools/` | `ResourceProbePatch.cs`: the client patch that makes the game's resource loader ask `CustomAssets` first. Compiled into `Ro3Emu.ClientTools` and `Ro3Emu.Setup`. |
| `tools/custom-items/` | Python/cmd tools that import Origin hats and build the custom item rows (`custom-items.csv`, `import_hat.py`, `build_custom_items.py`, `pack_custom.py`). |
| `tools/rogue/` | `convert_body.py`, `read_hskf.py`: turn an Origin job body (Rogue male) into the file the client loads in place of an official body. |
| `unity/` | Unity editor scripts and shader used to inspect/build Origin job models (copy into a Unity 2022.3.62f3 project). |
| `docs/` | How the custom systems work and how to use them. |
| `scripts/` | `Deploy-Customs.ps1`: copies `assets/` into the client. |
| `assets/` | Our art/model files (Origin data). **Not committed** (see `.gitignore`); this folder is where they are kept so they are all in one place. |

## Where the client lives

Customs are tested against `C:\Games\Ragnarok3-Official` (the official client). Custom files go in
`<client>\ro3emu\custom\` (`models\`, `icons\`); the loader reads loose files first, then `eclipse.dat`.

## Touch points in official files (the only edits outside this folder)

Kept tiny so merges stay easy:

- `src/Ro3Emu.ClientHooks/LuaPatches.cs`: one line, `WardrobeCustom.Run(global);` after `ConfigOverlay.Install(...)`.
- `src/Ro3Emu.ClientHooks/UiTweaks.cs`: `HatSwap.Animate()`, `HatSwap.Tick()`, `HatStudio.Tick()` calls.
- `src/Ro3Emu.ClientHooks/TextureSwap.cs`: replacement PNGs from `<client>\ro3emu\icons` (`IconDir`, `TryDisk`).
- `src/Ro3Emu.ClientHooks/Ro3Emu.ClientHooks.csproj`: two Unity module references and `Compile Include="..\..\customs\ClientHooks\*.cs"`.
- `src/Ro3Emu.ClientTools/ClientPatcher.cs`: patches `HappyEngine.Resource.dll` and `Render.dll` (`ResourceProbePatch`), plus a read-only `il` command.
- `src/Ro3Emu.ClientTools/Program.cs`: the `il` command.
- `src/Ro3Emu.ClientTools/Ro3Emu.ClientTools.csproj`, `src/Ro3Emu.Setup/Ro3Emu.Setup.csproj`: compile `customs/ClientTools`.

## How to use

1. Build and start the client with `scripts\start.cmd -ClientDir C:\Games\Ragnarok3-Official` (this patches the client's DLLs;
   the patcher keeps a backup of every DLL it changes).
2. Put custom files into `customs\assets\` and run `customs\scripts\Deploy-Customs.ps1`.
3. Log in and test. Remove `<client>\ro3emu\custom` to switch every custom asset off.

See `docs/custom-body-models.md` (Rogue body) and the existing hat notes in `tools/custom-items`.
