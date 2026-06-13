# -*- coding: utf-8 -*-
"""Index vanilla CONFIG classes (P:\\DZ\\**\\config.cpp) so enforce can validate config.cpp overrides.

Config classes are a SEPARATE namespace from script classes — an item override `class M4A1 : M4A1_Base`
only MERGES (keeps model/p3d) if declared under the SAME top-level config class the item lives in in
vanilla (CfgWeapons for weapons, CfgMagazines for mags, CfgVehicles for everything else). Putting it
in the wrong class = model-less phantom = won't spawn. This table lets check_config() catch that.

Records, for every class directly/indirectly under a top-level CfgXxx: (name, cfg_class, parent, file).
cfg_class is stored in the EXACT case vanilla used + a canonical form is derivable.

Usage: python index_config.py    (writes config_classes table into data/dayz_scripts.db)
"""
import os, re, glob, sqlite3, time

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "data", "dayz_scripts.db")
DZ = r"P:\DZ"

BLOCK = re.compile(r"/\*.*?\*/", re.S)
LINE = re.compile(r"//[^\n]*")
CLASSRE = re.compile(r"class\s+(\w+)\s*(?::\s*(\w+)\s*)?")


def strip(s):
    s = BLOCK.sub("", s)
    return LINE.sub("", s)


def parse(path, rows):
    try:
        s = strip(open(path, encoding="utf-8", errors="replace").read())
    except OSError:
        return
    stack = []   # frames: ['{'] or ['class', name, parent]
    i = 0
    n = len(s)
    rel = os.path.relpath(path, DZ)
    while i < n:
        c = s[i]
        if c == "{":
            stack.append(["{"]); i += 1; continue
        if c == "}":
            if stack: stack.pop()
            i += 1; continue
        m = CLASSRE.match(s, i)
        if m and (i == 0 or not s[i - 1].isalnum()):
            name, parent = m.group(1), m.group(2)
            j = m.end()
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] == "{":
                # cfg class = outermost class frame already on the stack (None if this IS top-level)
                cfg = None
                for fr in stack:
                    if fr[0] == "class":
                        cfg = fr[1]; break
                if cfg:   # this class lives under a top-level CfgXxx -> record it
                    rows.append((name, cfg, parent or "", rel))
                stack.append(["class", name, parent])
                i = j + 1; continue
            elif j < n and s[j] == ";":
                i = j + 1; continue
            else:
                i = m.end(); continue
        i += 1


def main():
    t0 = time.time()
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript("""
      CREATE TABLE IF NOT EXISTS config_classes(name TEXT, cfg_class TEXT, parent TEXT, file TEXT);
      CREATE INDEX IF NOT EXISTS idx_cfg_name ON config_classes(name COLLATE NOCASE);
    """)
    cur.execute("DELETE FROM config_classes")
    files = glob.glob(os.path.join(DZ, "**", "config.cpp"), recursive=True)
    rows = []
    for f in files:
        parse(f, rows)
    cur.executemany("INSERT INTO config_classes VALUES(?,?,?,?)", rows)
    cur.execute("INSERT OR REPLACE INTO meta VALUES('config_indexed_at', ?)", (time.strftime("%Y-%m-%d %H:%M:%S"),))
    con.commit()
    print("config files: %d | classes recorded: %d | %.1fs" % (len(files), len(rows), time.time() - t0))
    # canonical cfg-class spellings (which case dominates)
    print("\ntop-level cfg classes (by item count):")
    for cfg, cnt in cur.execute("SELECT cfg_class, COUNT(*) c FROM config_classes GROUP BY cfg_class ORDER BY c DESC LIMIT 20"):
        print("  %-26s %d" % (cfg, cnt))
    # the proof: where does M4A1 / a magazine actually live?
    print("\nproof — real cfg class of sample items:")
    for it in ("M4A1", "M4A1_Black", "Mag_FNX45_15Rnd", "AKM", "Ssh68Helmet"):
        r = cur.execute("SELECT DISTINCT cfg_class FROM config_classes WHERE name=? COLLATE NOCASE", (it,)).fetchall()
        print("  %-20s -> %s" % (it, [x[0] for x in r]))
    con.close()


if __name__ == "__main__":
    main()
