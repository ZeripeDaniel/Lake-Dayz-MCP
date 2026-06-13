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
import os, re, glob, sqlite3

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
    검사: 실존 여부, 주석처리(deprecated) 여부, 모듈 위치, 이미 modded한 모드, 사용 흔적."""
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


if __name__ == "__main__":
    mcp.run()
