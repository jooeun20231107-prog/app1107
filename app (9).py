"""부모님을 위한 입시자료 | 실행: streamlit run app.py
GitHub에는 app.py, requirements.txt 두 파일만 올리세요.
Streamlit Community Cloud에서 저장소와 app.py를 선택해 배포합니다.
온라인 저장 설정 및 SQL은 앱의 '설정·QR' 화면에 있습니다.
Secrets는 GitHub에 올리지 마세요. 앱은 합격 예측이나 최신 일정 자동 수집을 하지 않습니다.
"""
import copy
import io
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import qrcode
import requests
import streamlit as st

st.set_page_config(page_title="우리 가족 입시 길잡이", page_icon="🌷", layout="centered")
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()

def fresh():
    return {"version": 1, "profile": {"nickname": "우리 아이", "grade": "고1", "year": TODAY.year + 3},
            "events": [], "checks": {}, "notes": "", "schools": []}

GUIDE = {
    "고1": ["좋아하는 과목과 활동을 자녀와 함께 이야기해요.", "학교 교육과정과 과목 선택 안내를 확인해요.", "담임 선생님과 학교생활 적응·학습 습관을 상담해요.", "희망 분야의 학과와 진로를 넓게 탐색해요."],
    "고2": ["관심 학과의 교육과정과 진로를 비교해요.", "지원 학년도에 해당하는 대학별 전형계획을 확인해요.", "과목 선택과 학습 계획을 학교 선생님과 점검해요.", "모의평가와 학교 성적은 상담 참고 자료로 함께 살펴봐요."],
    "고3": ["지원 대학의 해당 학년도 최종 모집요강을 확인해요.", "전형별 지원 자격·반영 방법·제출 서류를 점검해요.", "원서 마감 시각과 면접·시험 일정을 직접 등록해요.", "합격 발표·등록·충원 안내를 대학 입학처에서 확인해요."]}
TERMS = {
    "수시": "정시와 구분되는 모집 시기예요. 학생부·논술·실기 등 전형마다 평가 요소가 다릅니다. 수시가 곧 학생부 전형이라는 뜻은 아니에요.",
    "정시": "수시와 구분되는 모집 시기예요. 수능 중심 전형이 많지만 대학과 모집단위에 따라 다른 요소를 반영할 수 있어요.",
    "학생부교과": "학교생활기록부의 교과 성적을 중심으로 평가하는 전형이에요. 반영 과목·산식·면접 여부 등을 대학별로 확인하세요.",
    "학생부종합": "학교생활기록부 등 제출 자료를 바탕으로 학생의 역량을 종합적으로 평가해요. 평가 요소와 면접 여부는 대학별로 다릅니다.",
    "수능 최저학력기준": "일부 전형에서 요구하는 수능 성적 기준이에요. 적용 여부와 영역·등급 조합을 모집요강에서 확인하세요.",
    "모집요강": "지원 자격, 평가 방법, 서류, 일정 등 지원에 필요한 세부 사항을 담은 안내예요. 해당 학년도와 변경 공지를 함께 확인하세요.",
    "충원 합격": "미등록 등으로 생긴 결원을 추가 합격자로 채우는 절차예요. 발표 방식·연락 방법·등록 기한을 대학별로 확인하세요.",
    "전형계획과 모집요강": "전형계획은 미리 공개하는 큰 틀의 안내이고 모집요강은 지원 시 확인할 세부 안내예요. 이후 정정·변경 공지도 확인해야 해요."}
SQL = '''-- Supabase 프로젝트의 SQL Editor에서 실행
create table if not exists public.family_admissions (
  user_id uuid primary key references auth.users(id) on delete cascade,
  payload jsonb not null,
  updated_at timestamptz not null default now()
);
alter table public.family_admissions enable row level security;
revoke all on public.family_admissions from anon;
grant select, insert, update, delete on public.family_admissions to authenticated;
drop policy if exists own_family_data on public.family_admissions;
create policy own_family_data on public.family_admissions
  for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
'''

def secret(key):
    try:
        return str(st.secrets.get(key, "")).strip()
    except Exception:
        return ""

URL, KEY = secret("SUPABASE_URL").rstrip("/"), secret("SUPABASE_ANON_KEY")
CLOUD = bool(URL and KEY)
if "data" not in st.session_state:
    st.session_state.data = fresh()
    st.session_state.dirty = False
    st.session_state.auth = None
    st.session_state.remote_stamp = None
    st.session_state.loaded = False
    st.session_state.rev = 0

def api(method, path, token=None, body=None, prefer=None):
    headers = {"apikey": KEY, "Authorization": "Bearer " + (token or KEY)}
    if prefer:
        headers["Prefer"] = prefer
    r = requests.request(method, URL + path, headers=headers, json=body, timeout=20)
    if not r.ok:
        # Do not display server response bodies that could contain sensitive data.
        raise RuntimeError(f"연결 요청에 실패했어요 (HTTP {r.status_code}). 로그인·이메일 인증·저장소 설정을 확인하세요.")
    return r.json() if r.content else None

def token():
    a = st.session_state.auth
    if not a:
        raise RuntimeError("먼저 로그인해 주세요.")
    if a.get("expires_at", 0) < datetime.now().timestamp() + 60:
        a = api("POST", "/auth/v1/token?grant_type=refresh_token", body={"refresh_token": a["refresh_token"]})
        st.session_state.auth = a
    return a["access_token"]

def remote():
    t = token()
    uid = st.session_state.auth["user"]["id"]
    rows = api("GET", f"/rest/v1/family_admissions?user_id=eq.{uid}&select=payload,updated_at", t)
    return rows[0] if rows else None

def validate(raw):
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("지원하지 않는 백업 형식입니다.")
    result = fresh()
    p = raw.get("profile", {})
    if not isinstance(p, dict) or p.get("grade") not in GUIDE:
        raise ValueError("자녀 정보가 올바르지 않습니다.")
    result["profile"] = {"nickname": str(p.get("nickname", "우리 아이"))[:40], "grade": p["grade"], "year": int(p.get("year", TODAY.year + 1))}
    if not 2000 <= result["profile"]["year"] <= 2100:
        raise ValueError("지원 학년도를 확인해 주세요.")
    checks = raw.get("checks", {})
    if not isinstance(checks, dict) or len(checks) > 100:
        raise ValueError("체크리스트 형식 오류")
    result["checks"] = {str(k)[:100]: bool(v) for k, v in checks.items()}
    result["notes"] = str(raw.get("notes", ""))[:20000]
    for field in ("events", "schools"):
        items = raw.get(field, [])
        if not isinstance(items, list) or len(items) > 500:
            raise ValueError("항목은 종류별 최대 500개까지 지원해요.")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("항목 형식 오류")
            if field == "events":
                date.fromisoformat(item["date"])
                clean = {"id": str(uuid.uuid4()), "title": str(item["title"])[:100], "date": item["date"],
                         "time": str(item.get("time", ""))[:20], "memo": str(item.get("memo", ""))[:2000],
                         "done": bool(item.get("done", False))}
            else:
                clean = {"id": str(uuid.uuid4()), "name": str(item["name"])[:100], "major": str(item.get("major", ""))[:100],
                         "track": str(item.get("track", ""))[:100], "url": str(item.get("url", ""))[:1000], "memo": str(item.get("memo", ""))[:2000]}
            result[field].append(clean)
    return result

def changed():
    st.session_state.dirty = True

def replace_data(data):
    st.session_state.data = data
    st.session_state.rev += 1

def safe_link(url):
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)

st.markdown('''<style>
html, body, [class*="css"] {font-size:18px;}
.stApp {background:#faf9f6;}
h1,h2,h3 {color:#245b4c;}
.stButton button,.stDownloadButton button {min-height:48px;border-radius:12px;}
[data-testid="stMetric"] {background:#edf3ee;padding:14px;border-radius:16px;}
</style>''', unsafe_allow_html=True)
st.title("🌷 우리 가족 입시 길잡이")
st.caption("정보는 쉽게, 준비는 차근차근 · 부모님과 자녀가 함께 보는 입시 노트")

with st.expander("🔐 가족 계정 · 로그인 / 회원가입", expanded=False):
    st.caption("온라인 저장은 같은 가족 계정으로 로그인한 기기에서 함께 이용합니다. 개별 가족 구성원 초대 기능은 없습니다.")
    if not CLOUD:
        st.info("지금은 임시 이용 중이에요. 설정·QR에서 온라인 저장소를 연결할 수 있어요.")
    elif st.session_state.auth:
        st.success("가족 계정에 로그인되어 있어요.")
        if st.button("로그아웃 (미저장 변경은 먼저 백업하세요)"):
            try:
                api("POST", "/auth/v1/logout", token())
            except Exception:
                pass
            st.session_state.auth = None
            replace_data(fresh())
            st.session_state.loaded = False
            st.session_state.dirty = False
            st.session_state.remote_stamp = None
            st.rerun()
    else:
        with st.form("login"):
            email = st.text_input("가족 계정 이메일", max_chars=200)
            password = st.text_input("비밀번호", type="password", max_chars=200)
            action = st.radio("계정 작업", ["로그인", "회원가입"], horizontal=True)
            submit = st.form_submit_button("계속하기")
        if submit:
            if "@" not in email or len(password) < 8:
                st.error("이메일과 8자 이상의 비밀번호를 입력해 주세요.")
            else:
                try:
                    if action == "회원가입":
                        api("POST", "/auth/v1/signup", body={"email": email.strip(), "password": password})
                        st.success("가입을 요청했어요. 인증 메일이 오면 인증 후 로그인해 주세요. 이미 가입된 경우 로그인을 시도하세요.")
                    else:
                        st.session_state.auth = api("POST", "/auth/v1/token?grant_type=password", body={"email": email.strip(), "password": password})
                        st.session_state.loaded = False
                        st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        st.caption("비밀번호 재설정은 이 첫 버전에서 제공하지 않아요. 운영자는 Supabase 인증 관리에서 지원할 수 있습니다.")

# Preserve anonymous edits. Remote loading is explicit so local work is never silently replaced.
if st.session_state.auth and not st.session_state.loaded:
    st.info("로그인했어요. 아래에서 온라인 자료를 불러오거나, 현재 자료를 온라인에 저장할 수 있어요.")

if st.session_state.get("flash"):
    st.success(st.session_state.pop("flash"))
D = st.session_state.data
rev = st.session_state.rev
st.caption("🟠 저장하지 않은 변경이 있어요" if st.session_state.dirty else "아래에서 온라인 저장 또는 백업 파일 다운로드를 이용하세요.")
if not st.session_state.auth:
    st.warning("임시 모드: 새로고침·접속 종료 시 입력 자료가 사라질 수 있어요. 꼭 백업 파일을 내려받으세요.")
page = st.radio("메뉴", ["🏠 오늘", "📖 길잡이", "🗓 일정", "🎓 관심 대학", "💾 저장·백업", "⚙️ 설정·QR"], horizontal=True, label_visibility="collapsed")

if page == "🏠 오늘":
    p = D["profile"]
    st.subheader(f"{p['nickname']}의 {p['grade']} 준비 노트")
    st.write(f"지원 학년도: **{p['year']}학년도** · 오늘: {TODAY:%Y.%m.%d}")
    pending = sorted([e for e in D["events"] if not e["done"]], key=lambda x:(x["date"],x["time"]))
    soon = [e for e in pending if 0 <= (date.fromisoformat(e["date"])-TODAY).days <= 7]
    a,b = st.columns(2)
    a.metric("앞으로 7일 일정", f"{len(soon)}개")
    b.metric("미완료 일정", f"{len(pending)}개")
    st.markdown("### 가장 가까운 준비")
    if not pending:
        st.info("일정 메뉴에서 학교 상담이나 확인한 원서 마감일을 등록해 보세요.")
    for e in pending[:5]:
        days = (date.fromisoformat(e["date"])-TODAY).days
        label = "오늘" if days == 0 else (f"D-{days}" if days > 0 else f"{abs(days)}일 지남")
        st.write(f"**{label} · {e['title']}**")
        st.caption(f"{e['date']} {e['time']}  |  {e['memo']}")
    st.info("💚 오늘은 ‘어느 대학에 갈까?’보다 ‘요즘 어떤 과목이 재미있니?’를 먼저 물어봐 주세요.")
    with st.form(f"notes_{rev}"):
        notes = st.text_area("가족 대화 메모 (민감한 개인정보는 적지 마세요)", D["notes"], max_chars=20000)
        if st.form_submit_button("메모 반영"):
            D["notes"] = notes
            changed()
            st.rerun()

elif page == "📖 길잡이":
    st.subheader("어려운 입시, 쉬운 말로")
    st.warning("기본 개념 안내입니다. 지원 제한·자격·평가 방법·일정은 지원 학년도와 대학에 따라 달라요. 반드시 해당 대학 입학처의 최신 모집요강·변경 공지를 확인하세요.")
    word = st.text_input("궁금한 용어 찾기", placeholder="예: 수능 최저, 학생부")
    for name, text in TERMS.items():
        if word.strip() in name or word.strip() in text:
            with st.expander(name):
                st.write(text)
    st.markdown(f"### {D['profile']['grade']} 부모님 체크리스트")
    grade = D["profile"]["grade"]
    for i, text in enumerate(GUIDE[grade]):
        key = f"{grade}_{i}"
        value = st.checkbox(text, value=D["checks"].get(key, False), key=f"check_{key}_{rev}")
        if value != D["checks"].get(key, False):
            D["checks"][key] = value
            changed()
            st.rerun()
    st.markdown("### 믿을 수 있는 정보부터")
    st.link_button("대입정보포털 어디가 열기 ↗", "https://www.adiga.kr/")
    st.link_button("대교협의 어디가 서비스 안내 ↗", "https://www.kcue.or.kr/work/sub06/sub05.php")
    st.caption("어디가에서 대학·학과·전형을 탐색하고, 최종 지원 전에는 대학 입학처 자료를 확인하세요. 최신 자료 자동 업데이트·합격 가능성 계산 기능은 없습니다.")
    st.markdown("**학교 상담 때 물어볼 질문**\n- 아이의 관심 분야와 강점은 무엇인가요?\n- 과목 선택과 학습 계획에서 무엇을 보완하면 좋을까요?\n- 희망 전형의 자격·평가 방법에서 확인할 점은 무엇인가요?\n- 부모가 도와주되 아이가 직접 결정할 부분은 무엇인가요?")

elif page == "🗓 일정":
    st.subheader("한눈에 보는 가족 일정")
    st.caption("날짜·시각은 한국 시간 기준으로 직접 입력해요. 앱을 열었을 때만 확인하며 문자·푸시 알림은 발송하지 않아요.")
    with st.expander("＋ 일정 추가", expanded=True):
        with st.form("new_event", clear_on_submit=True):
            title = st.text_input("일정 이름", max_chars=100, placeholder="예: ○○대 모집요강 확인")
            dt = st.date_input("날짜", value=TODAY, min_value=date(2000,1,1), max_value=date(2100,12,31))
            tm = st.text_input("시각 (선택)", placeholder="예: 17:00 마감", max_chars=20)
            memo = st.text_area("준비물·확인한 출처·메모", max_chars=2000)
            if st.form_submit_button("일정 추가"):
                if not title.strip():
                    st.error("일정 이름을 입력해 주세요.")
                elif len(D["events"]) >= 500:
                    st.error("일정은 최대 500개까지 저장할 수 있어요.")
                else:
                    D["events"].append({"id":str(uuid.uuid4()),"title":title.strip(),"date":dt.isoformat(),"time":tm,"memo":memo,"done":False})
                    changed()
                    st.rerun()
    filt = st.selectbox("보기", ["미완료", "전체", "완료"])
    for e in sorted(D["events"], key=lambda x:(x["date"],x["time"])):
        if (filt == "미완료" and e["done"]) or (filt == "완료" and not e["done"]):
            continue
        with st.expander(f"{'✅' if e['done'] else '📌'} {e['date']} · {e['title']}"):
            with st.form("edit_" + e["id"]):
                nt = st.text_input("일정 이름", e["title"], max_chars=100)
                nd = st.date_input("날짜", date.fromisoformat(e["date"]), min_value=date(2000,1,1), max_value=date(2100,12,31))
                ntime = st.text_input("시각", e["time"], max_chars=20)
                nm = st.text_area("메모", e["memo"], max_chars=2000)
                done = st.checkbox("완료했어요", value=e["done"])
                if st.form_submit_button("수정 반영"):
                    if nt.strip():
                        e.update(title=nt.strip(),date=nd.isoformat(),time=ntime,memo=nm,done=done)
                        changed()
                        st.rerun()
                    else:
                        st.error("이름을 입력하세요.")
            confirm = st.checkbox("이 일정을 삭제할게요", key="delok_"+e["id"])
            if st.button("삭제", key="del_"+e["id"], disabled=not confirm):
                D["events"].remove(e)
                changed()
                st.rerun()

elif page == "🎓 관심 대학":
    st.subheader("우리 가족 관심 대학 노트")
    st.caption("대학을 평가·추천하는 기능이 아니라 직접 확인한 정보를 모으는 노트예요. 잘못된 정보는 삭제 후 다시 등록하세요.")
    with st.form("school", clear_on_submit=True):
        name = st.text_input("대학 이름", max_chars=100)
        major = st.text_input("관심 학과", max_chars=100)
        track = st.text_input("관심 전형", max_chars=100)
        url = st.text_input("입학처·모집요강 링크 (선택)", max_chars=1000)
        memo = st.text_area("확인한 학년도·확인 날짜·궁금한 점", max_chars=2000)
        if st.form_submit_button("관심 대학 추가"):
            if not name.strip():
                st.error("대학 이름을 입력하세요.")
            elif url and not safe_link(url):
                st.error("http:// 또는 https://로 시작하는 주소를 입력하세요.")
            elif len(D["schools"]) >= 500:
                st.error("최대 500개까지 저장할 수 있어요.")
            else:
                D["schools"].append({"id":str(uuid.uuid4()),"name":name.strip(),"major":major,"track":track,"url":url,"memo":memo})
                changed()
                st.rerun()
    for s in D["schools"]:
        with st.expander(s["name"] + " · " + s["major"]):
            st.write("관심 전형: " + s["track"])
            st.write(s["memo"])
            if safe_link(s["url"]):
                st.link_button("등록한 공식 자료 확인 ↗", s["url"])
            ok = st.checkbox("이 대학 노트를 삭제할게요", key="sdok_"+s["id"])
            if st.button("대학 노트 삭제", key="sd_"+s["id"], disabled=not ok):
                D["schools"].remove(s)
                changed()
                st.rerun()

elif page == "💾 저장·백업":
    st.subheader("자료를 안전하게 보관해요")
    st.info("화면에서 ‘반영’한 내용은 현재 접속에만 남아요. 온라인 저장 또는 백업 파일 다운로드를 별도로 해 주세요.")
    st.markdown("### 1. 온라인 저장·불러오기")
    if not st.session_state.auth:
        st.write("온라인 저장소 연결 후 위의 가족 계정 메뉴에서 로그인해 주세요.")
    else:
        st.caption("다른 기기의 변경을 자동 병합하지 않아요. 작업 전 불러오고 작업 후 저장하세요. 충돌 시 백업 후 최신 자료를 불러와 다시 수정해 주세요.")
        allow = st.checkbox("불러오면 현재 화면의 자료가 바뀌는 것을 확인했어요")
        if st.button("온라인 자료 불러오기", disabled=not allow):
            try:
                row = remote()
                if row:
                    replace_data(validate(row["payload"]))
                    st.session_state.remote_stamp = row["updated_at"]
                    st.session_state.dirty = False
                    st.session_state.loaded = True
                    st.session_state.flash = "온라인 자료를 불러왔어요."
                    st.rerun()
                else:
                    st.session_state.loaded = True
                    st.session_state.remote_stamp = None
                    st.info("온라인 자료가 없어요. 현재 자료를 처음 저장해 주세요.")
            except Exception as exc:
                st.error(str(exc))
        if st.button("현재 자료 온라인에 저장", type="primary"):
            try:
                row = remote()
                expected = st.session_state.remote_stamp
                if row and (not st.session_state.loaded or row["updated_at"] != expected):
                    raise RuntimeError("온라인에 새 자료가 있어요. 현재 자료를 파일로 백업한 뒤 온라인 자료를 먼저 불러오세요.")
                t = token()
                uid = st.session_state.auth["user"]["id"]
                stamp = datetime.now(timezone.utc).isoformat()
                body = {"payload": D, "updated_at": stamp}
                if row:
                    # Atomic conditional update prevents lost writes between devices.
                    path = f"/rest/v1/family_admissions?user_id=eq.{uid}&updated_at=eq.{requests.utils.quote(expected, safe='')}"
                    saved = api("PATCH", path, t, body, "return=representation")
                    if not saved:
                        raise RuntimeError("다른 기기가 먼저 저장했어요. 백업 후 다시 불러와 주세요.")
                else:
                    body["user_id"] = uid
                    saved = api("POST", "/rest/v1/family_admissions", t, body, "return=representation")
                st.session_state.remote_stamp = saved[0]["updated_at"]
                st.session_state.loaded = True
                st.session_state.dirty = False
                st.session_state.flash = "온라인 저장을 완료했어요."
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    st.markdown("### 2. 파일 백업 (온라인 저장과 함께 사용 가능)")
    backup = json.dumps(D, ensure_ascii=False, indent=2).encode("utf-8")
    st.download_button("📥 현재 자료 백업 파일 다운로드", backup, file_name=f"입시노트_{TODAY.isoformat()}.json", mime="application/json")
    st.caption("백업 파일은 암호화되지 않아요. 가족 외에는 공유하지 마세요.")
    upload = st.file_uploader("예전에 저장한 JSON 백업 파일", type=["json"])
    overwrite = st.checkbox("백업을 불러오면 현재 화면 자료를 교체하는 것에 동의해요")
    if st.button("백업 파일 불러오기", disabled=not (upload and overwrite)):
        try:
            if upload.size > 2_000_000:
                raise ValueError("백업 파일은 2MB 이하만 지원해요.")
            data = validate(json.loads(upload.getvalue().decode("utf-8-sig")))
            replace_data(data)
            changed()
            st.session_state.flash = "백업을 불러왔어요. 온라인에 보관하려면 별도로 저장하세요."
            st.rerun()
        except Exception:
            st.error("백업 파일 형식·크기를 확인해 주세요. 기존 자료는 변경하지 않았어요.")
    with st.expander("온라인 자료 삭제"):
        st.caption("계정은 유지되고 온라인 노트만 삭제됩니다. 다른 기기에 남은 자료는 별도로 지워야 해요.")
        delete_ok = st.checkbox("복구할 수 없음을 이해했고 필요하면 백업했어요")
        if st.button("온라인 노트 영구 삭제", disabled=not (delete_ok and st.session_state.auth)):
            try:
                uid = st.session_state.auth["user"]["id"]
                api("DELETE", f"/rest/v1/family_admissions?user_id=eq.{uid}", token())
                st.session_state.remote_stamp = None
                st.session_state.loaded = True
                st.success("온라인 노트만 삭제했어요. 현재 화면 자료를 다시 저장하면 재생성됩니다.")
            except Exception as exc:
                st.error(str(exc))

elif page == "⚙️ 설정·QR":
    st.subheader("자녀 정보")
    with st.form(f"profile_{rev}"):
        nickname = st.text_input("자녀 별칭 (실명 불필요)", D["profile"]["nickname"], max_chars=40)
        grade = st.selectbox("학년", list(GUIDE), index=list(GUIDE).index(D["profile"]["grade"]))
        year = st.number_input("지원 학년도 (직접 확인해 주세요)", min_value=2000, max_value=2100, value=D["profile"]["year"])
        st.caption("예: 지원하려는 모집요강 제목이 ‘2028학년도’라면 2028을 입력해요. 학년만으로 자동 확정하지 않습니다.")
        if st.form_submit_button("자녀 정보 반영"):
            D["profile"] = {"nickname":nickname.strip() or "우리 아이", "grade":grade, "year":int(year)}
            changed()
            st.rerun()
    st.markdown("### 📱 QR로 앱 열기")
    st.caption("QR은 앱 주소를 여는 바로가기예요. 로그인이나 개인정보는 QR에 넣지 않습니다. 먼저 웹에 배포해야 다른 휴대폰에서도 열립니다.")
    app_url = st.text_input("배포한 앱의 공개 HTTPS 주소", value=secret("APP_URL"), placeholder="https://your-app.streamlit.app", max_chars=1000)
    parsed = urlparse(app_url)
    if app_url:
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment or parsed.username or parsed.password:
            st.warning("로그인 토큰·쿼리·비밀번호가 없는 공개 HTTPS 기본 주소를 입력하세요.")
        else:
            buf = io.BytesIO()
            qrcode.make(app_url).save(buf, format="PNG")
            st.image(buf.getvalue(), width=240, caption="휴대폰 카메라로 비춰 접속하세요")
            st.download_button("QR 이미지 다운로드", buf.getvalue(), "입시길잡이_QR.png", "image/png")
    with st.expander("처음 설치하는 분을 위한 GitHub · 배포 안내"):
        st.markdown('''1. GitHub 저장소를 만들고 **app.py**와 **requirements.txt**를 올립니다.
2. Streamlit Community Cloud에서 GitHub 저장소를 연결하고 실행 파일로 **app.py**를 선택해 배포합니다.
3. 배포가 끝나면 공개 앱 주소를 위 QR 입력란에 넣습니다.
4. 파일 백업만 이용한다면 여기까지 해도 됩니다.
5. 온라인 저장도 쓰려면 아래 Supabase 설정을 진행합니다.

로컬 실행 명령:''')
        st.code("pip install -r requirements.txt\nstreamlit run app.py", language="bash")
    with st.expander("온라인 저장소 연결 안내 (운영자 최초 1회)"):
        st.markdown('''**Supabase 무료/유료 제공 범위는 가입 시 직접 확인하세요.**

1. Supabase 프로젝트를 만들고 이메일·비밀번호 인증을 활성화합니다.
2. 이메일 인증을 사용할 경우 인증 설정의 Site URL에 배포 앱 주소를 설정합니다.
3. SQL Editor에서 아래 SQL을 실행합니다. RLS 정책이 있어야 계정별 자료가 분리됩니다.
4. 프로젝트 URL과 **anon 공개 키**를 Streamlit 배포 설정의 Secrets에 등록합니다.
   **service_role / secret 키는 절대 사용하지 마세요.** 인증 토큰과 비밀번호를 코드나 GitHub에 넣지 마세요.
5. 앱을 재시작하고 가족 계정을 가입·이메일 인증한 뒤 로그인합니다.
6. 다른 가족 계정으로 로그인했을 때 서로의 자료가 보이지 않는지 확인한 뒤 이용하세요.

이 버전은 **가족별 한 계정**을 여러 기기에서 사용하는 방식입니다. 기기별 자동 동기화나 개인별 초대·권한 분리는 지원하지 않습니다. 공용 기기에서는 꼭 로그아웃하세요.''')
        st.code(SQL, language="sql")
        st.code('SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"\nSUPABASE_ANON_KEY = "YOUR_ANON_PUBLIC_KEY"\nAPP_URL = "https://YOUR_APP.streamlit.app"', language="toml")
        st.caption("앱 코드에 위 값을 직접 넣지 말고 배포 서비스의 Secrets에만 등록하세요. 연결 오류 시 프로젝트 상태·키·SQL·이메일 인증 여부를 확인하세요.")
    st.markdown("### 개인정보 안내")
    st.write("주민등록번호, 수험번호, 성적표 원본, 주소 등 민감한 자료는 입력하지 마세요. 온라인 노트는 연결한 Supabase에 저장되며 운영자는 저장소를 관리할 수 있습니다. 공개 서비스로 확대할 때는 개인정보 안내·보유기간·계정 삭제 절차를 별도로 마련하세요.")

st.divider()
st.caption("🌱 부모님은 함께 준비하는 동반자입니다. 최종 지원 판단은 자녀·학교 선생님과 상의하세요. | 비공식 개인용 입시 준비 노트")
