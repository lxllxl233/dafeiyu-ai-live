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
  `games/registry.json`.

## Commands

```powershell
python main.py                  # desktop pet
python main.py studio           # overlay + management console (:8730)
python main.py game state       # current game state
python main.py game launch      # launch current game (Steam / exe)
python main.py agent run --demo # offline LangGraph turn
python main.py agent run        # real ctx + LLM, dry-run
python main.py agent run --execute
python main.py agent trace
python main.py agent graph
python -m pytest -q tests
```

## Invariants

- **Never print, log, commit, or copy the API key.** `config.json` is git-ignored;
  the HTTP API and console only expose `llm_api_key_set`.
- Only `main.py` lives at the repo root; Python code belongs under `src/dafeiyu/`.
- The desktop right-click menu is a Win32 popup. Menu IDs are explicit; do not
  renumber existing entries when adding one.
- Game adapters are read-only. Actions go through `tools/act.py`; game launch goes
  through `src/dafeiyu/games/launch.py`.
- Keep the UI palette (`--bg:#0b0d12`, `--gold:#f4c968`, dark panels).
- Agent traces are local JSONL (`logs/agent_trace.jsonl`); do not send them to a
  cloud service or include full game state / secrets.

## When changing agent behavior

1. Keep `tools/ctx.py` and `tools/act.py` as the tool boundary.
2. Add observation fields rather than a second game-state reader.
3. Record node-level timing/status/tokens in `TraceRecorder`.
4. Update `docs/AGENT_LANGGRAPH.md` and the console Agent view when the graph changes.
5. Run the agent tests and the full suite before committing.
