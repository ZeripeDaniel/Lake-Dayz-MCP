# -*- coding: utf-8 -*-
"""DayZ Enforce verification MCP server.

Answers, compiler-style ("통과? ㅇㅇ / ㄴㄴ"), the questions that prevent boot-killing mistakes:
  - does this symbol REALLY exist (or is it deprecated code left commented-out)?
  - where is it, which script module, who inherits/uses it, who already mods it?
  - does this Enforce snippet violate known gotchas / reference unknown types?

Data: data/dayz_scripts.db built by index_local.py (P:\\scripts + the mod source root,
comment-aware) plus a DayZ-script Doxygen reference (members, signatures,
References/Referenced-by). Re-run index_local.py after a game update.

Run (stdio): python server.py   |   Docker: see Dockerfile / README-ko.md
"""
import os, re, glob, sqlite3, json, collections

from mcp.server.fastmcp import FastMCP

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("DAYZ_MCP_DB", os.path.join(ROOT, "data", "dayz_scripts.db"))
MODSET = os.environ.get("DAYZ_MCP_MODSET", "")  # your mod source root (e.g. @YourMod\\source); enables live modset grep when set
GUIDE = os.environ.get("DAYZ_MCP_GUIDE", os.path.join(ROOT, "enforce-script-guide.md"))  # served by enforce_doc

mcp = FastMCP("lake-dayz")


def q(sql, args=()):
    con = sqlite3.connect(DB)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _lake_notes():
    """Curated Lake gotchas — engine classes that CANNOT be modded (compile 'Engine class X cannot be
    modded') even though they 'exist' in script source, plus where/how to hook instead. Read per-call so
    data/lake_notes.json edits go live without restarting the server. Grow it as new traps are found."""
    try:
        with open(os.path.join(ROOT, "data", "lake_notes.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _engine_note(class_name):
    for k, v in _lake_notes().get("engine_classes", {}).items():
        if k.lower() == class_name.lower():
            return v
    return None


def _sym_rows(name):
    return q("SELECT name,kind,parent,is_modded,commented_out,source,module,file,line "
             "FROM symbols WHERE name=? COLLATE NOCASE ORDER BY source, is_modded", (name,))


def _children(name):
    return [r[0] for r in q("SELECT DISTINCT name FROM symbols WHERE parent=? COLLATE NOCASE "
                            "AND commented_out=0 AND is_modded=0", (name,))]


def _usage(name):
    """{kind: {'vanilla': n, 'mods': n}} from usage_counts (instantiation/registration evidence)."""
    out = {}
    for src, kind, n in q("SELECT source, kind, n FROM usage_counts WHERE name=? COLLATE NOCASE", (name,)):
        d = out.setdefault(kind, {"vanilla": 0, "mods": 0})
        d["vanilla" if src == "vanilla" else "mods"] += n
    return out


def _base_verdict(name, children):
    """Base/template-class heuristic: name suffix OR many children -> blast-radius warning."""
    is_base = name.lower().endswith(("base", "_base")) or len(children) >= 5
    if not is_base:
        return None
    return ("⚠ Base/템플릿 클래스 (자식 %d개) — modded 하면 자식 전체에 일괄 적용(blast radius). "
            "전체 일괄이 목적이면 modded가 정답, 신규 1개 추가가 목적이면 extends+등록 (가이드 §10.4/§10.7)."
            % len(children))


def _usage_lines(name):
    """Human lines describing HOW this class gets created/registered — drives modded-vs-extends advice."""
    u = _usage(name)
    if not u:
        return ["생성/등록 흔적: 스크립트(.c)에서 없음 — config/CE(types.xml)로만 스폰되거나 미사용일 수 있음 (find_usages로 확인)"]
    parts = []
    for kind, label in (("new", "new(직접 생성)"), ("cast", ".Cast(처리)"), ("addaction", "AddAction(액션 등록)"), ("strlit", '"문자열"(config/CreateObject 힌트)')):
        if kind in u:
            d = u[kind]
            seg = []
            if d["vanilla"]: seg.append("vanilla %d" % d["vanilla"])
            if d["mods"]: seg.append("모드 %d" % d["mods"])
            parts.append("%s: %s" % (label, ", ".join(seg)))
    lines = ["생성/등록 흔적: " + " | ".join(parts)]
    if u.get("new", {}).get("vanilla"):
        lines.append("  → vanilla가 직접 new로 생성 — 기존 흐름에 끼어들려면 extends는 무효, **modded만 유효** (가이드 §10.3)")
    if u.get("addaction", {}).get("vanilla"):
        lines.append("  → 아이템 SetActions의 AddAction으로 등록되는 액션 — 동작 수정=modded+override / 새 액션 추가=extends+AddAction·ActionConstructor 등록 (가이드 §10.4)")
    return lines


@mcp.tool()
def check_modded(class_name: str) -> str:
    """`modded class X`를 써도 되는지 사전 판정 (부팅 사망 방지). 첫 줄이 판정: ㅇㅇ(통과)/ㄴㄴ(불가).
    검사: 실존 여부, 주석처리(deprecated) 여부, 모듈 위치, 이미 modded한 모드, 사용 흔적,
    **엔진 클래스 여부('실존'해도 modded 불가)**."""
    en = _engine_note(class_name)
    if en:
        return ("판정: ㄴㄴ 불가 — `%s`는 **엔진 클래스**라 `modded` 시 컴파일 거부 "
                "('Engine class cannot be modded'). 스크립트 바인딩(.c)이 있어 '실존'하지만 모딩은 불가 — "
                "여기서 '실존' ≠ '모딩가능'. 모딩은 확실한 스크립트 자식/UI 클래스에만.\n→ %s" % (class_name, en))
    rows = _sym_rows(class_name)
    base = [r for r in rows if not r[3]]            # plain definitions
    mods = [r for r in rows if r[3]]                # modded-definitions
    van = [r for r in base if r[5] == "vanilla"]
    live_van = [r for r in van if not r[4]]
    commented = [r for r in van if r[4]]
    in_mods = [r for r in base if r[5].startswith("mod:")]

    out = []
    if live_van:
        r = live_van[0]
        out.append("판정: ㅇㅇ 통과 — vanilla `%s` 실존 (module=%s, %s:%s)" % (r[0], r[6], r[7], r[8]))
        if r[2]:
            out.append("부모: %s" % r[2])
        if mods:
            out.append("이미 modded한 곳: " + ", ".join(sorted({m[5] for m in mods})) + " (체인 공존 OK, super 호출 유지)")
        kids = _children(class_name)
        bv = _base_verdict(class_name, kids)
        if bv:
            out.append(bv)
        nrefs = q("SELECT COUNT(*) FROM refs WHERE dst_class=? COLLATE NOCASE", (class_name,))[0][0]
        nmem = q("SELECT COUNT(*) FROM members WHERE class_name=? COLLATE NOCASE", (class_name,))[0][0]
        out.append("이해관계: 레퍼런스 멤버 %d개, 피참조 %d건%s" % (
            nmem, nrefs, " — 피참조 0건이면 '정의만 있고 안 쓰는' 코드일 수 있으니 쓰는곳 확인 권장" if nrefs == 0 else ""))
        out.extend(_usage_lines(class_name))
    elif commented:
        r = commented[0]
        out.append("판정: ㄴㄴ 불가 — `%s`는 vanilla 소스에 **주석처리(/* */)로만 존재** (deprecated). modded 하면 Unknown type 부팅 사망." % class_name)
        out.append("위치: %s:%s" % (r[7], r[8]))
        sib = q("SELECT DISTINCT name FROM symbols WHERE file=? AND source='vanilla' AND commented_out=0 AND kind='class' AND is_modded=0", (r[7],))
        if sib:
            out.append("같은 파일의 살아있는 클래스(대체 후보): " + ", ".join(s[0] for s in sib[:8]))
        ydz = q("SELECT 1 FROM ref_classes WHERE name=? COLLATE NOCASE", (class_name,))
        out.append("레퍼런스 등재: %s (Doxygen은 주석 코드를 배제 — 미등재=죽은 코드 방증)" % ("있음(확인 필요)" if ydz else "없음"))
    elif in_mods:
        srcs = sorted({r[5] for r in in_mods})
        r = in_mods[0]
        out.append("판정: ㅇㅇ 조건부 — vanilla엔 없고 모드 정의: %s (module=%s, %s:%s)" % (", ".join(srcs), r[6], r[7], r[8]))
        out.append("주의: 해당 모드를 requiredAddons에 넣어야 컴파일됨.")
    else:
        out.append("판정: ㄴㄴ 불가 — `%s` 정의를 어디서도 못 찾음 (vanilla+modset 인덱스 기준). 오타이거나 이 게임 버전에 없음." % class_name)
        like = q("SELECT DISTINCT name FROM symbols WHERE name LIKE ? COLLATE NOCASE AND commented_out=0 LIMIT 8", ("%" + class_name + "%",))
        if like:
            out.append("비슷한 이름: " + ", ".join(l[0] for l in like))
    return "\n".join(out)


@mcp.tool()
def symbol_lookup(name: str) -> str:
    """심볼(클래스/enum) 존재·정체 조회: 모든 소스에서 종류/부모/모듈/파일:라인/주석여부/modded현황."""
    rows = _sym_rows(name)
    if not rows:
        like = q("SELECT DISTINCT name, source FROM symbols WHERE name LIKE ? COLLATE NOCASE LIMIT 10", ("%" + name + "%",))
        return "없음: `%s` 미정의.%s" % (name, ("\n비슷한 이름: " + ", ".join("%s(%s)" % (l[0], l[1]) for l in like)) if like else "")
    out = ["`%s` — 정의 %d건:" % (name, len(rows))]
    for r in rows:
        flags = []
        if r[3]: flags.append("modded-def")
        if r[4]: flags.append("**주석처리(죽은 코드)**")
        out.append("- [%s] %s%s parent=%s module=%s %s:%s %s" % (
            r[5], r[1], (" " + " ".join(flags)) if flags else "", r[2] or "-", r[6], r[7], r[8], ""))
    return "\n".join(out)


@mcp.tool()
def class_info(name: str) -> str:
    """클래스 상세: 부모 체인(로컬 소스 권위), 자식들, 멤버+시그니처(레퍼런스)."""
    rows = [r for r in _sym_rows(name) if not r[3] and not r[4]]
    if not rows:
        return "없음(또는 주석처리): `%s` — symbol_lookup으로 상태 확인." % name
    r = rows[0]
    out = ["`%s` (%s, module=%s, %s:%s)" % (r[0], r[5], r[6], r[7], r[8])]
    # parent chain
    chain, cur_ = [], r[2]
    seen = {name.lower()}
    while cur_ and cur_.lower() not in seen and len(chain) < 15:
        chain.append(cur_)
        seen.add(cur_.lower())
        nxt = q("SELECT parent FROM symbols WHERE name=? COLLATE NOCASE AND is_modded=0 AND commented_out=0 AND parent IS NOT NULL LIMIT 1", (cur_,))
        cur_ = nxt[0][0] if nxt else None
    out.append("부모 체인: " + (" -> ".join([name] + chain) if chain else "(없음/루트)"))
    kids = _children(name)
    if kids:
        out.append("자식(%d): %s" % (len(kids), ", ".join(kids[:25]) + (" ..." if len(kids) > 25 else "")))
    bv = _base_verdict(name, kids)
    if bv:
        out.append(bv)
    out.extend(_usage_lines(name))
    mems = q("SELECT name, signature FROM members WHERE class_name=? COLLATE NOCASE", (name,))
    out.append("멤버 %d개 (레퍼런스):" % len(mems))
    for mn, sig in mems[:40]:
        out.append("  - %s" % sig)
    if len(mems) > 40:
        out.append("  ... (+%d)" % (len(mems) - 40))
    return "\n".join(out)


@mcp.tool()
def find_usages(symbol: str) -> str:
    """쓰는곳 검색: vanilla(레퍼런스 Referenced-by/References) + 모드셋 소스 실시간 grep.
    '정의돼 있다 ≠ 쓰인다' 판별용 — 이해관계 확인."""
    out = _usage_lines(symbol)
    rby = q("SELECT DISTINCT src_class, src_member FROM refs WHERE dst_class=? COLLATE NOCASE", (symbol,))
    out.append("vanilla에서 `%s`를 참조(레퍼런스): %d건" % (symbol, len(rby)))
    for c, m_ in rby[:25]:
        out.append("  - %s::%s" % (c, m_))
    if len(rby) > 25:
        out.append("  ... (+%d)" % (len(rby) - 25))
    uses = q("SELECT DISTINCT dst_class, dst_member FROM refs WHERE src_class=? COLLATE NOCASE LIMIT 20", (symbol,))
    if uses:
        out.append("`%s`가 참조하는 것(상위 20): %s" % (symbol, ", ".join(sorted({u[0] for u in uses if u[0]}))))
    # live grep over modset sources
    hits = []
    rx = re.compile(r"\b%s\b" % re.escape(symbol))
    for f in glob.glob(os.path.join(MODSET, "**", "*.c"), recursive=True):
        try:
            txt = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        n = len(rx.findall(txt))
        if n:
            hits.append((os.path.relpath(f, MODSET), n))
    out.append("모드셋(@LakeProject) 등장: %d파일" % len(hits))
    for f, n in sorted(hits, key=lambda x: -x[1])[:15]:
        out.append("  - %s (%d)" % (f, n))
    return "\n".join(out)


@mcp.tool()
def search_symbols(pattern: str) -> str:
    """이름 패턴으로 심볼 검색 (SQL LIKE, %·_ 와일드카드. 예: 'ActionFish%', '%Teleport%')."""
    rows = q("SELECT DISTINCT name, kind, source, module, commented_out FROM symbols "
             "WHERE name LIKE ? COLLATE NOCASE ORDER BY commented_out, source LIMIT 60", (pattern,))
    if not rows:
        return "0건: %s" % pattern
    out = ["%d건 (60 한도):" % len(rows)]
    for n, k, s, mo, c in rows:
        out.append("- %s [%s/%s/%s]%s" % (n, k, s, mo, " **주석처리**" if c else ""))
    return "\n".join(out)


# ---- enforce_lint -----------------------------------------------------------
CAST_RE = re.compile(r"\(\s*(?:int|float|bool|string)\s*\)\s*[\w(]")
STR_PLUS_BOOL = re.compile(r'"[^"]*"\s*\+\s*(?:true|false)\b|\b(?:true|false)\s*\+\s*"')
MODDED_DECL = re.compile(r"^[ \t]*modded[ \t]+class[ \t]+(\w+)", re.M)
BASE_DECL = re.compile(r"^[ \t]*(?:modded[ \t]+)?class[ \t]+\w+[ \t]*(?::|extends)[ \t]*(\w+)", re.M)


def _methods_of_chain(typename):
    """All method names (lowercased) on typename + its parent chain. Empty set = no data indexed."""
    methods = set()
    seen = set()
    cur = typename
    while cur and cur.lower() not in seen:
        seen.add(cur.lower())
        for r in q("SELECT method FROM methods WHERE class_name=? COLLATE NOCASE", (cur,)):
            methods.add(r[0].lower())
        p = q("SELECT parent FROM symbols WHERE name=? COLLATE NOCASE AND is_modded=0 AND commented_out=0 AND parent IS NOT NULL LIMIT 1", (cur,))
        cur = p[0][0] if p else None
    return methods


# --- platform / diag preprocessor guards ---------------------------------------------------------
# Our build target is PC + RELEASE. A vanilla method defined ONLY under an INACTIVE #ifdef (e.g.
# PLATFORM_CONSOLE — the gamepad-only inventory toolbar) does NOT exist in the compiled PC scripts,
# so `modded ... override`-ing it gives "no function with this name in the base class" -> the whole
# script module fails to compile -> client hangs at the loading screen. This is exactly the
# GetConsoleToolbarText trap. `protected` is overridable — the real killer is the platform #ifdef.
_BUILD_ACTIVE = {"PLATFORM_WINDOWS", "PLATFORM_PC", "RELEASE", "GAME_DAYZ", "DAYZ"}
_BUILD_INACTIVE = {
    "PLATFORM_CONSOLE", "PLATFORM_XBOX", "PLATFORM_XBOXONE", "PLATFORM_XB1", "PLATFORM_PS4",
    "PLATFORM_PS5", "PLATFORM_PSVITA", "PLATFORM_SWITCH", "PLATFORM_MAC", "PLATFORM_LINUX",
    "DIAG_DEVELOPER", "DEVELOPER", "_DEVELOPER", "GAME_TRANSLATION_DEBUG",
}


def _guard_pc_absent(guard):
    """If `guard` ("PLATFORM_CONSOLE" / "!PLATFORM_WINDOWS" / "A & B") makes the symbol absent on our
    PC+RELEASE build, return the offending token; else None. Only judges tokens whose build state we
    KNOW (unknown defines -> None, so we never false-flag)."""
    if not guard:
        return None
    for cond in guard.split(" & "):
        neg = cond.startswith("!")
        tok = cond[1:] if neg else cond
        if not neg and tok in _BUILD_INACTIVE:
            return tok                       # #ifdef <inactive> -> compiled out on PC
        if neg and tok in _BUILD_ACTIVE:
            return "!" + tok                 # #ifndef <active>  -> compiled out on PC
    return None


def _vanilla_method_guards(typename, method):
    """Guards under which vanilla defines `method` on `typename` + its parent chain. Empty list =
    method not found in vanilla (don't judge — that's the widget-method-existence check's job)."""
    guards = []
    seen = set()
    cur = typename
    while cur and cur.lower() not in seen:
        seen.add(cur.lower())
        for r in q("SELECT guard FROM methods WHERE class_name=? COLLATE NOCASE AND method=? "
                   "COLLATE NOCASE AND source='vanilla'", (cur, method)):
            guards.append(r[0] or "")
        p = q("SELECT parent FROM symbols WHERE name=? COLLATE NOCASE AND is_modded=0 AND "
              "commented_out=0 AND parent IS NOT NULL LIMIT 1", (cur,))
        cur = p[0][0] if p else None
    return guards


_PP_LINT = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b\s*(!?\w+)?")
_CLASS_ANY = re.compile(r"^\s*(modded\s+)?(?:sealed\s+)?class\s+(\w+)")
_M1 = r"(?:proto|native|static|protected|private|sealed|ref|const|autoptr|owned|external|local|out|inout|volatile|event|notnull)"
_OVR = re.compile(r"^\s*(?:" + _M1 + r"\s+)*override\s+(?:" + _M1 + r"\s+)*[\w\[\]<>,]+(?:\s*<[^>;]*>)?\s+(\w+)\s*\(")


def _platform_guard_findings(live):
    """Flag `class X { override ... M(...) }` where vanilla defines M ONLY under an inactive build
    guard (PLATFORM_CONSOLE etc.) AND our override is not itself guarded to match — so it would try
    to compile on PC and fail. Tracks our-side #ifdef too: an override correctly wrapped in the same
    #ifdef is NOT flagged."""
    issues = []
    stack = []      # (class_name, body_depth)
    depth = 0
    pending = None  # class name awaiting '{'
    pp = []         # our-side preprocessor condition stack
    for ln, line in enumerate(live.split("\n"), 1):
        pm = _PP_LINT.match(line)
        if pm:
            d = pm.group(1); tok = pm.group(2) or ""
            if d == "ifdef": pp.append(tok)
            elif d == "ifndef": pp.append("!" + tok)
            elif d == "if": pp.append("?")
            elif d == "elif":
                if pp: pp[-1] = "?"
            elif d == "else":
                if pp:
                    t = pp[-1]; pp[-1] = t[1:] if t.startswith("!") else "!" + t
            elif d == "endif":
                if pp: pp.pop()
            continue
        cm = _CLASS_ANY.match(line)
        if cm:
            pending = cm.group(2)
        if stack and depth == stack[-1][1]:
            om = _OVR.match(line)
            if om and not _guard_pc_absent(" & ".join(pp)):
                cls = stack[-1][0]; meth = om.group(1)
                guards = _vanilla_method_guards(cls, meth)
                if guards and all(_guard_pc_absent(g) for g in guards):
                    tok = _guard_pc_absent(guards[0])
                    issues.append("PLATFORM-GUARD L%d: `%s.%s()`는 바닐라에서 `#ifdef %s` 안에만 정의 "
                                  "(우리 빌드=PC/RELEASE엔 컴파일 안 됨) → override 대상 없음 → 모듈 컴파일 "
                                  "실패(로딩 멈춤). 콘솔 전용이면 `#ifdef %s`로 감싸거나 제거" % (ln, cls, meth, tok, tok))
        for ch in line:
            if ch == "{":
                depth += 1
                if pending is not None:
                    stack.append((pending, depth)); pending = None
            elif ch == "}":
                if stack and depth == stack[-1][1]:
                    stack.pop()
                depth -= 1
                if depth < 0: depth = 0
    return issues


# methods that exist on EVERY class (Managed/Class templated natives) — never flag these
_UNIVERSAL = {"cast", "casto", "classname", "type", "getclassname", "tostr", "getmemberonscript",
              "getvariable", "setvariable", "isinherited", "isinstance"}
# local-var declaration:  `<TypeName> <var>` at a statement boundary, then = or ;
_LOCALDECL = re.compile(r"[{};\)]\s*((?:autoptr\s+|ref\s+)?)([A-Z]\w+)\s+(\w+)\s*[=;]")
_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(")
_NONTYPE = {"if", "for", "while", "switch", "return", "foreach", "else", "case", "new", "delete", "thread"}


_DECL_CLASS = re.compile(r"^\s*(?:modded\s+)?(?:sealed\s+)?class\s+(\w+)")
_DECL_METHOD = re.compile(r"^\s*(?:(?:proto|native|static|protected|private|override|sealed|ref|const|autoptr|owned|external|local|out|inout|volatile|event|notnull)\s+)*[\w\[\]<>,]+(?:\s*<[^>;]*>)?\s+(\w+)\s*\(")


def _name_collisions(live):
    """Flag a method we DECLARE whose name == an existing vanilla CLASS. Enforce then resolves the
    call site to the TYPE (cast/construct), not the method → cryptic 'Types X and Y are unrelated'.
    (This is exactly what `static void FoodStage(...)` did — collided with class FoodStage.)
    Skips constructors (name == enclosing class) and `modded class` (that collision is intentional)."""
    issues = []
    stack = []      # (class_name, body_depth)
    depth = 0
    pending = None
    flagged = set()
    for line in live.split("\n"):
        hm = _DECL_CLASS.match(line)
        if hm:
            pending = hm.group(1)
        if stack and depth == stack[-1][1]:
            mm = _DECL_METHOD.match(line)
            if mm:
                name = mm.group(1)
                enclosing = stack[-1][0]
                if (name not in ("if", "for", "while", "switch", "return", "foreach") and name != enclosing
                        and name not in flagged):
                    # CASE-SENSITIVE: Enforce identifiers are case-sensitive, so method `Set` does NOT
                    # collide with class `set` (the generic container). Only an EXACT-case class clashes
                    # (FoodStage==FoodStage). COLLATE BINARY overrides the table's NOCASE index.
                    rows = q("SELECT file FROM symbols WHERE name=? COLLATE BINARY AND kind='class' "
                             "AND is_modded=0 AND commented_out=0 LIMIT 1", (name,))
                    # ONLY dangerous as a DISCARDED-STATEMENT bare call `Name(args);` — there the
                    # compiler binds Name to the TYPE and fails. When the result is USED as an
                    # expression (`Map().Set(...)`, `x = Map()`), it binds to the method and is fine
                    # (our cache Map()/Set() do exactly that → must NOT flag). Distinguisher: the
                    # call's `)` is immediately followed by `;` (statement) vs `.`/operator (expr).
                    bare = re.search(r"[(;{}=,&|!]\s*" + re.escape(name) + r"\s*\([^()]*\)\s*;", live)
                    if rows and bare:
                        flagged.add(name)
                        issues.append("NAME-COLLISION: 메서드 `%s()`가 vanilla 클래스 `%s`와 충돌 + 단독문장 bare 호출 (%s) — "
                                      "호출이 타입(캐스트)으로 해석돼 'Types ... unrelated' 컴파일 에러. 메서드명 바꿔라(예: %sInfo)."
                                      % (name, name, rows[0][0], name))
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
    return issues


def _method_findings(live):
    """Flag x.Method() where x's type is known (in-file local OR a known class) and Method is absent
    from that type's whole chain. Conservative: only when the type HAS methods indexed (coverage),
    method isn't universal, and receiver isn't super/this/an unknown member."""
    # 1) local var -> declared type (only types that are real classes)
    var_type = {}
    for _, _, typ, var in [(m.group(0), m.group(1), m.group(2), m.group(3)) for m in _LOCALDECL.finditer(live)]:
        if typ in _NONTYPE:
            continue
        if _sym_rows(typ):  # is a known class/enum
            var_type[var] = typ
    issues = []
    flagged = set()
    for m in _CALL.finditer(live):
        recv, meth = m.group(1), m.group(2)
        if recv in ("super", "this", "g_Game", "GetGame"):
            continue
        if meth.lower() in _UNIVERSAL:
            continue
        # determine receiver type: a known class name (static call) OR an in-file local
        rtype = None
        if recv in var_type:
            rtype = var_type[recv]      # in-file local of known type
        elif recv[:1].isupper() and _sym_rows(recv):
            rtype = recv                # Type.StaticMethod(...)
        if not rtype:
            continue
        # HARD-flag only Widget types: enwidgets.c proto = complete coverage → ~0 false positives,
        # and widget read-back (TextWidget.GetText) is the recurring real trap. Non-widget method
        # checks are unreliable (methods added by un-indexed mods like Expansion, parser gaps,
        # scope-blind local typing) so we skip them rather than cry wolf.
        if not rtype.endswith("Widget"):
            continue
        chain_methods = _methods_of_chain(rtype)
        if not chain_methods:
            continue  # no method data for this type -> can't judge
        if meth.lower() in chain_methods:
            continue
        key = (rtype, meth)
        if key in flagged:
            continue
        flagged.add(key)
        ln = live.count("\n", 0, m.start()) + 1
        issues.append("METHOD L%d: `%s.%s()` — `%s`에 그 메서드 없음 (체인 전체 확인). 오타이거나 잘못된 위젯/타입 (예: TextWidget엔 GetText 없음→EditBoxWidget만)" % (ln, recv, meth, rtype))
    return issues


@mcp.tool()
def enforce_lint(code_or_path: str) -> str:
    """Enforce 코드/파일 정적 검사 — 컴파일러처럼 첫 줄 판정(통과? ㅇㅇ/ㄴㄴ).
    검사: ① modded/extends 대상 클래스 실존+비주석 (Unknown type 사전 차단)
    ② C-스타일 캐스트 (int)x ③ "str"+bool 연결 등 gotcha
    ④ 위젯 변수.메서드() 실존 (TextWidget엔 GetText 없음류 — proto 완전커버, 오탐0)
    ⑤ platform-gated 메서드 override (#ifdef PLATFORM_CONSOLE 전용 메서드를 PC빌드에서 override — 로딩멈춤)."""
    if os.path.exists(code_or_path):
        code = open(code_or_path, encoding="utf-8", errors="replace").read()
        label = code_or_path
    else:
        code, label = code_or_path, "<snippet>"
    # lint the LIVE code only (comments are allowed to mention anything)
    live = re.sub(r"/\*.*?\*/", lambda m_: "\n" * m_.group(0).count("\n"), code, flags=re.S)
    live = re.sub(r"//[^\n]*", "", live)

    issues = []
    for m_ in MODDED_DECL.finditer(live):
        cn = m_.group(1)
        en = _engine_note(cn)
        if en:
            issues.append("ENGINE-CLASS: `modded class %s` — 엔진 클래스라 컴파일 거부 ('Engine class cannot be modded'). %s" % (cn, en))
            continue
        rows = [r for r in _sym_rows(cn) if not r[3]]
        live_rows = [r for r in rows if not r[4]]
        if not rows:
            issues.append("UNKNOWN-TYPE: `modded class %s` — 어떤 소스에도 정의 없음 (부팅 사망)" % cn)
        elif not live_rows:
            issues.append("UNKNOWN-TYPE: `modded class %s` — **주석처리된 deprecated 클래스** (부팅 사망). %s:%s" % (cn, rows[0][7], rows[0][8]))
        elif all(r[5].startswith("mod:") for r in live_rows):
            issues.append("requiredAddons 확인: `%s`는 모드(%s) 정의 — 의존성 누락 시 Unknown type" % (cn, live_rows[0][5]))
    for m_ in BASE_DECL.finditer(live):
        bn = m_.group(1)
        rows = [r for r in _sym_rows(bn) if not r[3]]
        if rows and all(r[4] for r in rows):
            issues.append("UNKNOWN-TYPE: 부모 `%s`가 주석처리된 클래스 (부팅 사망)" % bn)
        elif not rows and bn not in ("Managed", "ScriptedWidgetEventHandler"):
            issues.append("부모 `%s` 인덱스에 없음 — 오타/엔진내장/의존성 확인" % bn)
    for m_ in CAST_RE.finditer(live):
        ln = live.count("\n", 0, m_.start()) + 1
        issues.append("GOTCHA L%d: C-스타일 캐스트 `%s` — Enforce에 없음 (Math.Floor/.ToInt 등 사용)" % (ln, m_.group(0).strip()))
    for m_ in STR_PLUS_BOOL.finditer(live):
        ln = live.count("\n", 0, m_.start()) + 1
        issues.append("GOTCHA L%d: string+bool 연결 — 컴파일 에러" % ln)
    issues += _method_findings(live)   # x.Method() not on x's type (TextWidget.GetText class of bug)
    issues += _name_collisions(live)   # method name shadows a vanilla class (FoodStage class of bug)
    issues += _platform_guard_findings(live)  # override of #ifdef PLATFORM_CONSOLE method (toolbar trap)

    head = "통과? %s — %s" % ("ㅇㅇ" if not issues else ("ㄴㄴ (%d건)" % len(issues)), label)
    return "\n".join([head] + ["- " + i for i in issues])


def _config_overrides(text):
    """Parse a config.cpp -> [(item_name, declared_cfg_class)] for every class WITH A BODY that sits
    under a top-level CfgXxx (forward-decls `class X;` have no body -> skipped). Brace-stack parser."""
    s = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    out = []
    stack = []
    i, n = 0, len(s)
    crx = re.compile(r"class\s+(\w+)\s*(?::\s*(\w+)\s*)?")
    while i < n:
        c = s[i]
        if c == "{":
            stack.append(["{"]); i += 1; continue
        if c == "}":
            if stack: stack.pop()
            i += 1; continue
        m = crx.match(s, i)
        if m and (i == 0 or not s[i - 1].isalnum()):
            name = m.group(1)
            j = m.end()
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] == "{":
                cfg = None
                for fr in stack:
                    if fr[0] == "class":
                        cfg = fr[1]; break
                if cfg:
                    out.append((name, cfg))
                stack.append(["class", name, None])
                i = j + 1; continue
            elif j < n and s[j] == ";":
                i = j + 1; continue
            else:
                i = m.end(); continue
        i += 1
    return out


@mcp.tool()
def check_config(config_path: str) -> str:
    """config.cpp 정합성 검사 — 컴파일러처럼 첫 줄 판정(통과? ㅇㅇ/ㄴㄴ). 아이템 override가 바닐라와
    같은 config 클래스(CfgWeapons/CfgMagazines/CfgVehicles)에 선언됐는지 검증(M4A1을 CfgVehicles에
    넣으면 모델없는 유령→스폰X 버그를 차단) + 존재하지 않는 cfg 클래스명(CfgWeapon 오타류) 탐지.
    config 클래스명은 Enfusion에서 대소문자 무시(바닐라도 CfgVehicles/cfgVehicles 혼용)라 NOCASE 비교."""
    if not os.path.exists(config_path):
        return "파일 없음: %s" % config_path
    text = open(config_path, encoding="utf-8", errors="replace").read()
    overrides = _config_overrides(text)
    if not overrides:
        return "통과? ㅇㅇ — override 없음 (%s)" % config_path

    # known top-level cfg classes (from vanilla index), lowercased
    known = {r[0].lower() for r in q("SELECT DISTINCT cfg_class FROM config_classes")}
    if not known:
        return "config 인덱스 비어있음 — `python index_config.py` 먼저 실행"

    issues = []
    seen = set()
    META = ("cfgpatches", "cfgmods")  # addon/mod declaration blocks — their classes aren't items
    for name, cfg in overrides:
        if cfg.lower() in META:
            continue
        # 1) the declared cfg class must be a real top-level config class (catches CfgWeapon typo)
        if cfg.lower() not in known and (cfg, "?") not in seen:
            seen.add((cfg, "?"))
            issues.append("UNKNOWN-CFG: `class %s` — `%s`는 바닐라에 없는 config 클래스 (오타? CfgWeapon→CfgWeapons)" % (name, cfg))
            continue
        # 2) the item must live in that same cfg class in vanilla (catches M4A1 in CfgVehicles)
        van = [r[0] for r in q("SELECT DISTINCT cfg_class FROM config_classes WHERE name=? COLLATE NOCASE", (name,))]
        if not van:
            continue  # unknown item (mod item / new) — override just won't merge with vanilla, not an error
        if cfg.lower() not in {v.lower() for v in van} and name not in seen:
            seen.add(name)
            issues.append("WRONG-CFG: `%s`는 바닐라에서 **%s**에 있는데 override는 `%s`에 선언 — 모델없는 유령 클래스 → 스폰X (그 cfg로 옮겨라)"
                          % (name, "/".join(sorted(set(van))), cfg))

    head = "통과? %s — %s (override %d개 검사)" % ("ㅇㅇ" if not issues else ("ㄴㄴ (%d건)" % len(issues)), config_path, len(overrides))
    return "\n".join([head] + ["- " + i for i in issues[:40]])


@mcp.tool()
def enforce_doc(topic: str) -> str:
    """Enforce 문법/관용구 레퍼런스 검색 (우리 큐레이션 가이드 enforce-script-guide.md 섹션)."""
    if not os.path.exists(GUIDE):
        return "가이드 파일 없음: %s" % GUIDE
    text = open(GUIDE, encoding="utf-8", errors="replace").read()
    # split by headings, return sections mentioning the topic
    parts = re.split(r"(?m)^(#{1,4} .+)$", text)
    hits, cur_head = [], ""
    for seg in parts:
        if seg.startswith("#"):
            cur_head = seg
        elif topic.lower() in (cur_head + seg).lower():
            hits.append(cur_head + "\n" + seg.strip())
    if not hits:
        return "'%s' 관련 섹션 없음. 가이드 헤딩: %s" % (topic, ", ".join(re.findall(r"(?m)^#{1,4} (.+)$", text)[:30]))
    return ("\n\n---\n\n".join(hits))[:8000]


def _in_docker():
    """컨테이너 안인가. 도커면 호스트(윈도우)를 볼 수 없으므로 호스트 점검은 '명령 반환' 으로 돌린다."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8", errors="replace") as f:
            return "docker" in f.read() or "containerd" in f.read()
    except Exception:
        return False


def _steam_libraries():
    """Steam 라이브러리 경로들. DayZ Tools 가 C: 가 아닌 다른 드라이브에 깔려 있을 수 있다."""
    libs, seen = [], set()
    roots = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("ProgramFiles", r"C:\Program Files")]
    for r in roots:
        if r:
            libs.append(os.path.join(r, "Steam"))
    for base in list(libs):
        vdf = os.path.join(base, "steamapps", "libraryfolders.vdf")
        try:
            txt = open(vdf, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for p in re.findall(r'"path"\s*"([^"]+)"', txt):
            libs.append(p.replace("\\\\", "\\"))
    out = []
    for p in libs:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _find_tools():
    """DayZ Tools 의 Bin 폴더. 없으면 ""."""
    env = os.environ.get("DAYZ_TOOLS_BIN", "")
    if env and os.path.isdir(env):
        return env
    for lib in _steam_libraries():
        p = os.path.join(lib, "steamapps", "common", "DayZ Tools", "Bin")
        if os.path.isdir(p):
            return p
    return ""


def _workdrive_hint(bin_dir):
    """과거 마운트에 쓰인 작업 폴더 경로를 WorkDrive 로그에서 되찾는다(환경마다 다르다)."""
    for f in sorted(glob.glob(os.path.join(bin_dir, "Logs", "WorkDrive*.rpt")), reverse=True):
        try:
            txt = open(f, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        m = re.search(r'/mount\s+[Pp]:?\s+"([^"]+)"', txt)
        if m:
            return m.group(1)
    return ""


@mcp.tool()
def check_env() -> str:
    """작업 시작 전 환경 점검. **코드를 짜기 전에 제일 먼저 부른다.**

    실행 모드를 스스로 감지해서 답이 달라진다.
      * venv/네이티브(윈도우) : 호스트를 직접 볼 수 있으므로 DayZ Tools·P: 드라이브·게임데이터를
        **직접 판정**하고, 없을 때만 고치는 명령을 준다.
      * 도커 : 호스트가 안 보이므로 **확인/수정 명령을 돌려준다.** 호출한 쪽이 실행하면 된다.

    P: 마운트와 게임데이터 추출은 WorkDrive.exe 가 무인 실행을 지원하므로 에이전트가 직접 할 수 있다.
    사람이 해야 하는 건 DayZ 와 DayZ Tools 설치뿐이다."""
    L = []
    ok = True
    docker = _in_docker()
    mode = "도커" if docker else ("네이티브(%s)" % os.name)

    # ── 어느 모드든 컨테이너/프로세스가 아는 것 ────────────────
    L.append("[1] 인덱스 DB")
    if not os.path.exists(DB):
        ok = False
        L.append("  ㄴㄴ 없음: " + DB)
        L.append("     저장소를 통째로 클론했는지 확인(data/dayz_scripts.db 가 같이 온다).")
        if docker:
            L.append("     도커라면 data 마운트를 빠뜨렸을 수 있다.")
    else:
        try:
            n_sym = q("SELECT COUNT(*) FROM symbols")[0][0]
            n_mod = q("SELECT COUNT(*) FROM symbols WHERE source!='vanilla'")[0][0]
            L.append("  ㅇㅇ 심볼 %d개 (바닐라 %d / 모드 %d)" % (n_sym, n_sym - n_mod, n_mod))
            if n_mod == 0:
                L.append("     ! 모드 심볼 0 — 내 모드 소스가 인덱싱되지 않았다.")
                L.append("       DAYZ_MCP_MODSET 을 주고 index_local.py 를 다시 돌릴 것.")
        except Exception as e:
            ok = False
            L.append("  ㄴㄴ DB 를 읽지 못함: %s" % e)

    L.append("")
    L.append("[2] 모드 소스 (DAYZ_MCP_MODSET=%s)" % (MODSET or "(미설정)"))
    if not MODSET:
        L.append("  -- 미설정. 내 모드 클래스는 조회되지 않는다(바닐라만).")
    elif not os.path.isdir(MODSET):
        ok = False
        L.append("  ㄴㄴ 경로가 없다" + (" — 마운트가 안 붙었다." if docker else "."))
    else:
        n = 0
        for _, _, fs in os.walk(MODSET):
            n += len([f for f in fs if f.lower().endswith(".c")])
        L.append("  ㅇㅇ .c 파일 %d개" % n)
        if n == 0:
            L.append("     ! 비어 있다. 모드 소스 루트가 맞는지 확인.")

    L.append("")
    L.append("[3] Enforce 가이드 (enforce_doc 용)")
    L.append("  %s %s" % ("ㅇㅇ" if os.path.exists(GUIDE) else "--", GUIDE))

    # ── 호스트 쪽 ─────────────────────────────────────────────
    L.append("")
    if docker or os.name != "nt":
        L.append("[4] 호스트 점검 — 실행 모드가 %s 라 여기서는 볼 수 없다." % mode)
        L.append("    아래를 **직접 실행**해 확인하고, 없으면 고칠 것.")
        T = "<DayZ Tools>/Bin"
        L.append("")
        L.append("  (a) DayZ Tools 설치")
        L.append('      ls "C:/Program Files (x86)/Steam/steamapps/common/DayZ Tools/Bin"')
        L.append("      없으면: Steam 라이브러리 -> 도구 -> DayZ Tools  ← 사람이 해야 함")
        L.append("")
        L.append("  (b) P: 마운트   ->  ls /p/scripts")
        L.append('      없으면: "%s/WorkDrive/WorkDrive.exe" /y /Silent /nowarnings /mount P: "<작업폴더>"' % T)
        L.append('      작업폴더 경로는 %s/Logs/WorkDrive.*.rpt 의 "Command line:" 줄에서 찾을 수 있다.' % T)
        L.append("")
        L.append("  (c) 게임데이터 추출 (P:/scripts 가 비어 있으면)")
        L.append('      "%s/WorkDrive/WorkDrive.exe" /ExtractGameData' % T)
        L.append("      끝난 뒤 index_local.py 재실행.")
    else:
        L.append("[4] 호스트 점검 (네이티브 실행이라 직접 확인함)")
        bin_dir = _find_tools()
        L.append("")
        if not bin_dir:
            ok = False
            L.append("  (a) DayZ Tools  ㄴㄴ 못 찾음")
            L.append("      Steam 라이브러리 -> 도구 -> DayZ Tools 설치  ← 사람이 해야 함")
            L.append("      다른 곳에 있다면 DAYZ_TOOLS_BIN 환경변수로 Bin 경로를 지정.")
        else:
            L.append("  (a) DayZ Tools  ㅇㅇ %s" % bin_dir)
            for sub, why in (("PboUtils/FileBank.exe", "패킹"),
                             ("PboUtils/BankRev.exe", "언팩"),
                             ("CfgConvert/CfgConvert.exe", "bin<->cpp"),
                             ("WorkDrive/WorkDrive.exe", "P: 마운트/추출")):
                p = os.path.join(bin_dir, *sub.split("/"))
                L.append("      %s %-28s (%s)" % ("ㅇㅇ" if os.path.exists(p) else "ㄴㄴ", sub, why))

        L.append("")
        pscripts = os.path.join("P:\\", "scripts")
        if not os.path.isdir("P:\\"):
            ok = False
            L.append("  (b) P: 드라이브  ㄴㄴ 마운트 안 됨")
            if bin_dir:
                hint = _workdrive_hint(bin_dir) or "C:\\Dayzworkfolder"
                L.append("      실행하면 붙는다(무인):")
                L.append('      "%s\\WorkDrive\\WorkDrive.exe" /y /Silent /nowarnings /mount P: "%s"'
                         % (bin_dir, hint))
                if _workdrive_hint(bin_dir):
                    L.append("      (작업폴더 경로는 이 PC 의 WorkDrive 로그에서 가져왔다)")
        else:
            L.append("  (b) P: 드라이브  ㅇㅇ 마운트됨")
            if not os.path.isdir(pscripts):
                ok = False
                L.append("  (c) 게임데이터  ㄴㄴ P:\\scripts 없음 — 추출이 안 됐다")
                if bin_dir:
                    L.append('      "%s\\WorkDrive\\WorkDrive.exe" /ExtractGameData' % bin_dir)
                    L.append("      오래 걸린다. 끝난 뒤 index_local.py 재실행.")
            else:
                n = len(glob.glob(os.path.join(pscripts, "*")))
                L.append("  (c) 게임데이터  ㅇㅇ P:\\scripts (항목 %d개)" % n)

    # ── 도구가 대신 못 하는 절차 ────────────────────────────────
    L.append("")
    L.append("[5] 작업 규칙 — 도구가 대신 못 하므로 반드시 지킬 것")
    L.append("  * 패킹 뒤에는 pbo 를 열어 변경이 실제로 들어갔는지 확인한다.")
    L.append("    FileBank 는 대상 pbo 가 잠겨 있으면 exit=0 을 반환하면서 조용히 건너뛴다")
    L.append("    (게임/서버가 켜져 있으면 잠긴다 -> 끄고 다시 패킹).")
    L.append("  * 배포 뒤에는 사용자에게 이렇게 요청한다:")
    L.append('      "서버를 켜고 profiles/script.log 를 붙여넣어 주세요"')
    L.append("    컴파일 성공 여부는 그 로그로만 확인된다.")
    L.append("  * GUI(레이아웃/stringtable)를 바꿨으면 클라이언트를 완전히 재시작해야 한다.")
    L.append("    reconnect 로는 pbo 가 갱신되지 않는다.")

    head = "환경 점검 [%s]: %s" % (mode, "이상 없음 ㅇㅇ" if ok else "문제 있음 ㄴㄴ")
    return head + "\n\n" + "\n".join(L)

import struct as _struct
import hashlib as _hashlib


def _pbo_header(path):
    """PBO 헤더만 읽는다(데이터 블록은 건드리지 않음).
    반환: (props, entries, data_offset). entries = [(name, method, osize, dsize), ...]"""
    VERS, CPRS = 0x56657273, 0x43707273
    props, ents = [], []
    with open(path, "rb") as f:
        def z():
            o = bytearray()
            while True:
                b = f.read(1)
                if not b or b == b"\x00":
                    break
                o += b
            return o.decode("latin-1")
        while True:
            name = z()
            hdr = f.read(20)
            if len(hdr) < 20:
                break
            method, osize, res, ts, dsize = _struct.unpack("<5I", hdr)
            if name == "" and method == VERS:
                while True:
                    k = z()
                    if k == "":
                        break
                    props.append((k, z()))
                continue
            if name == "" and method == 0 and dsize == 0:
                break
            ents.append((name, method, osize, dsize))
        off = f.tell()
    return props, ents, off, CPRS


def _scan_bytes(path, needles):
    """파일을 청크로 훑어 각 문자열이 있는지. 700MB 짜리 pbo 가 있으므로 통째로 읽지 않는다.
    청크 경계에서 잘리는 걸 막으려고 이전 꼬리를 겹쳐 이어 붙인다."""
    found = {n: False for n in needles}
    raw = [n.encode("utf-8") for n in needles]
    keep = max([len(r) for r in raw] + [1]) - 1
    tail = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            buf = tail + chunk
            for n, r in zip(needles, raw):
                if not found[n] and r in buf:
                    found[n] = True
            if all(found.values()):
                break
            tail = buf[-keep:] if keep else b""
    return found


def _sha1_file(path):
    h = _hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _newest(src_dir):
    """소스 폴더에서 가장 최근에 바뀐 (mtime, 경로)."""
    best = (0, "")
    for root, _, fs in os.walk(src_dir):
        for fn in fs:
            p = os.path.join(root, fn)
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if m > best[0]:
                best = (m, p)
    return best


@mcp.tool()
def check_pbo(pbo_path: str, source_dir: str = "", contains: str = "", also: str = "") -> str:
    """패킹/배포 사후 검증. **패킹한 직후, 그리고 배포한 직후에 부른다.**

    "패킹했다" 와 "변경이 실제로 들어갔다" 는 다른 말이다. FileBank 는 대상 pbo 가
    다른 프로세스에 잠겨 있으면(게임/서버 실행 중) **exit=0 을 반환하면서 조용히 건너뛴다.**
    그래서 성공한 줄 알고 옛 pbo 를 그대로 쓰는 사고가 난다. 이 툴은 pbo 를 직접 열어 확인한다.

    인자
      pbo_path   : 검사할 pbo
      source_dir : 이 pbo 를 만든 소스 폴더(주면 stale 판정 — 조용한 실패를 잡는 핵심)
      contains   : 쉼표로 구분한 문자열들. 모두 pbo 안에 있어야 한다
                   (예: 방금 넣은 한국어 문구, 새 클래스명)
      also       : 쉼표로 구분한 다른 배포본 경로. 해시가 같은지 본다
                   (서버용/클라용 폴더 양쪽에 넣었는지)"""
    L = []
    ok = True

    if not os.path.exists(pbo_path):
        # 도커라 안 보이는 것일 수 있다 — 그때는 실행할 명령을 준다.
        if _in_docker():
            return ("파일이 안 보인다: %s\n"
                    "도커로 실행 중이라 호스트 경로가 마운트돼 있지 않을 수 있다.\n"
                    "직접 확인할 것:  ls -la \"%s\"\n"
                    "또는 pbo 가 있는 폴더를 컨테이너에 마운트하고 다시 부를 것." % (pbo_path, pbo_path))
        return "ㄴㄴ 파일 없음: %s" % pbo_path

    size = os.path.getsize(pbo_path)
    L.append("대상: %s (%.1f MB)" % (pbo_path, size / 1048576.0))

    # ── 1. 헤더 / prefix ──────────────────────────────────────
    try:
        props, ents, off, CPRS = _pbo_header(pbo_path)
    except Exception as e:
        return "ㄴㄴ pbo 헤더를 못 읽음: %s" % e

    prefix = dict(props).get("prefix", "")
    L.append("")
    L.append("[1] prefix / 헤더")
    if not prefix:
        ok = False
        L.append("  ㄴㄴ prefix 가 비었다 — 이 pbo 의 파일들이 경로로 잡히지 않는다.")
    else:
        L.append("  ㅇㅇ prefix = %r" % prefix)
        if prefix.endswith("\\") or prefix.endswith("/"):
            ok = False
            L.append("  ㄴㄴ ★ prefix 끝에 구분자가 붙어 있다.")
            L.append("      이러면 이 pbo 의 스크립트가 **통째로 컴파일에서 빠진다**.")
            L.append("      증상은 엉뚱하게 나온다 — 다른 모드에서 'Can't find variable X'.")
            L.append("      FileBank 는 -property prefix=<이름> 으로 넘길 것($PREFIX$ 파일의 개행도 원인이 된다).")
    L.append("  엔트리 %d개, 데이터 시작 오프셋 %d" % (len(ents), off))

    empty = [n for n, m, o, d in ents if d == 0]
    if empty:
        L.append("  ! 크기 0인 엔트리 %d개: %s" % (len(empty), empty[:3]))

    # $PREFIX$ 가 엔트리로 들어가 있으면 헤더 prefix 와 어긋나는지 본다
    pfx_ent = [n for n, m, o, d in ents if n.lower().lstrip("$").rstrip("$") == "prefix"]
    if pfx_ent:
        L.append("  ! $PREFIX$ 파일이 pbo 안에 포함돼 있다(%s). 보통은 빼도 된다." % pfx_ent[0])

    # ── 2. stale 판정 (FileBank 조용한 실패) ──────────────────
    L.append("")
    L.append("[2] 최신 여부")
    pbo_m = os.path.getmtime(pbo_path)
    if not source_dir:
        L.append("  -- source_dir 를 주면 소스보다 오래됐는지(=패킹이 실제로 됐는지) 판정한다.")
    elif not os.path.isdir(source_dir):
        L.append("  ㄴㄴ source_dir 없음: %s" % source_dir)
    else:
        m, newest = _newest(source_dir)
        if m > pbo_m:
            ok = False
            L.append("  ㄴㄴ ★ pbo 가 소스보다 오래됐다 — 패킹이 실제로 반영되지 않았다.")
            L.append("      소스 최신: %s" % newest)
            L.append("      FileBank 가 잠긴 파일을 건너뛴 경우일 가능성이 높다.")
            L.append("      게임/서버를 끄고 다시 패킹한 뒤 이 툴을 다시 부를 것.")
        else:
            L.append("  ㅇㅇ 소스보다 최신 (소스 대비 +%d초)" % int(pbo_m - m))

    # ── 3. 내용 확인 ──────────────────────────────────────────
    L.append("")
    L.append("[3] 내용 확인")
    needles = [s.strip() for s in contains.split(",") if s.strip()]
    if not needles:
        L.append("  -- contains 로 '방금 넣은 문구/클래스명' 을 주면 실제 포함 여부를 확인한다.")
    else:
        found = _scan_bytes(pbo_path, needles)
        for n in needles:
            if not found[n]:
                ok = False
            L.append("  %s %s" % ("ㅇㅇ" if found[n] else "ㄴㄴ", n))
        if not all(found.values()):
            L.append("      ! 없는 항목이 있다 = 이 pbo 는 그 변경을 담고 있지 않다.")
            L.append("        압축(Cprs) 엔트리 안의 문자열은 이 방식으로 안 잡힐 수 있다 —")
            L.append("        FileBank 로 만든 pbo 는 비압축이라 보통 문제되지 않는다.")

    # ── 4. 다른 배포본과 대조 ─────────────────────────────────
    L.append("")
    L.append("[4] 배포본 대조")
    others = [s.strip() for s in also.split(",") if s.strip()]
    if not others:
        L.append("  -- also 로 다른 배포 경로를 주면 해시를 비교한다(양쪽에 넣었는지).")
    else:
        h0 = _sha1_file(pbo_path)
        L.append("  기준 %s  %s" % (h0[:12], pbo_path))
        for o in others:
            if not os.path.exists(o):
                ok = False
                L.append("  ㄴㄴ 없음: %s" % o)
                continue
            h = _sha1_file(o)
            same = (h == h0)
            if not same:
                ok = False
            L.append("  %s %s  %s" % ("ㅇㅇ" if same else "ㄴㄴ", h[:12], o))
        if not ok:
            L.append("      ! 다르면 한쪽만 갱신된 것이다. 복사가 잠금으로 실패했을 수 있다.")

    L.append("")
    L.append("[5] 다음 단계")
    L.append("  서버를 켜고 profiles/script.log 를 받아 컴파일 결과를 확인할 것.")
    L.append('  사용자에게: "서버를 켜고 profiles/script.log 를 붙여넣어 주세요"')
    L.append("  GUI(레이아웃/stringtable)를 바꿨다면 클라이언트 완전 재시작이 필요하다.")

    head = "패킹/배포 검증: %s" % ("이상 없음 ㅇㅇ" if ok else "문제 있음 ㄴㄴ")
    return head + "\n\n" + "\n".join(L)

_LAYER_RE = re.compile(r"(?i)(^|[\\/])([1-9]_[A-Za-z]+)([\\/]|$)")


def _read(p):
    try:
        return open(p, "rb").read().decode("utf-8", "replace")
    except Exception:
        return ""


def _strip_comments(t):
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return re.sub(r"//[^\n]*", "", t)


def _cfgpatches_names(text):
    """config.cpp 의 CfgPatches 아래 1단계 클래스 이름들 = 이 addon 의 '진짜 이름'."""
    m = re.search(r"class\s+CfgPatches\s*\{", text)
    if not m:
        return []
    i, depth, out = m.end(), 1, []
    while i < len(text) and depth > 0:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 1:
            mm = re.match(r"class\s+([A-Za-z0-9_]+)", text[i:])
            if mm:
                out.append(mm.group(1))
                i += mm.end() - 1
        i += 1
    return out


def _declared_layers(text, module_name):
    """CfgMods ... defs 의 files[] 에 선언된 스크립트 폴더들."""
    return [p.replace("\\", "/") for p in re.findall(r'"([^"]*[1-9]_[A-Za-z]+[^"]*)"', text)]


def _corpus_defines(root, limit_files=4000):
    """모드셋 전체에서 실제로 쓰이는 #ifdef 이름들. 우리 가드가 오타인지 판별하는 근거."""
    seen = set()
    n = 0
    for dirpath, _, fs in os.walk(root):
        for fn in fs:
            if not fn.lower().endswith((".c", ".cpp", ".hpp")):
                continue
            n += 1
            if n > limit_files:
                return seen
            for m in re.finditer(r"#\s*(?:ifdef|ifndef|define)\s+([A-Za-z_][A-Za-z0-9_]*)",
                                 _read(os.path.join(dirpath, fn))):
                seen.add(m.group(1))
    return seen


@mcp.tool()
def check_addon(module_dir: str, addons_dir: str = "") -> str:
    """모듈 하나를 통째로 사전 점검 — **컴파일 에러가 아니라 '조용히 아무 일도 안 일어나는'** 실수를 잡는다.

    Enforce 에서 제일 비싼 실수는 부팅이 죽는 게 아니라, 코드가 멀쩡히 컴파일됐는데
    **실행이 안 되는** 쪽이다. 로그에 아무것도 안 남아서 원인 찾기가 어렵다. 검사 대상:

      1) requiredAddons 가 **CfgPatches 이름**인가 (파일명 아님 — 이름이 다른 pbo 가 흔하다)
      2) `#ifdef` 가드가 실재하는 디파인인가 (틀리면 그 블록이 통째로 사라진다)
      3) `modded class` 가 든 폴더가 config 의 files[] 에 **선언돼 있는가**
         (선언 안 하면 그 파일은 아예 컴파일되지 않는다 = 후킹이 조용히 무효)
      4) $PREFIX$ 위생 (끝의 개행/구분자 -> 스크립트가 통째로 빠진다)

    module_dir : 모듈 소스 폴더(config.cpp 가 있는 곳)
    addons_dir : (선택) pbo 들이 있는 폴더. requiredAddons 이름을 pbo 파일명과 대조해
                 '파일명을 적은 실수' 를 더 정확히 짚어 준다."""
    L, ok = [], True
    if not os.path.isdir(module_dir):
        if _in_docker():
            return ("폴더가 안 보인다: %s\n도커라 마운트가 없을 수 있다. 직접 확인: ls -la \"%s\""
                    % (module_dir, module_dir))
        return "ㄴㄴ 폴더 없음: %s" % module_dir

    cfg_path = ""
    for cand in ("config.cpp", "Config.cpp"):
        p = os.path.join(module_dir, cand)
        if os.path.exists(p):
            cfg_path = p
            break
    if not cfg_path:
        return "ㄴㄴ config.cpp 를 못 찾음: %s" % module_dir

    cfg = _strip_comments(_read(cfg_path))
    names = _cfgpatches_names(cfg)
    L.append("모듈: %s" % module_dir)
    L.append("CfgPatches: %s" % (", ".join(names) if names else "(없음!)"))
    if not names:
        ok = False
        L.append("  ㄴㄴ CfgPatches 가 없으면 이 addon 은 등록되지 않는다.")

    # ── 1. requiredAddons ─────────────────────────────────────
    L.append("")
    L.append("[1] requiredAddons — 파일명이 아니라 CfgPatches 이름이어야 한다")
    req = []
    for blk in re.findall(r"requiredAddons\s*\[\s*\]\s*=\s*\{([^}]*)\}", cfg, re.S):
        req += re.findall(r'"([^"]+)"', blk)
    if not req:
        L.append("  -- requiredAddons 가 비었다. 우리가 modded 하는 대상 addon 을 넣어야")
        L.append("     그 뒤에 로드되고, 대상 클래스가 실제로 존재하게 된다.")
    else:
        known = set()
        if MODSET and os.path.isdir(MODSET):
            for dirpath, _, fs in os.walk(MODSET):
                for fn in fs:
                    if fn.lower() == "config.cpp":
                        known |= set(_cfgpatches_names(_strip_comments(_read(os.path.join(dirpath, fn)))))
        pbo_names = set()
        if addons_dir and os.path.isdir(addons_dir):
            pbo_names = {os.path.splitext(f)[0] for f in os.listdir(addons_dir) if f.lower().endswith(".pbo")}

        L.append("  %d개" % len(req))
        for r in req:
            if r in known:
                L.append("  ㅇㅇ %-42s (모드셋에서 CfgPatches 확인)" % r)
            elif r in pbo_names and r not in known:
                ok = False
                L.append("  ㄴㄴ %-42s ★ pbo 파일명과 같다. CfgPatches 이름인지 확인할 것." % r)
                L.append("       (우리 것 중에도 파일명과 CfgPatches 이름이 다른 게 있다)")
            else:
                L.append("  ?? %-42s (모드셋 밖 — 바닐라/서드파티면 정상)" % r)

    # ── 2. #ifdef 가드 ────────────────────────────────────────
    L.append("")
    L.append("[2] #ifdef 가드 — 틀리면 블록이 통째로 사라진다(에러 없음)")
    guards = {}
    for dirpath, _, fs in os.walk(module_dir):
        for fn in fs:
            if fn.lower().endswith(".c"):
                p = os.path.join(dirpath, fn)
                for m in re.finditer(r"(?m)^\s*#\s*if(?:n?def)\s+([A-Za-z_][A-Za-z0-9_]*)", _read(p)):
                    guards.setdefault(m.group(1), set()).add(os.path.relpath(p, module_dir))
    if not guards:
        L.append("  -- 사용 안 함")
    else:
        corpus = _corpus_defines(MODSET) if (MODSET and os.path.isdir(MODSET)) else set()
        for g, files in sorted(guards.items()):
            if not corpus:
                L.append("  ?? %-28s (모드셋 미설정이라 대조 불가)" % g)
            elif g in corpus:
                L.append("  ㅇㅇ %-28s %s" % (g, ", ".join(sorted(files))[:60]))
            else:
                ok = False
                L.append("  ㄴㄴ %-28s ★ 모드셋 어디에도 없는 디파인." % g)
                L.append("       오타면 이 가드 안의 코드가 **전부 사라진다**. 파일: %s"
                         % ", ".join(sorted(files))[:60])

    # ── 3. 스크립트 레이어 선언 ───────────────────────────────
    L.append("")
    L.append("[3] 스크립트 폴더가 config 에 선언됐나 — 빠지면 그 파일은 컴파일조차 안 된다")
    declared = _declared_layers(cfg, os.path.basename(module_dir))
    have = {}
    for dirpath, _, fs in os.walk(module_dir):
        if not any(f.lower().endswith(".c") for f in fs):
            continue
        rel = os.path.relpath(dirpath, module_dir).replace("\\", "/")
        m = _LAYER_RE.search("/" + rel + "/")
        if m:
            have.setdefault(m.group(2), []).append(rel)
    if not have:
        L.append("  -- .c 파일이 없다(에셋 전용 addon).")
    else:
        for layer, dirs in sorted(have.items()):
            hit = [d for d in declared if re.search(r"(?i)[\\/]%s([\\/]|$)" % re.escape(layer), "/" + d)]
            if hit:
                L.append("  ㅇㅇ %-10s 선언됨: %s" % (layer, hit[0]))
            else:
                ok = False
                L.append("  ㄴㄴ %-10s ★ .c 가 있는데 files[] 에 없다 -> 컴파일 안 됨(후킹이 조용히 무효)" % layer)
                L.append("       해당 폴더: %s" % ", ".join(dirs)[:70])
        for d in declared:
            m = _LAYER_RE.search("/" + d + "/")
            if m and m.group(2) not in have:
                L.append("  !  %-10s 선언은 있는데 .c 가 없다: %s" % (m.group(2), d))

    # ── 4. $PREFIX$ ───────────────────────────────────────────
    L.append("")
    L.append("[4] $PREFIX$")
    pf = os.path.join(module_dir, "$PREFIX$")
    if not os.path.exists(pf):
        L.append("  -- 파일 없음. FileBank 에 -property prefix=<이름> 으로 넘기면 된다(권장).")
    else:
        raw = open(pf, "rb").read()
        txt = raw.decode("utf-8", "replace")
        if raw != txt.strip().encode("utf-8"):
            ok = False
            L.append("  ㄴㄴ ★ 끝에 개행/공백이 붙어 있다 -> prefix 가 오염돼 스크립트가 통째로 빠진다.")
            L.append("      내용: %r" % raw[:40])
        elif txt.endswith("\\") or txt.endswith("/"):
            ok = False
            L.append("  ㄴㄴ ★ 끝에 구분자가 붙어 있다 -> 같은 증상.")
        else:
            L.append("  ㅇㅇ %r" % txt)

    L.append("")
    L.append("[5] 다음 단계")
    L.append("  modded class 하나하나는 check_modded 로, 문법은 enforce_lint 로 따로 확인할 것.")
    L.append("  패킹 뒤에는 check_pbo 로 실제 반영을 확인할 것.")

    head = "모듈 점검: %s" % ("이상 없음 ㅇㅇ" if ok else "문제 있음 ㄴㄴ")
    return head + "\n\n" + "\n".join(L)

"""find_hook — '어디를 후킹해야 하나' 를 우리 소스의 검증된 선례로 답한다."""

# 우리 모드셋에서 실제로 쓰이는 후킹 형태 (650개 .c 를 집계해 뽑은 것).
# 왼쪽이 흔할수록 안전하고 손이 덜 간다.
_HOOK_FORMS = [
    ("레이아웃 리다이렉트", r"override\s+string\s+Get\w*Layout\w*\s*\(", "안전",
     "경로를 돌려주는 getter 를 덮는다. 원본 로직을 하나도 안 건드려 제일 안전하다."),
    ("super 후 보정", r"super\.(\w+)\s*\(", "안전",
     "super 를 그대로 부르고 결과만 고친다. 계산 로직을 베끼지 않아도 된다."),
    ("키/문자열 치환표", r"s_\w*\.Set\(\s*\"", "안전",
     "키를 우리 키로 바꾸는 map. 못 찾으면 원본을 그대로 두는 형태여야 안전하다."),
    ("위젯 SetText", r"\.SetText\(", "보통",
     "만들어진 위젯의 텍스트만 바꾼다. 위젯 이름이 바뀌면 조용히 넘어간다(안전한 실패)."),
    ("override static", r"override\s+static\s", "위험",
     "super 없이 통째 교체. 원본이 갱신되면 우리 복사본이 낡는다. 짧은 함수에만 쓸 것."),
    ("CreateWidgets 직접", r"CreateWidgets\(\s*\"", "위험",
     "레이아웃 경로가 코드에 박혀 getter 가 없는 경우. 감싸는 함수를 통째로 베껴야 한다."),
]


def _hook_forms_in(text):
    out = []
    for name, rx, risk, why in _HOOK_FORMS:
        if re.search(rx, text):
            out.append((name, risk, why))
    return out


def _enclosing(text, pos):
    """pos 를 감싸는 (modded class, 메서드) 이름을 되짚는다."""
    head = text[:pos]
    cls = None
    for m in re.finditer(r"(?:modded\s+)?class\s+([A-Za-z0-9_]+)", head):
        cls = m.group(1)
    meth = None
    for m in re.finditer(r"(?m)^\s*(?:override\s+|static\s+|protected\s+)*[A-Za-z_][\w<>\[\]]*\s+([A-Za-z_]\w*)\s*\([^;]*$", head):
        meth = m.group(1)
    return cls, meth


@mcp.tool()
def find_hook(target: str, ref_dir: str = "", limit: int = 12) -> str:
    """'이걸 바꾸려면 어디를 건드려야 하나' — **우리 소스의 검증된 선례**로 답한다.

    코드를 새로 지어내기 전에 이걸 먼저 부른다. 우리 모드셋(DAYZ_MCP_MODSET)은 대부분
    실제로 동작 중인 코드라, 같은 문제를 이미 어떻게 풀었는지가 그대로 답이 된다.

    target : 찾을 것 — 스트링테이블 키(STR_EXPANSION_...), 클래스명, 화면에 보이는 문구 등
    ref_dir: (선택) 서드파티 원본 소스를 마운트했다면 그 경로.
             거기서 '값을 세팅하는 지점' 을 찾고, 감싸는 클래스에 getter 가 있는지까지 본다.

    돌려주는 것
      1) 우리 소스의 선례 — 파일/클래스/메서드 + 쓰인 후킹 형태와 위험도
      2) ref_dir 이 있으면 원본의 세팅 지점 + getter 유무(= 3줄 수정이냐 통째 재작성이냐)
      3) 후킹 형태별 요약(안전한 것부터)"""
    if not (MODSET and os.path.isdir(MODSET)):
        return ("DAYZ_MCP_MODSET 이 없다. 우리 소스가 있어야 선례를 보여 줄 수 있다.\n"
                "도커라면 모드 소스 루트를 /modset 으로 마운트할 것.")

    needle = target.strip().lstrip("#$")
    if not needle:
        return "target 이 비었다."

    L = ["찾는 것: %s" % target, ""]

    # ── 1) 우리 소스의 선례 ───────────────────────────────────
    hits = []
    for dirpath, _, fs in os.walk(MODSET):
        for fn in fs:
            if not fn.lower().endswith((".c", ".cpp", ".layout", ".csv")):
                continue
            p = os.path.join(dirpath, fn)
            t = _read(p)
            if not t or needle.lower() not in t.lower():
                continue
            rel = os.path.relpath(p, MODSET)
            for m in re.finditer(re.escape(needle), t, re.I):
                cls, meth = _enclosing(t, m.start())
                line = t.count("\n", 0, m.start()) + 1
                hits.append((rel, line, cls, meth, t))
                break  # 파일당 첫 지점만
    L.append("[1] 우리 소스의 선례 — %d개 파일" % len(hits))
    if not hits:
        L.append("  없음. 이 프로젝트에서 처음 다루는 대상이다.")
        L.append("  search_symbols / find_usages 로 범위를 넓혀 보고, 없으면 아래 [3] 의")
        L.append("  안전한 형태부터 골라 새로 만든다.")
    else:
        forms_all = collections.Counter()
        for rel, line, cls, meth, t in hits[:limit]:
            L.append("  %s:%d" % (rel, line))
            if cls or meth:
                L.append("     %s%s" % ("class %s" % cls if cls else "",
                                        "  ->  %s()" % meth if meth else ""))
            fs_ = _hook_forms_in(t)
            for name, risk, _why in fs_:
                forms_all[(name, risk)] += 1
            if fs_:
                L.append("     형태: %s" % ", ".join("%s(%s)" % (n, r) for n, r, _ in fs_))
        if len(hits) > limit:
            L.append("  ... 외 %d개 파일" % (len(hits) - limit))
        if forms_all:
            L.append("")
            L.append("  이 선례들이 쓴 형태:")
            for (n, r), c in forms_all.most_common():
                L.append("    %-22s %-4s %d개 파일" % (n, r, c))

    # ── 2) 원본에서 세팅하는 지점 ─────────────────────────────
    L.append("")
    if not ref_dir:
        L.append("[2] 원본 세팅 지점 — ref_dir 을 주면 찾는다")
        L.append("    (서드파티 소스를 언팩해 두고 그 경로를 넘기면,")
        L.append("     값을 세팅하는 곳과 getter 유무까지 짚어 준다)")
    elif not os.path.isdir(ref_dir):
        L.append("[2] ref_dir 없음: %s" % ref_dir)
    else:
        L.append("[2] 원본 세팅 지점 (%s)" % ref_dir)
        found = 0
        for dirpath, _, fs in os.walk(ref_dir):
            for fn in fs:
                if not fn.lower().endswith((".c", ".layout")):
                    continue
                p = os.path.join(dirpath, fn)
                t = _read(p)
                if not t or needle.lower() not in t.lower():
                    continue
                rel = os.path.relpath(p, ref_dir)
                for m in re.finditer(re.escape(needle), t, re.I):
                    seg = t[max(0, m.start() - 220):m.start() + 80]
                    kind = None
                    if re.search(r"\.SetText\(\s*[^)]*$", seg):
                        kind = "SetText"
                    elif re.search(r"=\s*\"[^\"]*$", seg):
                        kind = "대입"
                    elif fn.lower().endswith(".layout"):
                        kind = "레이아웃 정적 텍스트"
                    cls, meth = _enclosing(t, m.start())
                    line = t.count("\n", 0, m.start()) + 1
                    getter = re.search(r"override\s+string\s+Get\w*Layout\w*\s*\(", t)
                    L.append("  %s:%d  %s" % (rel, line, kind or ""))
                    if cls or meth:
                        L.append("     %s%s" % ("class %s" % cls if cls else "",
                                                "  ->  %s()" % meth if meth else ""))
                    if fn.lower().endswith(".layout"):
                        L.append("     -> 레이아웃 텍스트. 이 파일을 쓰는 클래스에 GetLayoutFile() 이 있으면")
                        L.append("        복사본으로 리다이렉트하는 게 가장 안전하다.")
                    elif getter:
                        L.append("     -> 같은 파일에 레이아웃 getter 있음 = 리다이렉트로 풀릴 가능성 높음 (안전)")
                    elif meth:
                        L.append("     -> getter 없음. %s() 를 override 해서 **super 후 보정**을 먼저 검토할 것." % meth)
                        L.append("        super 로 안 되면 그때 통째 교체(위험, 함수가 짧을 때만).")
                    found += 1
                    break
                if found >= limit:
                    break
            if found >= limit:
                break
        if not found:
            L.append("  없음.")

    # ── 3) 형태별 요약 ────────────────────────────────────────
    L.append("")
    L.append("[3] 후킹 형태 — 위에서부터 우선 검토할 것")
    for name, _rx, risk, why in _HOOK_FORMS:
        L.append("  [%s] %s" % (risk, name))
        L.append("        %s" % why)

    L.append("")
    L.append("[4] 고른 뒤에는")
    L.append("  check_modded 로 대상 클래스가 실존하는지, enforce_lint 로 문법을,")
    L.append("  check_addon 으로 가드/레이어/requiredAddons 를 확인하고 패킹한다.")
    return "\n".join(L)

if __name__ == "__main__":
    mcp.run()
