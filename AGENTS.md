# AGENTS.md

This repository is a Python live-streaming pet that is **driven by a DeepSeek Harness
agent**. The kernel does not run its own hidden LLM loop; the agent decides what to
say and which game action to take. The optional `agent run` LLM planner is explicit
and dry-run by default; keep that boundary unless the task asks for a new planner.

## Start here

- Read `README.md` for the product surface, `ARCHITECTURE.md` for the layers.
- Decision skills live in `.dsh/skills/` (`sts2-ironclad-ironclad`,
  `sts2-caster`, `sts2-defect-defect`).
- LangGraph turn orchestration: `docs/AGENT_LANGGRAPH.md`.
- The game packets are under `games/<id>/`; the registry is
  `games/registry.json`. `games/toy-duel/` is the second, fully offline plugin
  used to prove the core is not STS2-specific; keep it working when changing
  the plugin/host/tools contracts.

## Commands

```powershell
python main.py                  # desktop pet
python main.py studio           # overlay + management console (:8730)
python main.py game state       # current game state
python main.py game launch      # only launch current game (Steam / exe)
python main.py play             # launch + autonomous run; Ctrl+C or --stop
python main.py play --mode llm  # LLM-planned combat turns (needs API key)
python main.py play --pace voice # default: wait for speech; normal = old fast
python main.py play --game toy-duel --mode local  # offline second-game acceptance
python main.py play --stop      # cross-process stop
python main.py agent run --demo # offline LangGraph turn
python main.py agent run        # real ctx + LLM, dry-run
python main.py agent run --execute
python main.py agent trace
python main.py agent graph
python tools/release_check.py --metadata-only  # version sync only
python tools/release_check.py                  # release gate (tests/docs/compile)
python -m pytest -q tests
```

Version bumps must stay in sync across `pyproject.toml`, `src/dafeiyu/__init__.py`,
`README.md`, `games/slay-the-spire-2/game.json`, and `CHANGELOG.md`. The `.dsh`
skill `v22` is a separate skill version, not the product version.

## Invariants

- **Never print, log, commit, or copy the API key.** `config.json` is git-ignored;
  the HTTP API and console only expose `llm_api_key_set`.
- Only `main.py` lives at the repo root; core Python code belongs under
  `src/dafeiyu/`, game-specific code belongs under `games/<id>/`. Core must not
  hardcode a game id, display name, or alias; use `games/catalog.py` and
  `game.json` metadata.
- The desktop right-click menu is a Win32 popup. Menu IDs are explicit; do not
  renumber existing entries when adding one.
- Combat actions use the `games/slay-the-spire-2/decision/act.py` batch protocol
  through `sts2.cmd_batch`; screen transitions use
  `games/slay-the-spire-2/decision/sts2.py` (compat client). Game launch goes
  through `src/dafeiyu/games/launch.py`. The autoplay facade is
  `src/dafeiyu/agent/play.py`, the generic status/stop/recording/timing host is
  `src/dafeiyu/agent/host.py`; the actual loop and tools live in the game package
  (STS2: `games/slay-the-spire-2/autoplay/runner.py`,
  `games/slay-the-spire-2/decision/`) and control the game only through the same
  tool boundary; unknown screens must stop rather than guess. `tools/ctx.py`,
  `tools/act.py`, `tools/advisor.py`, `tools/sim.py` etc. are compatibility shims.
- Keep the UI palette (`--bg:#0b0d12`, `--gold:#f4c968`, dark panels).
- Agent traces are local JSONL (`logs/agent_trace.jsonl`); do not send them to a
  cloud service or include full game state / secrets.

## When changing agent behavior

1. Keep `games/slay-the-spire-2/decision/ctx.py` and `decision/act.py` as the
   tool boundary; `tools/ctx.py` / `tools/act.py` only forward for old commands.
2. Add observation fields rather than a second game-state reader.
3. Record node-level timing/status/tokens in `TraceRecorder`.
4. Update `docs/AGENT_LANGGRAPH.md` and the console Agent view when the graph changes.
5. Run the agent tests and the full suite before committing.
