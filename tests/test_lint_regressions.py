# 이슈 #1 #2 #3 회귀 테스트.
#   python tests/test_lint_regressions.py
# mcp 패키지 없이 server.py 를 그대로 불러 함수만 쓴다.
import importlib.util
import os
import sys
import types

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DAYZ_MCP_DB", os.path.join(ROOT, "data", "dayz_scripts.db"))


def load(modset=None):
    """server.py 를 mcp 스텁과 함께 로드. modset 을 주면 그 값으로 세팅."""
    if modset is None:
        os.environ.pop("DAYZ_MCP_MODSET", None)
    else:
        os.environ["DAYZ_MCP_MODSET"] = modset
    m = types.ModuleType("mcp")
    s = types.ModuleType("mcp.server")
    f = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, *a, **k):
            pass

        def tool(self, *a, **k):
            return lambda fn: fn

        def run(self):
            pass

    f.FastMCP = FastMCP
    sys.modules.update({"mcp": m, "mcp.server": s, "mcp.server.fastmcp": f})
    spec = importlib.util.spec_from_file_location("srv_%s" % (modset or "none"),
                                                  os.path.join(ROOT, "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FAILED = []


def check(name, cond, detail=""):
    print("  %-58s %s" % (name, "OK" if cond else "*** FAIL ***"))
    if not cond:
        FAILED.append("%s %s" % (name, detail))


# ── #1 NAME-COLLISION: 버려지는 단독 문장만 잡아야 한다 ──────────────
# vanilla 에 실재하는 클래스명을 메서드명으로 써야 검사가 발동한다.
print("[#1] NAME-COLLISION — 단독 문장만")
srv = load()
COLLIDE = "FoodStage"   # vanilla 클래스이면서 이슈에 언급된 그 버그 유형

# ★ 이 검사는 **줄 단위 파서**다. 한 줄짜리 코드로는 클래스/메서드 선언이
# 잡히지 않아 검사 자체가 발동하지 않는다(테스트가 조용히 무의미해진다).
def src(sig, body):
    lines = [
        "class LakeThing",
        "{",
        "\t" + sig,
        "\t{",
        "\t\t" + body,
        "\t}",
        "",
        "\tvoid B()",
        "\t{",
        "\t\t%s",
        "\t}",
        "}",
        "",
    ]
    return "\n".join(lines)


CASES_PASS = [
    ("대입",      src("ref map<string, int> %s()" % COLLIDE, "return null;") % ("auto x = %s();" % COLLIDE)),
    ("함수 인자",  src("int %s()" % COLLIDE, "return 1;") % ("Print(%s());" % COLLIDE)),
    ("메서드 체인", src("ref map<string, int> %s()" % COLLIDE, "return null;") % ("%s().Count();" % COLLIDE)),
]
for label, code in CASES_PASS:
    out = srv.enforce_lint(code)
    check("통과해야 함: %s" % label, "NAME-COLLISION" not in out, out[:120])

BARE = src("void %s()" % COLLIDE, "") % ("%s();" % COLLIDE)
out = srv.enforce_lint(BARE)
check("잡아야 함: 단독 문장 bare 호출", "NAME-COLLISION" in out, out[:120])
# ── #2 C캐스트: 문자열 리터럴 안은 무시 ──────────────────────────────
print()
print("[#2] C-style cast — 문자열 안은 무시")
out = srv.enforce_lint('class A { void Warn() { Print("never write (int)x in Enforce"); } }')
check("통과해야 함: 문자열 속 (int)x", "GOTCHA" not in out or "캐스트" not in out, out[:160])

out = srv.enforce_lint("class A { void B() { int y = (int)x; } }")
check("잡아야 함: 진짜 C캐스트", "캐스트" in out, out[:160])

# 문자열 blanking 이 STR_PLUS_BOOL 을 망가뜨리지 않아야 한다
out = srv.enforce_lint('class A { void B() { string s = "hp:" + true; } }')
check("여전히 잡아야 함: string + bool", "bool" in out.lower(), out[:160])

# ── #3 find_usages: MODSET 미설정이면 CWD 를 훑지 않는다 ─────────────
print()
print("[#3] find_usages — MODSET 미설정 시 CWD 스캔 금지")
import tempfile

tmp = tempfile.mkdtemp()
open(os.path.join(tmp, "bait.c"), "w", encoding="utf-8").write("class ZZTestBait {}\n")
cwd = os.getcwd()
try:
    os.chdir(tmp)
    srv_none = load(None)
    out = srv_none.find_usages("ZZTestBait")
    check("CWD 파일을 긁지 않음", "bait.c" not in out, out[:160])
    check("미설정을 명시함", "DAYZ_MCP_MODSET" in out, out[:160])
finally:
    os.chdir(cwd)

# MODSET 을 주면 라벨이 하드코딩 @LakeProject 가 아니어야 한다
srv_set = load(tmp)
out = srv_set.find_usages("ZZTestBait")
check("MODSET 주면 스캔함", "bait.c" in out, out[:160])
check("라벨이 @LakeProject 하드코딩 아님", "@LakeProject" not in out, out[:160])

print()
if FAILED:
    print("실패 %d건:" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("전부 통과")
