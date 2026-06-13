# lake-dayz

[English](README.md) · **한국어**

> DayZ Enforce 코드를 짜기 **전에**, 컴파일러처럼 **"통과? ㅇㅇ / ㄴㄴ"** 로 판정해 주는 MCP 서버. `modded class`·`extends` 대상이 진짜 존재하는지(주석처리된 죽은 코드 아닌지), 어느 모듈에 있는지, 누가 쓰는지(이해관계)를 즉답한다. 패킹 전에 호출해서 부팅 사망을 미리 거른다.
>
> 🎮 점령전 DayZ 서버 **[dayzlake.online](https://dayzlake.online)** 만들다가 탄생한 도구.

---

## 왜 만들었나

DayZ Enforce는 **오프라인 컴파일 검증 수단이 없다**. 잘못된 코드의 에러는 **게임을 켜야** 처음 드러난다.

실제 사고:

```enforce
modded class ActionFishing { override string GetText() { ... } }
```

→ 부팅하자마자 **`Can't compile World script module! Unknown type 'ActionFishing'`** → 게임 UI 사망.

원인은 "ActionFishing이 삭제됐다"가 **아니었다.** DayZ는 **deprecated 코드를 지우지 않고 주석으로 남긴다** — `actionfishing.c` 1~52줄 전체가 `/* ... */`이고 살아있는 클래스는 `ActionFishingNew`다. `grep "class ActionFishing"` 은 주석 속 죽은 코드를 잡고, *"코드가 있네?!"* 하고 맹신해 modded 하면 죽는다.

> **교훈: "정의돼 있다 ≠ 살아 있다."** 주석인지, 정의만 있고 안 쓰는지 — **이해관계까지** 봐야 한다.

`check_modded("ActionFishing")` → **ㄴㄴ** 한 줄로 끝날 일. 그래서 만들었다.

---

## 뭐가 되나

전부 **판정/근거 우선** 출력. 첫 줄이 결론 (`ㅇㅇ`=통과 / `ㄴㄴ`=불가).

| 도구 | 용도 |
|---|---|
| **`check_modded(class)`** | `modded class` 사전 판정 — 실존? 주석처리(deprecated)? 모듈? 이미 누가 mod? 쓰는곳? |
| **`enforce_lint(code\|path)`** | 정적 검사 — unknown-type(주석 포함)·C캐스트·`string+bool`·위젯 메서드 실존·이름충돌·platform-gated override |
| **`check_config(path)`** | `config.cpp` 정합성 — 아이템이 맞는 `CfgXxx`에 선언됐나 (WRONG-CFG = 모델없는 유령 → 스폰버그) |
| `symbol_lookup(name)` | 심볼 카드: 소스별 정의/부모/모듈/`파일:라인`/주석여부/modded현황 |
| `class_info(name)` | 부모 체인(로컬 소스 권위) + 자식 + 멤버 시그니처 |
| `find_usages(symbol)` | 쓰는곳: vanilla 레퍼런스 Referenced-by/References + 모드셋 소스 실시간 grep |
| `search_symbols(pattern)` | LIKE 패턴 검색 (`ActionFish%`, `%Teleport%`) |
| `enforce_doc(topic)` | Enforce 문법 / 모딩 디자인 패턴 (큐레이션 가이드 섹션 검색) |

---

## 어떻게 동작

```text
index_local.py  ─ P:\scripts + (선택)모드셋 주석-aware 파싱 (commented_out 플래그) ─┐
index_config.py ─ P:\DZ\**\config.cpp → 아이템→CfgXxx 매핑                          ├→ data/dayz_scripts.db
DayZ 스크립트 Doxygen 레퍼런스 ─ 멤버+시그니처 + References/Referenced-by            ─┘  (미리 빌드돼 DB에 동봉)
server.py       ─ 위 DB로 도구 서빙 (stdio MCP)
```

- **`commented_out` 플래그** — "주석처리된 죽은 코드" 여부 + `파일:라인`. `P:\`는 게임과 동기된 권위 소스.
- **멤버/교차참조** — DayZ 스크립트 Doxygen에서 추출(주석 코드 배제), **DB에 미리 빌드돼 동봉**되므로 바로 쓴다.
- **상속**은 로컬 소스가 권위.
- 현재 DB: 심볼 7,378 / 메서드 31,877 / 멤버 32,259 / 교차참조 75,548 / config 클래스 90,870.

---

## 설치

DB(`data/dayz_scripts.db`)가 동봉돼 있어 클론하면 바로 동작한다. **DayZ·DayZ Tools 없이도 질의·검증 가능**하고, DB를 **갱신**(새 게임 버전)하려면 DayZ Tools로 추출한 `P:\`가 필요하다.

### A. 네이티브 (가장 단순)

```cmd
cd <repo>
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

claude mcp add -s user lake-dayz ^
  <repo>\.venv\Scripts\python.exe ^
  <repo>\server.py
```

### B. 도커 (서버만)

```cmd
docker build -t lake-dayz .

claude mcp add -s user lake-dayz -- ^
  docker run -i --rm ^
    -v "<repo>\data:/data:ro" ^
    -v "<your-mod-source-root>:/modset:ro" ^
    -v "<dir-with-enforce-script-guide.md>:/docs:ro" ^
    lake-dayz
```

마운트가 read-only라, 호스트에서 DB만 다시 만들면 컨테이너가 자동으로 새 DB를 본다.
환경변수: `DAYZ_MCP_DB` / `DAYZ_MCP_MODSET` / `DAYZ_MCP_GUIDE`.

> `enforce_doc` 는 `enforce-script-guide.md` 를 서빙한다 — 네이티브는 repo 루트(또는 `DAYZ_MCP_GUIDE`), 도커는 `/docs` 마운트에 둔다.

---

## 데이터 갱신 (게임 업데이트 후)

```cmd
.venv\Scripts\python.exe index_local.py     REM P:\scripts (+ DAYZ_MCP_MODSET 설정 시 모드셋) 재인덱스
.venv\Scripts\python.exe index_config.py    REM P:\DZ config 재인덱스
```

1. 게임 업데이트 → DayZ Tools로 **P:\ 재추출** (`P:\`가 권위 소스).
2. `index_local.py` — 거의 항상 이것만 (modded class 판정의 핵심).
3. 자기 모드까지 보려면 `DAYZ_MCP_MODSET` 에 모드 소스 루트(예: `@YourMod\source`)를 지정. 각 하위폴더 = 모드 하나.

---

## 운용 규칙

> **`modded class X` / `class X : Y` / `extends Y` 가 들어가는 코드는, 패킹 전에 `check_modded(X)` + `enforce_lint(파일)` 를 반드시 통과시킨다. 아이템 config override는 `check_config`.**

판정이 **ㄴㄴ**면 패킹 금지. 이 한 단계가 "코드 있네?! → 부팅 사망"을 막는다.

---

## 만든 배경

점령전 DayZ 서버 **[dayzlake.online](https://dayzlake.online)** 를 만들다가 시작됐습니다. 부팅해야만 터지는 Enforce 실수에 계속 당하는 게 답답해서, 패킹 전에 미리 잡으려고 이 MCP를 만들었어요. DayZ가 한국에서도 더 발전하길 바랍니다 — 서버에도 한번 놀러오세요. 🇰🇷

---

## 라이선스

- **이 프로젝트의 자체 코드** (`server.py`, 인덱서 등) — **GPLv3**, [LICENSE](LICENSE) 참고.
- `data/dayz_scripts.db` 에 인덱싱된 **DayZ 스크립트 데이터**는 **DayZ © Bohemia Interactive** 에서 파생됐으며 **DayZ Public License – No Derivatives (DPL-ND)** 의 적용을 받는다: <https://www.bohemia.net/community/licenses/dayz-public-license-no-derivatives-dpl-nd>
- 본 프로젝트는 독립적인 비공식 모딩 도구이며 **Bohemia Interactive와 무관/비제휴**다.

## 크레딧

- 구조 참고: [steffenbk/enfusion-mcp-BK](https://github.com/steffenbk/enfusion-mcp-BK) — Arma Reforger용 Enfusion MCP (틀만 차용, 데이터는 DayZ).
