# lake-dayz

**English** · [한국어](README.ko.md)

> An MCP server that pre-flights DayZ Enforce mods — compiler-style pass/fail checks that catch boot-crashing mistakes (deprecated/commented-out classes, wrong config classes, bad modded/overrides) before you pack a PBO.

---

## Why

DayZ Enforce has **no offline compile check** — a broken mod only reveals its error when you **launch the game**.

A real incident:

```enforce
modded class ActionFishing { override string GetText() { ... } }
```

→ on boot: **`Can't compile World script module! Unknown type 'ActionFishing'`** → the whole game UI dies.

The cause was **not** "ActionFishing was removed." DayZ leaves **deprecated code commented out instead of deleting it** — lines 1–52 of `actionfishing.c` are one `/* ... */` block, and the live class is `ActionFishingNew`. A raw `grep "class ActionFishing"` catches the dead code *inside the comment* → *"oh, the code exists!"* → modding it = dead boot.

> **Lesson: "defined ≠ alive."** Is it commented out? Defined but unused? You have to check the **usage**, not just the name.

`check_modded("ActionFishing")` → **ㄴㄴ (no)** in one line. That's why this exists.

---

## What it does

Every tool is **verdict/evidence first** — the first line is the conclusion (`ㅇㅇ` = pass / `ㄴㄴ` = no).

| Tool | Purpose |
|---|---|
| **`check_modded(class)`** | Pre-flight a `modded class` — exists? commented-out (deprecated)? which module? already modded by whom? used anywhere? |
| **`enforce_lint(code\|path)`** | Static check — unknown-type (incl. commented), C-style casts, `string+bool`, widget-method existence, name collisions, platform-gated overrides |
| **`check_config(path)`** | `config.cpp` validity — is an item declared under the right `CfgXxx`? (WRONG-CFG = model-less phantom → won't spawn) |
| `symbol_lookup(name)` | Symbol card: per-source definition / parent / module / `file:line` / commented-out / modded status |
| `class_info(name)` | Parent chain (local source = authority) + children + member signatures |
| `find_usages(symbol)` | Where it's used: vanilla reference Referenced-by/References + live grep over your mod source |
| `search_symbols(pattern)` | LIKE pattern search (`ActionFish%`, `%Teleport%`) |
| `enforce_doc(topic)` | Enforce syntax / modding design patterns (curated guide section search) |

---

## How it works

```text
index_local.py  ─ P:\scripts + (optional) your mod source, comment-aware parse (commented_out flag) ─┐
index_config.py ─ P:\DZ\**\config.cpp → item→CfgXxx mapping                                          ├→ data/dayz_scripts.db
DayZ-script Doxygen reference ─ members + signatures + References/Referenced-by                       ─┘  (pre-built into the DB)
server.py       ─ serves the tools from that DB (stdio MCP)
```

- **`commented_out` flag** — whether code is commented-out dead code, plus `file:line`. `P:\` is the authority source, kept in sync with the game.
- **members / cross-references** — extracted from a DayZ-script Doxygen (comment code excluded), **pre-built and shipped in the DB**, so it works out of the box.
- **Inheritance** authority = local source.
- Current DB: 7,378 symbols / 31,877 methods / 32,259 members / 75,548 cross-refs / 90,870 config classes.

---

## Install

The DB (`data/dayz_scripts.db`) ships with the repo, so it works right after cloning. You can query/validate **without DayZ or DayZ Tools installed**; you only need DayZ Tools' extracted `P:\` to **rebuild** the DB for a new game version.

### A. Native (simplest)

```cmd
cd <repo>
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

claude mcp add -s user lake-dayz ^
  <repo>\.venv\Scripts\python.exe ^
  <repo>\server.py
```

### B. Docker (server only)

```cmd
docker build -t lake-dayz .

claude mcp add -s user lake-dayz -- ^
  docker run -i --rm ^
    -v "<repo>\data:/data:ro" ^
    -v "<your-mod-source-root>:/modset:ro" ^
    -v "<dir-with-enforce-script-guide.md>:/docs:ro" ^
    lake-dayz
```

Mounts are read-only, so rebuilding the DB on the host is picked up automatically.
Env vars: `DAYZ_MCP_DB` / `DAYZ_MCP_MODSET` / `DAYZ_MCP_GUIDE`.

> `enforce_doc` serves `enforce-script-guide.md` — put it at the repo root (or `DAYZ_MCP_GUIDE`) for native, or the `/docs` mount for Docker.

---

## Updating data (after a game update)

```cmd
.venv\Scripts\python.exe index_local.py     REM re-index P:\scripts (+ modset if DAYZ_MCP_MODSET is set)
.venv\Scripts\python.exe index_config.py    REM re-index P:\DZ config
```

1. Game updates → re-extract `P:\` with DayZ Tools (`P:\` is the authority source).
2. `index_local.py` — almost always just this (the core data for modded-class verdicts).
3. To cover your own mod, set `DAYZ_MCP_MODSET` to your mod source root (e.g. `@YourMod\source`); each subfolder = one mod.

---

## Rule of use

> **Any code with `modded class X` / `class X : Y` / `extends Y` must pass `check_modded(X)` + `enforce_lint(file)` before packing. Item config overrides: `check_config`.**

If the verdict is **ㄴㄴ (no)**, don't pack. This one step stops "the code exists!? → dead boot."

---

## License

- **This project's own code** (`server.py`, indexers, etc.) — **GPLv3**, see [LICENSE](LICENSE).
- **DayZ script data** indexed in `data/dayz_scripts.db` is derived from **DayZ © Bohemia Interactive** and remains subject to the **DayZ Public License – No Derivatives (DPL-ND)**: <https://www.bohemia.net/community/licenses/dayz-public-license-no-derivatives-dpl-nd>
- This is an independent, unofficial modding tool, **not affiliated with or endorsed by Bohemia Interactive**.

## Credits

- Structure reference: [steffenbk/enfusion-mcp-BK](https://github.com/steffenbk/enfusion-mcp-BK) — an Enfusion MCP for Arma Reforger (skeleton only; the data here is DayZ).
