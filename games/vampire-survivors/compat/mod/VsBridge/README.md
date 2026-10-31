# VsBridge

BepInEx 6 IL2CPP bridge for Vampire Survivors. It exposes a loopback HTTP API
(`127.0.0.1:8091`) so the pet can read the run and move the player without
touching a window or injecting keyboard input.

## Build

1. Install BepInEx 6 IL2CPP bleeding edge into the game, start it once, and let
   it generate `BepInEx/interop/`.
2. Copy the refs (from the game folder):
   ```powershell
   $refs = "..\refs\vs-refs"
   New-Item -ItemType Directory -Force $refs, "$refs\interop"
   Copy-Item "<game>\BepInEx\core\*.dll" $refs
   Copy-Item "<game>\BepInEx\interop\*.dll" "$refs\interop"
   ```
3. Build and install:
   ```powershell
   dotnet build . -c Release
   Copy-Item .\bin\VsBridge.dll "<game>\BepInEx\plugins\"
   ```

`refs\` and `bin\` are git-ignored; the reference assemblies are ~150 MB and
come from the local install, not from this repository.

## Runtime

- `GET /state` — live run snapshot (see the pack README).
- `POST /act` — `move`, `choose`, `control`, `start`, `stop`, `press`.
- `VS_BRIDGE_PORT` overrides the port (default 8091).

The plugin is main-thread driven (`BridgeBehaviour.Update`): the HTTP server
only queues actions and returns the last snapshot, so Unity APIs are never
called from a background thread. Movement is applied by a Harmony prefix on
`CharacterController.ProcessRawDirection`; menu pages are advanced with the
game's own methods (`ShowCharacterSelect`, `ConfirmCharacter`, `ConfirmStage`,
`Select`, `Quit`, …).

## Credits

Menu/state logic is adapted from the MIT-licensed
[jev_vampire_survivors](https://github.com/oldmoldycake/jev_vampire_survivors)
(BepInEx/Mono, Linux) and the MIT-licensed
[Off the Clock VS mods](https://github.com/OffTheClockMods/vampiresurvivors-mods)
(BepInEx 6 IL2CPP, Windows/Unity 6).
