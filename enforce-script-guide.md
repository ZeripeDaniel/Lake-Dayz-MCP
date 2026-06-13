# Enforce Script 작성 가이드 (DayZ)

DayZ = **Enfusion 엔진** + **Enforce Script 언어**(우리가 만지는 `.c`). 이 문서는 우리 LakeProject에서 실제로 쓰는 문법/API + 겪은 함정을 모은 실전 레퍼런스다.

> 출처: Bohemia 위키 `DayZ:Enforce Script Syntax` + 우리 코드베이스(P:\scripts 바닐라, Expansion 언팩) 실측. 헷갈리면 **추측하지 말고** 언팩한 모드/바닐라에서 같은 idiom을 grep해서 베껴라.

---

## 0. 황금 규칙 (Enforce ≠ C 핵심)

1. **C 스타일 캐스트 `(int)x` 없음.** `(int)c[0]` → 컴파일 에러("can't find variable"). → `Math.Round()/Math.Floor()` 쓰거나, 문자열엔 그냥 float을 넣어라(`"" + c[0]` 됨).
2. **한 표현식을 여러 줄로 쪼개지 마라.** `return a\n && b;` → syntax error. 한 줄로 쓰거나 중간 변수로 풀어라.
3. **`string + bool` 금지.** `"" + boolVal` 위험. `if`로 문자열을 만들어라. (`string + int`, `string + float`는 OK.)
4. **`modded class`는 원본의 `private` 멤버에 접근 가능.** (Expansion이 바닐라 `private`를 그대로 씀.) private라고 못 쓴다 착각하지 마라.
5. **기본 메서드는 virtual.** 오버라이드는 반드시 `override` 키워드.
6. **참조는 기본 weak.** 객체를 살려두려면 `ref` (멤버) / `autoptr` (지역). 비동기 콜백 객체는 어딘가 `ref`로 잡아둬야 GC 안 됨.

---

## 1. 타입과 변환

`int`, `float`, `bool`, `string`, `vector`, `void`.

```c
int a = 5;
float f = 9.5;
bool b = true;
string s = "hi";
vector v = "10 0 22";     // 문자열에서 벡터 초기화
```

**변환:**
- float→int: `(int)` 없음. `Math.Round(f)` / `Math.Floor(f)` (반환 float). int 변수에 대입하면 narrowing.
- 숫자→문자열: `"" + intVal`, `"" + floatVal` 모두 OK. **bool은 안 됨** → `if`로.
- 문자열→int: `s.ToInt()`. 문자열→float: `s.ToFloat()`.

**vector:**
```c
float x = v[0]; float y = v[1]; float z = v[2];   // 인덱싱 OK
v[1] = 0;                                          // 컴포넌트 대입 OK
float d = vector.Distance(a, b);
```

---

## 2. 변수 / 스코프 / 제어문

```c
int x;
x = 5;
if (cond) { ... } else { ... }
for (int i = 0; i < n; i++) { ... }
while (cond) { ... }
switch (val) { case 1: ...; break; default: ...; }
```
- 같은 이름의 중첩 스코프 변수는 DayZ에서 에러날 수 있으니 피해라.
- 연산자: `+ - * / %`, `+= -= ...`, `== != < <= > >=`, `&& || !`, `++ --`.

---

## 3. 배열 / 맵

**동적 배열 `array<T>`** (typedef: `TStringArray=array<string>`, `TIntArray`, `TFloatArray`, `TClassArray`, `TVectorArray`):
```c
ref array<string> names = new array<string>();
names.Insert("Peter");
string n = names.Get(0);
int c = names.Count();
int idx = names.Find("Peter");   // 없으면 -1
names.Remove(0);
foreach (string name : names) { ... }
```

**맵 `map<K,V>`:**
```c
ref map<string, string> m = new map<string, string>();
m.Set("uid", "aurelia");          // insert or update
string val;
if (m.Find("uid", val)) { ... }   // 반환 bool, val에 결과
bool has = m.Contains("uid");
m.Remove("uid");
int n2 = m.Count();
```

---

## 4. 클래스 / 상속 / modded

```c
class MyThing
{
    protected int m_Value;
    void MyThing() { }            // 생성자 = 클래스명
    void ~MyThing() { }           // 소멸자 = ~클래스명
    void Say() { Print("hi"); }
}

class Child: MyThing             // 상속 (콜론)
{
    override void Say() { super.Say(); Print("child"); }
}
```

**modded class** (기존 클래스 확장 — 우리가 vanilla/Expansion 후킹할 때 쓰는 핵심):
```c
modded class SomeVanillaClass
{
    override void SomeMethod(...)
    {
        super.SomeMethod(...);    // 원본 호출 (체인)
        // 우리 코드
    }
}
```
- 로드 순서상 **나중에 로드된 modded가 최상단** → 그게 먼저 실행되고 `super`로 아래로 내려감. 순서는 `requiredAddons` 의존성으로 보장.
- `private`도 접근 가능(위 황금규칙 4).
- 접근자: `private`(클래스 내부만), `protected`(자식/modded 포함), 기본 public.

---

## 5. ref / autoptr (메모리)

```c
class Parent { ref Child m_child; }   // strong: 살려둠
class Child  { Parent m_parent; }     // weak: 안 살림(순환참조 방지)

autoptr MyClass o = new MyClass();    // 지역 strong, 스코프 끝나면 자동 해제
```
- **비동기 REST/콜백 핸들러 객체**는 호출 끝날 때까지 `ref`로 어딘가(예: static map) 잡아둬야 한다. 안 그러면 GC되어 콜백이 죽는다. (우리 `LakeProjectFactionLoad`의 pending map이 그 역할.)

---

## 6. 우리가 자주 쓰는 DayZ API (검증됨)

```c
// 게임/서버
GetGame().IsServer();  GetGame().IsClient();
GetGame().GetPlayers(players);                 // array<Man>, 서버측 전체
GetGame().CreateObjectEx("ClassName", pos, ECE_PLACE_ON_SURFACE);
GetGame().GetCallQueue(CALL_CATEGORY_GUI).CallLater(func, delayMs, repeat, p1, p2);

// 로그
Print("[tag] msg");                            // .RPT/script.log
ErrorEx("msg");

// 문자열
s.Length(); s.Substring(start, len); s.IndexOf(" "); s.TrimInPlace();
s.ToInt(); s.Contains("x");

// 수학/벡터
Math.AbsFloat(f); Math.Round(f); Math.Floor(f); Math.Max(a,b);
vector.Distance(a, b);

// config (커스텀 CfgVehicles 파라미터 런타임 읽기)
string out; GetGame().ConfigGetText("CfgVehicles " + GetType() + " myParam", out);
int i = GetGame().ConfigGetInt("CfgVehicles " + GetType() + " myInt");
float fl = GetGame().ConfigGetFloat("CfgVehicles " + GetType() + " myFloat");

// JSON
JsonFileLoader<MyClass>.LoadData(jsonStr, data, err);   // 문자열 파싱, bool 반환
JsonFileLoader<MyClass>.MakeData(data, outStr, err, false); // 직렬화
JsonFileLoader<MyClass>.LoadFile(path, data, err);
JsonFileLoader<MyClass>.SaveFile(path, data, err);

// 엔티티 hook (modded class에서 override)
override void EEInit() { super.EEInit(); ... }   // 스폰 시
```

### CF RPC (Community Framework)
```c
// 등록 (서버/클라)
GetRPCManager().AddRPC("Namespace", "RPC_Name", this, SingleplayerExecutionType.Server);  // 또는 .Client
// 송신
GetRPCManager().SendRPC("Namespace", "RPC_Name", new Param1<string>(x), true, identity); // identity=특정 클라
// 핸들러 시그니처 (ref 파라미터는 무해한 경고 발생)
void RPC_Name(CallType type, ParamsReadContext ctx, PlayerIdentity sender, Object target)
{
    if (type != CallType.Server) return;     // 또는 CallType.Client
    Param1<string> data;
    if (!ctx.Read(data)) return;
    ...
}
```
- CF 모듈 인스턴스 얻기: `MyModule m; CF_Modules<MyModule>.Get(m); if (m) {...}`

---

## 7. GUI (UIScriptedMenu)

```c
class MyMenu extends UIScriptedMenu
{
    override Widget Init()
    {
        layoutRoot = GetGame().GetWorkspace().CreateWidgets("MyMod/GUI/layouts/x.layout");
        // ❌ layoutRoot.SetHandler(this) 하지 마라 — UIScriptedMenu는 ScriptedWidgetEventHandler가 아님.
        //    OnClick/OnChange override는 엔진이 자동 호출.
        return layoutRoot;
    }
    override bool UseMouse() { return true; }
    override bool UseKeyboard() { return true; }
    override void OnShow() { super.OnShow(); GetGame().GetUIManager().ShowUICursor(true); GetGame().GetInput().ChangeGameFocus(1); }
    override void OnHide() { super.OnHide(); GetGame().GetInput().ChangeGameFocus(-1); GetGame().GetUIManager().ShowUICursor(false); }
    override bool OnClick(Widget w, int x, int y, int button) { if (w == m_Btn) {...; return true;} return super.OnClick(w,x,y,button); }
}
```
**메뉴 등록 + 열기:**
```c
// 등록 (modded MissionGameplay)
override UIScriptedMenu CreateScriptedMenu(int id) { if (id == MY_ID) return new MyMenu(); return super.CreateScriptedMenu(id); }
// 열기 (다른 메뉴 핸들러/채팅명령 안에서는 defer!)
GetGame().GetCallQueue(CALL_CATEGORY_GUI).CallLater(GetGame().GetUIManager().EnterScriptedMenu, 250, false, MY_ID, NULL);
```
**레이아웃(.layout):** 버튼 `ButtonWidgetClass ... style Default ... color r g b a`, 텍스트 `TextWidgetClass ... font "gui/fonts/etelkatextpro22" ... "text halign" center`. 위젯 위치는 `position size` + `hexactpos/vexactpos/hexactsize/vexactsize`(0=비율, 1=픽셀).

---

## 8. 모드/패킹 (PBO)

- `config.cpp`의 `requiredAddons[]`에 **modded/상속하는 클래스를 정의한 애드온**을 반드시 넣어라 (예: `JM_CF_Scripts`, `JM_COT_Scripts`, `DayZExpansion_*_Scripts`). 안 그러면 "undefined base class" 또는 로드순서 꼬임.
- `$PREFIX$/$PRODUCT$/$VERSION$` 파일은 **끝에 줄바꿈 절대 금지** → PBO prefix 오염 → 엔진이 "Unable to open". FileBank `-property prefix=Name`으로 패킹하면 깔끔.
- 스크립트 모듈: `3_Game`(범용), `4_World`(엔티티/월드/플레이어), `5_Mission`(미션/GUI/클라UI). 상위가 하위 참조 가능(5→4→3).
- **클라 GUI 변경은 클라 완전 재시작** 필요(reconnect로는 pbo 안 갱신). 클라 로그: `%LOCALAPPDATA%\DayZ\script_*.log`.

---

## 9. 어디서 찾나
- Bohemia 위키: `DayZ:Enforce Script Syntax`, `DayZ:Modding Basics`, `DayZ:Enforce Script API`.
- GitHub: `BohemiaInteractive/DayZ-Samples`.
- 바닐라 원본: P:\scripts (WorkDrive). 모드 원본: BankRev로 언팩.
- **확신 없으면 베껴라**: 위 소스에서 실제 동작하는 idiom을 grep해서 그대로.

---

## 10. 모딩 디자인 패턴 — modded vs extends vs 신규 class 결정

> 전부 이 프로젝트에서 **실제 검증된** 관행. 출처(우리 코드/까본 모드)를 같이 적음.
> 판단 순서: ① 목적(기존 변경 vs 신규 추가) → ② 대상이 살아있나(check_modded) → ③ 누가 어떻게 생성하나(find_usages) → ④ 아래 패턴 매칭.

### 10.1 결정 트리 (modding pattern decision)

```text
기존 바닐라/타모드 동작을 수정·가로채기  -> modded class + override(+super 유지)
새 액션/아이템/오브젝트 추가             -> Base를 extends + "등록 지점"에 등록
유틸/헬퍼/상수/static 함수               -> 일반 class (등록 불필요)
타 모드 GUI/레이아웃 교체                -> modded-redirect (file-shadow 절대 금지)
클라<->서버 통신                         -> CF RPC (바닐라 ScriptRPC 직접 사용 금지)
```

### 10.2 modded class — 기존 동작 수정 (호환성의 핵심)
- `modded`는 클래스를 **재오픈**(대체 아님). 여러 모드가 같은 클래스를 modded해도 **체인으로 전부 공존** — 그래서 기존 동작 수정은 반드시 modded (extends로 새 클래스를 만들면 엔진/바닐라는 그걸 모름).
- 기존 메서드를 건드릴 땐 `override` 명시 + **`super.X()` 호출 유지가 기본값**. super 생략 = 체인 절단 = 바닐라뿐 아니라 Expansion 등 타 모드의 modded까지 끊어버림. 생략은 "완전 대체"가 의도일 때만.
- 새 멤버 이름엔 우리 prefix(`m_Lake~`, `Lake~`) — 타 모드 modded와 멤버명 충돌 방지. (Expansion=`m_Expansion~`, COT=`JM/COT~`, warodu=`warodu~` 전부 같은 관행)
- modded는 private에도 접근 가능 (검증됨, §4 참조).
- 검증 예: `LakeActionBaseRedirect.c`(ActionBase.GetText 가로채기), Expansion의 InGameMenu(super.Init() 후 수정), 우리 메뉴 re-SetText 전부.

### 10.3 엔진/미션이 직접 띄우는 클래스 = modded만 가능
`MissionServer`, `MissionGameplay`, `ChatInputMenu`, 바닐라 메뉴(`OptionsMenu`/`InGameMenu`/`MainMenu`...), `ActionBase` 같은 클래스는 **엔진/미션이 클래스명을 하드코딩으로 인스턴스화** → extends한 새 클래스는 영원히 호출되지 않음 → modded가 유일한 주입점.
- 판별법: find_usages/grep에서 `new X(`·`X.Cast(`가 바닐라 핵심 루프에 있거나, 미션/엔진이 생성 주체면 이 케이스.
- 검증 예: 접속 후킹=`modded MissionServer.InvokeOnConnect`, 채팅 명령=`modded ChatInputMenu`(COT보다 로드 순서 주의).

### 10.4 레지스트리/팩토리 생성 클래스 = extends + 등록 (등록 없으면 죽은 코드)
새 클래스를 만들기만 하면 게임은 모른다. **생성 진입점에 등록해야 산다:**
- **새 액션**: `ActionXxx extends ActionContinuousBase/...` + **`modded class ActionConstructor { override void RegisterActions(TTypenameArray actions) { super.RegisterActions(actions); actions.Insert(ActionXxx); } }`** — Garage/Souls/Teleport/Territories 전부 이 패턴 (super 호출 필수).
- **아이템에 액션 부착**: 해당 ItemBase의 `SetActions()`에서 `AddAction(ActionXxx);` — 바닐라 예: `fishingrod_base.c:11 AddAction(ActionFishingNew);`. 바닐라 아이템에 붙일 땐 `modded class FishingRod_Base { override void SetActions() { super.SetActions(); AddAction(우리액션); } }`.
- **새 아이템**: 스크립트 `class Lake_Xxx extends ItemBase(등)` + **config.cpp CfgVehicles에 같은 클래스명** (config이 스폰 진입점) + types.xml. 예: `Lake_Soul`, `Lake_TerritoryFlag_*`.
- **새 메뉴**: UIScriptedMenu extends + 메뉴ID 배정(우리 31341 같은 고유 ID) + 열기 코드.

### 10.5 타 모드 덮기 = modded-redirect, file-shadow 금지 (사용자 확정 원칙)
같은 prefix로 파일 미러(pbo 섀도잉) = 베이스 모드가 버전업하면 깨지는 프리징 포크 — **금지**. 대신 **로드 지점을 modded로 열고 경로/동작만 우리 걸로 돌린다**:
- Expansion 채팅: `override string GetLayoutFile()` (ExpansionChatUIWindow 등이 일부러 열어둔 지점) → 우리 레이아웃 경로 반환.
- getter 없는 로더: 그 메서드를 modded+override 해서 `CreateWidgets(경로)`만 교체 (warodu가 MissionGameplay.InitExpansionChat에서 하는 방식).
- 원칙(사용자): "모드가 버전업하면 modded 하는 쪽이 맞추는 게 당연. 버전업 걱정으로 file-shadow 짜는 건 더 망가뜨리는 것."

### 10.6 CF RPC — 클라/서버 통신 표준 (충돌 방지)
- 클라↔서버 통신은 **CF(Community Framework, `JM_CF_Scripts`) RPC** 사용 — 모드 간 RPC 충돌 방지 + 핸들러 등록 표준. requiredAddons에 `JM_CF_Scripts` 필수.
- 우리 exemplar(베낄 곳): `LakeProject/Scripts/3_Game/LakeProject/Rpc/LakeProjectRpcRouter.c` (라우터), `LakeProject_Factions/.../LakeProjectFactionRpcRouter.c`, 영토 동기화 `LakeProjectTerritoryRpc`(서버→클라 전체 push: 접속 시 요청 + 변경 시 broadcast — "클라가 안 가봐도 전 영토 표시" 패턴).
- 원칙: 클라이언트는 API/DB 직접 접근 금지 — 모든 영속 데이터는 서버가 RestApi로, 클라는 CF RPC로 서버에게만 말한다.
- DB/영속 브리지 exemplar(MCP로 까볼 곳): `LakeProjectApiClient`(RestApi 클라이언트 — 서버가 외부 API EXE로 HTTP), `LakeProjectStorage`(타입드 storage helper), 요청 DTO `LakeProjectApiStorageGet/Set/Delete/ListRequest`·`LakeProjectApiEventRequest`, 비동기 콜백 `LakeProjectApiCallback`. **흐름: 클라 → CF RPC → 서버 모드 → `LakeProjectApiClient`(RestApi) → API EXE → MySQL.** 콜백 객체는 pending map에 `ref`로 잡아 GC 방지(§5). `class_info`/`find_usages`로 구조 추적 가능.
- CF ≠ COT: CF=`@1559212036`(JM_CF_Scripts, 프레임워크/의존성), COT=`@1564026768`(JM_COT_Scripts, 어드민툴+`/`명령 가로챔). 같은 제작자라 헷갈림 주의.

### 10.7 Base 클래스 blast radius (modded 전 필수 확인)
`~Base`(ItemBase, Weapon_Base, ActionContinuousBase, ActionBandageBase...) 또는 자식이 많은 클래스를 modded하면 **모든 자식에 일괄 적용**.
- 전체 일괄이 목적 → modded가 정답 (예: Lake_Tweaks가 붕대 전체 밸런스에 `modded ActionBandageBase` — 의도적).
- 신규 1개 추가가 목적 → Base는 건드리지 말고 extends + 등록 (10.4).
- class_info가 자식 수를 보여줌 — 자식 수 = 영향 반경.

### 10.8 함정 모음 (이 프로젝트에서 실제로 맞은 것들)
- **주석 deprecated**: BI는 죽은 클래스를 지우지 않고 `/* */`로 남김 (`ActionFishing`→실제는 `ActionFishingNew`). grep에 잡혀도 컴파일엔 없음 → **modded 전 check_modded 필수** (어기면 Unknown type 부팅 사망 — 2026-06-12 실사고).
- **동적 텍스트 키**: 액션 `m_Text`는 `"#harvest" + " " + 대상명`처럼 런타임 합성됨 — 정확매칭 가로채기는 빠짐, 선두 토큰 매칭으로.
- **GetText override 액션 13종**은 ActionBase 중앙 hook을 우회 — 클래스별 처리 필요.
- **vanilla #STR 스트링키는 override 불가** — 새 키(#LAKE_)로 remap.
- **런타임 위젯/레이아웃 스왑 금지** — 렌더링 죽음(2회 검증). 레이아웃은 로드 시점 경로 교체(10.5)로.
- **클라 GUI pbo는 완전 재시작**해야 갱신 (reconnect 무효).
