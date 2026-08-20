# lake-dayz

**English** · [한국어](README.ko.md)

> An MCP server that pre-flights DayZ Enforce mods — compiler-style pass/fail checks that catch boot-crashing mistakes (deprecated/commented-out classes, wrong config classes, bad modded/overrides) before you pack a PBO.
>
> 🎮 Built while making **[dayzlake.online](https://dayzlake.online)** — a DayZ territory-control (점령전) server.

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
| **`find_hook(target, ref_dir)`** | *Where do I hook this?* — answers from **your own working source**: prior art (file/class/method + the hook form used), plus the original set-sites and whether a getter exists (3-line redirect vs rewriting a 50-line `Init()`) |
| **`check_env()`** | Pre-flight the environment **before writing code** — index DB, mod-source mount, and the exact commands to mount P: / extract game data (both run unattended) |
| **`check_addon(module_dir, addons_dir)`** | Whole-module preflight for **silent** failures — requiredAddons is a CfgPatches name (not a pbo filename), `#ifdef` guard really exists (a typo deletes the block), script folder declared in `files[]` (undeclared = never compiled), `$PREFIX$` hygiene |
| **`check_modded(class)`** | Pre-flight a `modded class` — exists? commented-out (deprecated)? which module? already modded by whom? used anywhere? |
| **`enforce_lint(code\|path)`** | Static check — unknown-type (incl. commented), C-style casts, `string+bool`, widget-method existence, name collisions, platform-gated overrides |
| **`check_config(path)`** | `config.cpp` validity — is an item declared under the right `CfgXxx`? (WRONG-CFG = model-less phantom → won't spawn) |
| **`check_pbo(pbo, source_dir, contains, also)`** | Post-pack/deploy proof — stale-vs-source (FileBank silently skips locked pbos and still exits 0), `prefix` trailing-separator trap, string presence, copy-to-copy hash |
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
    -v "<your-addons-dir>:/addons:ro" ^
    lake-dayz
```

> `check_pbo` inspects packed PBOs, so mount the folder your PBOs land in (`/addons`) too — otherwise it can only hand back the command for you to run.
> Running natively (option A) needs no mounts: it sees the host directly and probes DayZ Tools / `P:` itself.

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

Call them in workflow order. Each step stops a **different kind of death**.

| When | Call | Stops |
|---|---|---|
| Before starting | `check_env` | Getting stuck because the toolchain isn't set up |
| Before writing code | `find_hook` | Hooking the wrong place, or rewriting 50 lines when a getter exists |
| Before packing | `check_modded` · `enforce_lint` · `check_config` | Dead boot (compile errors) |
| Before packing | `check_addon` | **Silent failure** — it compiles, and then nothing happens |
| Right after pack/deploy | `check_pbo` | Believing a change shipped when it didn't |

> **Any code with `modded class X` / `class X : Y` / `extends Y` must pass `check_modded(X)` + `enforce_lint(file)` before packing. Item config overrides: `check_config`. Whole module: `check_addon`.**

If the verdict is **ㄴㄴ (no)**, don't pack.

One step stays human: **start the server and read `profiles/script.log`** — that's the only place compile results show up. `check_pbo` hands you that request line too.

---

## Background

This started while building **[dayzlake.online](https://dayzlake.online)**, a DayZ territory-control server. I kept getting bitten by Enforce mistakes that only blow up *after* you launch the game — so I got fed up and built this MCP to catch them before packing. Hoping DayZ keeps growing, the Korean community included. Drop by the server sometime. 🇰🇷

---

## License

- **This project's own code** (`server.py`, indexers, etc.) — **GPLv3**, see [LICENSE](LICENSE).
- **DayZ script data** indexed in `data/dayz_scripts.db` is derived from **DayZ © Bohemia Interactive** and remains subject to the **DayZ Public License – No Derivatives (DPL-ND)**: <https://www.bohemia.net/community/licenses/dayz-public-license-no-derivatives-dpl-nd>
- This is an independent, unofficial modding tool, **not affiliated with or endorsed by Bohemia Interactive**.

## Credits

- Structure reference: [steffenbk/enfusion-mcp-BK](https://github.com/steffenbk/enfusion-mcp-BK) — an Enfusion MCP for Arma Reforger (skeleton only; the data here is DayZ).
