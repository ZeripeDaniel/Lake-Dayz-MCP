# -*- coding: utf-8 -*-
"""Comment-aware local indexer: P:\\scripts (vanilla) + the @LakeProject modset -> SQLite.

The whole reason this exists: DayZ leaves deprecated classes COMMENTED-OUT in the source
(e.g. ActionFishing /* */ -> replaced by ActionFishingNew). A raw grep sees them as real;
`modded class <commented-out>` = "Unknown type" = the game won't boot. So every symbol here
carries a `commented_out` flag = "defined in the raw file but gone after comment stripping".

Tables (data/dayz_scripts.db, shared with the Doxygen-reference import which adds members/refs):
  symbols(name, kind, parent, is_modded, commented_out, source, module, file, line)
  meta(key, value)

Usage: python index_local.py
"""
import os, re, sqlite3, time, glob

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "data", "dayz_scripts.db")

# source roots: tag -> directory. Vanilla = P:\scripts (user keeps it current with the game).
SOURCES = {
    "vanilla": os.environ.get("DAYZ_MCP_VANILLA", r"P:\scripts"),
    # each subfolder of your mod source root is one mod (tag = mod:<name>).
    # set DAYZ_MCP_MODSET to your mod source root (e.g. @YourMod\source) to index it; empty = vanilla only.
    "_modset_root": os.environ.get("DAYZ_MCP_MODSET", ""),
}

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"//[^\n]*")
# Enforce: `class X : Y`, `class X extends Y`, `modded class X`, `class X` (no base)
CLASS_RE = re.compile(r"^[ \t]*(modded[ \t]+)?class[ \t]+(\w+)[ \t]*(?:(?::|extends)[ \t]*(\w+))?", re.M)
ENUM_RE = re.compile(r"^[ \t]*(modded[ \t]+)?enum[ \t]+(\w+)", re.M)
# raw-pass variants: also match a class header sitting INSIDE a comment, e.g. `/*class ActionToggleFishing`
# or a `* class X` continuation line — so it gets flagged commented_out instead of silently absent.
CLASS_RAW_RE = re.compile(r"^[ \t]*(?:/\*+[ \t]*|\*+[ \t]*|//[ \t]*)?(modded[ \t]+)?class[ \t]+(\w+)[ \t]*(?:(?::|extends)[ \t]*(\w+))?", re.M)
ENUM_RAW_RE = re.compile(r"^[ \t]*(?:/\*+[ \t]*|\*+[ \t]*|//[ \t]*)?(modded[ \t]+)?enum[ \t]+(\w+)", re.M)


def strip_comments(s):
    # keep newlines inside stripped blocks so line numbers stay correct
    s = BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), s)
    s = LINE_COMMENT.sub("", s)
    return s


def module_of(relpath):
    top = relpath.replace("\\", "/").split("/")[0].lower()
    return top if top in ("1_core", "2_gamelib", "3_game", "4_world", "5_mission") else top


# instantiation/registration evidence (counted on comment-STRIPPED text only):
#   new X(...)        -> engine/code creates it directly (modify = modded only; extends won't be seen)
#   X.Cast(...)       -> handled/queried in code paths
#   AddAction(X)      -> action registered on an item's SetActions (vanilla idiom, fishingrod_base.c:11)
#   "X" string        -> config/CreateObjectEx-style creation hints
NEW_RE = re.compile(r"\bnew\s+(\w+)")
CAST_RE = re.compile(r"\b(\w+)\.Cast\s*\(")
ADDACTION_RE = re.compile(r"\bAddAction\s*\(\s*(\w+)")
STRLIT_RE = re.compile(r'"(\w{3,})"')


def count_usage(stripped, source, counters):
    for rx, kind in ((NEW_RE, "new"), (CAST_RE, "cast"), (ADDACTION_RE, "addaction"), (STRLIT_RE, "strlit")):
        for name in rx.findall(stripped):
            key = (name, source, kind)
            counters[key] = counters.get(key, 0) + 1


# Method declarations per class (so enforce_lint can verify x.Method() exists on x's type —
# the gap that let TextWidget.GetText() through). Works on proto (enwidgets.c) AND script classes.
_MODS = r"(?:proto|native|static|protected|private|override|sealed|ref|const|autoptr|owned|external|local|out|inout|volatile|event|notnull)"
METHOD_DECL = re.compile(r"^\s*(?:" + _MODS + r"\s+)*[\w\[\]<>,]+(?:\s*<[^>;]*>)?\s+(\w+)\s*\(")
CLASS_HDR = re.compile(r"^\s*(?:modded\s+)?(?:sealed\s+)?class\s+(\w+)")
# preprocessor directives survive comment-stripping (they're not comments). We track them so each
# method carries the #ifdef/#ifndef condition it lives under — e.g. GetConsoleToolbarText is defined
# only under `#ifdef PLATFORM_CONSOLE`, so on a PC build it doesn't exist and overriding it crashes.
PP_RE = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b\s*(!?\w+)?")


def extract_methods(stripped):
    """[(class_name, method_name, guard)] — a method decl is a `<ret> Name(` line sitting exactly at a
    class body's brace depth (so calls/locals inside method bodies, which are deeper, don't match).
    `guard` = the active preprocessor condition stack ("PLATFORM_CONSOLE", "!PLATFORM_WINDOWS",
    "A & B", or "" when unguarded), so the linter can tell a method is absent on our build target."""
    out = []
    stack = []          # (class_name, body_depth)
    depth = 0
    pending = None      # class name awaiting its '{'
    pp = []             # preprocessor condition stack, e.g. ["PLATFORM_CONSOLE"]
    for line in stripped.split("\n"):
        pm = PP_RE.match(line)
        if pm:
            d = pm.group(1); tok = pm.group(2) or ""
            if d == "ifdef":
                pp.append(tok)
            elif d == "ifndef":
                pp.append("!" + tok)
            elif d == "if":
                pp.append("?")            # opaque expression — never judged by the linter
            elif d == "elif":
                if pp: pp[-1] = "?"
            elif d == "else":
                if pp:
                    t = pp[-1]
                    pp[-1] = t[1:] if t.startswith("!") else "!" + t
            elif d == "endif":
                if pp: pp.pop()
            continue
        hm = CLASS_HDR.match(line)
        if hm:
            pending = hm.group(1)
        if stack and depth == stack[-1][1]:
            mm = METHOD_DECL.match(line)
            if mm and mm.group(1) not in ("if", "for", "while", "switch", "return", "foreach"):
                out.append((stack[-1][0], mm.group(1), " & ".join(pp)))
        for ch in line:
            if ch == "{":
                depth += 1
                if pending is not None:
                    stack.append((pending, depth))
                    pending = None
            elif ch == "}":
                if stack and depth == stack[-1][1]:
                    stack.pop()
                depth -= 1
                if depth < 0:
                    depth = 0
    return out


def scan_file(path, rel, source, rows, counters, method_rows):
    try:
        raw = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return
    stripped = strip_comments(raw)
    count_usage(stripped, source, counters)
    for cn, mn, guard in extract_methods(stripped):
        method_rows.append((cn, mn, guard, source))

    def collect(text, class_rx, enum_rx):
        out = {}
        for rx, kind in ((class_rx, "class"), (enum_rx, "enum")):
            for m in rx.finditer(text):
                modded = bool(m.group(1))
                name = m.group(2)
                parent = m.group(3) if kind == "class" else None
                line = text.count("\n", 0, m.start()) + 1
                out[(name, modded)] = (kind, parent, line)
        return out

    live = collect(stripped, CLASS_RE, ENUM_RE)
    raw_syms = collect(raw, CLASS_RAW_RE, ENUM_RAW_RE)
    mod = module_of(rel)
    for (name, modded), (kind, parent, line) in raw_syms.items():
        commented = 0 if (name, modded) in live else 1
        if not commented:
            kind, parent, line = live[(name, modded)]
        rows.append((name, kind, parent, int(modded), commented, source, mod, rel, line))


def main():
    t0 = time.time()
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript("""
      CREATE TABLE IF NOT EXISTS symbols(
        name TEXT, kind TEXT, parent TEXT, is_modded INTEGER, commented_out INTEGER,
        source TEXT, module TEXT, file TEXT, line INTEGER);
      CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name COLLATE NOCASE);
      CREATE TABLE IF NOT EXISTS usage_counts(
        name TEXT, source TEXT, kind TEXT, n INTEGER);
      CREATE INDEX IF NOT EXISTS idx_usage_name ON usage_counts(name COLLATE NOCASE);
      DROP TABLE IF EXISTS methods;
      CREATE TABLE methods(class_name TEXT, method TEXT, guard TEXT, source TEXT);
      CREATE INDEX IF NOT EXISTS idx_methods_class ON methods(class_name COLLATE NOCASE);
      CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    cur.execute("DELETE FROM symbols WHERE source='vanilla' OR source LIKE 'mod:%'")
    cur.execute("DELETE FROM usage_counts")
    cur.execute("DELETE FROM methods")

    rows = []
    counters = {}
    method_rows = []
    van = SOURCES["vanilla"]
    nv = 0
    for f in glob.glob(os.path.join(van, "**", "*.c"), recursive=True):
        scan_file(f, os.path.relpath(f, van), "vanilla", rows, counters, method_rows)
        nv += 1

    modroot = SOURCES["_modset_root"]
    nm = 0
    modnames = sorted(os.listdir(modroot)) if (modroot and os.path.isdir(modroot)) else []
    for modname in modnames:
        mdir = os.path.join(modroot, modname)
        if not os.path.isdir(mdir):
            continue
        for f in glob.glob(os.path.join(mdir, "**", "*.c"), recursive=True):
            scan_file(f, os.path.relpath(f, modroot), "mod:" + modname, rows, counters, method_rows)
            nm += 1

    cur.executemany("INSERT INTO symbols VALUES(?,?,?,?,?,?,?,?,?)", rows)

    # keep usage rows only for names that are known symbols (prunes string-literal noise)
    known = {r[0] for r in rows}
    usage_rows = [(name, src, kind, n) for (name, src, kind), n in counters.items() if name in known]
    cur.executemany("INSERT INTO usage_counts VALUES(?,?,?,?)", usage_rows)
    cur.executemany("INSERT INTO methods VALUES(?,?,?,?)", sorted(set(method_rows)))
    cur.execute("INSERT OR REPLACE INTO meta VALUES('local_indexed_at', ?)", (time.strftime("%Y-%m-%d %H:%M:%S"),))
    cur.execute("INSERT OR REPLACE INTO meta VALUES('local_files', ?)", (str(nv + nm),))
    con.commit()

    # report
    for src_like, label in (("vanilla", "vanilla"), ("mod:%", "modset")):
        q = cur.execute("SELECT COUNT(*), SUM(commented_out), SUM(is_modded) FROM symbols WHERE source LIKE ?", (src_like,)).fetchone()
        print("%-8s symbols=%s commented_out=%s modded-defs=%s" % (label, q[0], q[1], q[2]))
    print("files scanned: vanilla=%d modset=%d | %.1fs" % (nv, nm, time.time() - t0))
    # the proof cases
    for n in ("ActionFishing", "ActionFishingNew", "ActionToggleFishing"):
        r = cur.execute("SELECT name, commented_out, file, line FROM symbols WHERE name=? AND source='vanilla' AND is_modded=0", (n,)).fetchall()
        print("  check %-22s -> %s" % (n, r))
    con.close()


if __name__ == "__main__":
    main()
