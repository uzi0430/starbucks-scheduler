import streamlit as st
import pandas as pd
import datetime
import os
import calendar
import io
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from ortools.sat.python import cp_model

st.set_page_config(layout="wide", page_title="스타벅스 통합 스마트 스케줄러")

# ==========================================
# ☁️ 구글 스프레드시트 (비밀 금고 연동)
# ==========================================
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "인하대점_스케줄DB"
MASTER_ID = "MASTER777" 

@st.cache_resource
def init_connection():
    try:
        # 스트림릿 비밀 금고에서 안전하게 열쇠를 꺼냅니다!
        key_dict = json.loads(st.secrets["GOOGLE_KEY"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, SCOPE)
        return gspread.authorize(creds)
    except Exception as e:
        # 로컬 테스트용 폴백
        if os.path.exists("google_key.json"):
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_key.json", SCOPE)
            return gspread.authorize(creds)
        st.error(f"비밀 금고 열쇠 오류: {e}")
        return None

client = init_connection()

# 🌐 통합 DB 읽기 함수
def load_all_data(worksheet_name, default_cols):
    if client:
        try:
            sheet = client.open(SHEET_NAME).worksheet(worksheet_name)
            records = sheet.get_all_records()
            if records:
                df = pd.DataFrame(records)
                for col in default_cols:
                    if col not in df.columns: df[col] = ""
                return df
        except: pass
    return pd.DataFrame(columns=default_cols)

# 🌐 통합 DB 쓰기 함수
def save_all_data(worksheet_name, df):
    if client:
        try:
            sheet = client.open(SHEET_NAME).worksheet(worksheet_name)
            sheet.clear()
            sheet.update(range_name="A1", values=[df.columns.values.tolist()] + df.values.tolist())
        except Exception as e:
            st.error(f"🚨 DB 저장 오류: {e}")

# 🏢 특정 매장 데이터만 읽기
def load_store_data(worksheet_name, store_name, default_cols):
    df_all = load_all_data(worksheet_name, default_cols)
    if not df_all.empty and '매장명' in df_all.columns:
        return df_all[df_all['매장명'] == str(store_name)].copy()
    return pd.DataFrame(columns=default_cols)

# 🏢 특정 매장 데이터만 저장
def save_store_data(worksheet_name, store_name, df_store):
    df_all = load_all_data(worksheet_name, list(df_store.columns) + (['매장명'] if '매장명' not in df_store.columns else []))
    if not df_all.empty and '매장명' in df_all.columns:
        df_all = df_all[df_all['매장명'] != str(store_name)]
    
    df_store['매장명'] = str(store_name)
    df_final = pd.concat([df_all, df_store], ignore_index=True)
    save_all_data(worksheet_name, df_final)

# ==========================================
# 🔒 1. 지능형 로그인 화면 (사번 라우팅)
# ==========================================
def login_page():
    st.title("☕ 스타벅스 통합 스마트 스케줄러")
    st.write("사번을 입력하시면 소속 매장으로 자동 안내됩니다.")
    st.divider()
    
    col1, col2 = st.columns([1, 1])
    with col1:
        emp_id = st.text_input("👤 사번 입력 (숫자 6자리)", placeholder="예: 123456")
        if st.button("로그인", type="primary", use_container_width=True):
            if emp_id == MASTER_ID:
                st.session_state['master_mode'] = True
                st.rerun()
            elif emp_id:
                st.session_state['show_register'] = None
                df_auth = load_all_data("auth_data", ["매장명", "사번", "이름", "상태"])
                df_auth['사번'] = df_auth['사번'].astype(str)
                user_row = df_auth[df_auth['사번'] == str(emp_id)]
                
                if not user_row.empty:
                    status = user_row.iloc[0]['상태']
                    store_name = user_row.iloc[0]['매장명']
                    user_name = user_row.iloc[0]['이름']
                    
                    if status == 'Approved':
                        st.session_state['logged_in'] = True
                        st.session_state['emp_id'] = emp_id
                        st.session_state['user_name'] = user_name
                        st.session_state['store_name'] = store_name
                        st.success(f"{store_name} {user_name} 파트너님 환영합니다!")
                        st.rerun()
                    else:
                        st.warning(f"⏳ 최고 관리자의 승인을 대기 중입니다. (소속: {store_name})")
                else:
                    st.session_state['show_register'] = emp_id
                    st.rerun()
            else:
                st.warning("사번을 입력해주세요.")

    if st.session_state.get('show_register'):
        st.divider()
        st.info("⚠️ 등록되지 않은 사번입니다. 최초 1회 소속 매장 등록 및 승인 요청이 필요합니다.")
        reg_id = st.session_state['show_register']
        reg_name = st.text_input("이름 (예: 홍길동)")
        reg_store = st.text_input("소속 매장명 (예: 인하대점)")
        
        if st.button("승인 요청하기"):
            if reg_name and reg_store:
                df_auth = load_all_data("auth_data", ["매장명", "사번", "이름", "상태"])
                new_row = pd.DataFrame([{'매장명': reg_store, '사번': str(reg_id), '이름': reg_name, '상태': 'Pending'}])
                df_auth = pd.concat([df_auth, new_row], ignore_index=True)
                save_all_data("auth_data", df_auth)
                st.success("✅ 최고 관리자에게 승인 요청이 전송되었습니다! 승인 후 접속 가능합니다.")
                st.session_state['show_register'] = None
            else:
                st.error("이름과 매장명을 모두 입력해주세요.")

# ==========================================
# 👑 2. 최고 관리자 (Master) 관제탑
# ==========================================
def master_dashboard():
    col1, col2 = st.columns([8, 2])
    col1.title("👑 [최고 관리자] 전국 매장 관제탑")
    with col2:
        if st.button("로그아웃", use_container_width=True):
            st.session_state['master_mode'] = False
            st.rerun()
            
    st.write("전국 스타벅스 매장의 모든 사용자를 통제하고 승인할 수 있는 마스터 권한입니다.")
    st.divider()
    
    df = load_all_data("auth_data", ["매장명", "사번", "이름", "상태"])
    
    if df.empty:
        st.info("현재 등록되거나 대기 중인 사용자가 없습니다.")
        return
        
    st.subheader("👥 전국 사용자 관리 내역")
    for index, row in df.iterrows():
        col_m, col_n, col_s, col_b1, col_b2 = st.columns([2, 2, 2, 2, 2])
        col_m.write(f"🏢 **{row['매장명']}**")
        col_n.write(f"👤 {row['이름']} ({row['사번']})")
        
        if row['상태'] == 'Pending':
            col_s.warning("승인 대기")
            if col_b1.button("✅ 접속 허락", key=f"app_{row['사번']}_{index}", type="primary"):
                df.at[index, '상태'] = 'Approved'
                save_all_data("auth_data", df)
                st.rerun()
            if col_b2.button("🗑️ 거절(삭제)", key=f"rej_{row['사번']}_{index}"):
                df = df.drop(index)
                save_all_data("auth_data", df)
                st.rerun()
        else:
            col_s.success("사용 중")
            if col_b1.button("🚫 강제 차단", key=f"rev_{row['사번']}_{index}"):
                df.at[index, '상태'] = 'Pending'
                save_all_data("auth_data", df)
                st.rerun()

# ==========================================
# ☕ 3. 메인 스케줄러 (매장 전용 화면)
# ==========================================
def parse_time(time_str, default_time):
    try: return datetime.datetime.strptime(str(time_str), "%H:%M").time()
    except:
        try: return datetime.datetime.strptime(str(time_str), "%H:%M:%S").time()
        except: return default_time

def parse_int(val, default_val):
    try: return int(val)
    except: return default_val

def color_schedule(val):
    if val == '오픈': return 'background-color: #b8e994; color: black;'
    elif val == '오픈(관리자)': return 'background-color: #00704A; color: white; font-weight: bold;'
    elif val in ['미들', '미들(관리자)']: return 'background-color: #f6e58d; color: black;'
    elif val == '마감': return 'background-color: #7ed6df; color: black;'
    elif val == '마감(관리자)': return 'background-color: #22a6b3; color: white; font-weight: bold;'
    return ''

def get_role_times(role):
    if role in ["점장", "부점장"]: return 8, 1.0, 9.0  
    elif role == "수퍼바이저": return 7, 0.5, 7.5  
    else: return 5, 0.5, 5.5  

def main_scheduler_app():
    store_name = st.session_state['store_name']
    user_name = st.session_state['user_name']
    
    col_title, col_user = st.columns([8, 2])
    col_title.title(f"☕ {store_name} 스케줄러")
    with col_user:
        st.info(f"👤 {user_name} 파트너님")
        if st.button("로그아웃"):
            st.session_state['logged_in'] = False
            st.rerun()
                
    if not client:
        st.error("🚨 구글 DB 연결 실패! 스트림릿 Secrets(비밀 금고)에 GOOGLE_KEY가 잘 설정되었는지 확인해주세요.")
        st.stop()
        
    if 'store_settings' not in st.session_state:
        df_settings = load_store_data("settings", store_name, ["매장명", "항목", "값"])
        st.session_state.store_settings = dict(zip(df_settings['항목'], df_settings['값'])) if not df_settings.empty else {}
    
    s_dict = st.session_state.store_settings
    
    def_open = parse_time(s_dict.get("open_time"), datetime.time(7, 0))
    def_close = parse_time(s_dict.get("close_time"), datetime.time(22, 0))
    def_peak_start = parse_time(s_dict.get("peak_start"), datetime.time(12, 0))
    def_peak_end = parse_time(s_dict.get("peak_end"), datetime.time(14, 30))
    def_req_open = parse_int(s_dict.get("req_open"), 2)
    def_req_close = parse_int(s_dict.get("req_close"), 2)
    def_min_floor = parse_int(s_dict.get("min_floor"), 2)
    def_peak_staff = parse_int(s_dict.get("peak_staff"), 4)
    def_first_mid = str(s_dict.get("first_mid_start_str", "08:30"))
    def_last_mid = str(s_dict.get("last_mid_end_str", "21:00"))

    st.header("1. 매장 기본 설정")
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        date_range = st.date_input("🗓️ 스케줄 기간", value=(datetime.date.today(), datetime.date.today() + datetime.timedelta(days=6)))
        if len(date_range) != 2: st.stop()
        start_date, end_date = date_range
        num_days = (end_date - start_date).days + 1

    with col_d2:
        target_wh = st.number_input("💰 설정 기간 내 목표 총 워킹(WH) 한도", value=num_days * 45, step=10)

    col1, col2, col3, col4 = st.columns(4)
    with col1: open_time = st.time_input("오픈 시간", def_open)
    with col2: close_time = st.time_input("마감 시간", def_close)
    with col3: peak_start = st.time_input("🔥 피크 시작", def_peak_start)
    with col4: peak_end = st.time_input("🔥 피크 종료", def_peak_end)

    dt_open = datetime.datetime.combine(start_date, open_time)
    dt_close = datetime.datetime.combine(start_date, close_time)
    dt_peak_start = datetime.datetime.combine(start_date, peak_start)
    dt_peak_end = datetime.datetime.combine(start_date, peak_end)
    
    total_minutes = int((dt_close - dt_open).total_seconds() / 60)
    num_slots = total_minutes // 30
    time_slots = [(dt_open + datetime.timedelta(minutes=30*i)).strftime("%H:%M") for i in range(num_slots)]
    peak_slots = [i for i, t in enumerate(time_slots) if dt_peak_start <= dt_open + datetime.timedelta(minutes=30*i) < dt_peak_end]

    col5, col6, col7 = st.columns(3)
    with col5: req_open = st.number_input("오픈 고정 인원", 1, 5, def_req_open)
    with col6: req_close = st.number_input("마감 고정 인원", 1, 5, def_req_close)
    with col7: min_floor = st.number_input("상시 최소 인원", 1, 5, def_min_floor)

    col8, col9, col10 = st.columns(3)
    opts_mid_start = time_slots[1:peak_slots[0]+1] if peak_slots else time_slots[1:-4]
    default_ms_idx = opts_mid_start.index(def_first_mid) if def_first_mid in opts_mid_start else (opts_mid_start.index("08:30") if "08:30" in opts_mid_start else 0)
    with col8: first_mid_start_str = st.selectbox("🌅 첫 미들 출근", opts_mid_start, index=default_ms_idx)
    
    with col9: peak_staff = st.number_input("🔥 피크 고정 인원 (정확히 맞춤)", 1, 10, def_peak_staff)
        
    opts_mid_end = [(dt_open + datetime.timedelta(minutes=30*i)).strftime("%H:%M") for i in range(peak_slots[-1] if peak_slots else 2, num_slots)]
    default_me_idx = opts_mid_end.index(def_last_mid) if def_last_mid in opts_mid_end else (opts_mid_end.index("21:00") if "21:00" in opts_mid_end else len(opts_mid_end)-2)
    with col10: last_mid_end_str = st.selectbox("🌙 마감 전 마지막 미들 퇴근", opts_mid_end, index=default_me_idx)

    first_mid_start_idx = time_slots.index(first_mid_start_str)
    last_mid_end_idx = opts_mid_end.index(last_mid_end_str) + (num_slots - len(opts_mid_end))
    
    if st.button("💾 이 설정을 우리 매장 기본값으로 저장", type="secondary"):
        new_settings = pd.DataFrame([
            {"항목": "open_time", "값": open_time.strftime("%H:%M")},
            {"항목": "close_time", "값": close_time.strftime("%H:%M")},
            {"항목": "peak_start", "값": peak_start.strftime("%H:%M")},
            {"항목": "peak_end", "값": peak_end.strftime("%H:%M")},
            {"항목": "req_open", "값": req_open},
            {"항목": "req_close", "값": req_close},
            {"항목": "min_floor", "값": min_floor},
            {"항목": "peak_staff", "값": peak_staff},
            {"항목": "first_mid_start_str", "값": first_mid_start_str},
            {"항목": "last_mid_end_str", "값": last_mid_end_str}
        ])
        save_store_data("settings", store_name, new_settings)
        st.session_state.store_settings = dict(zip(new_settings['항목'], new_settings['값']))
        st.success("✅ 매장 기본 설정 저장 완료!")

    st.divider()

    st.header("2. 파트너 명단")
    if 'partner_data' not in st.session_state:
        df_p = load_store_data("partners", store_name, ["매장명", "이름", "직급", "주간최소시간"])
        if df_p.empty: df_p = pd.DataFrame([{"매장명": store_name, "이름": "점장님", "직급": "점장", "주간최소시간": 40}])
        st.session_state.partner_data = df_p
    
    display_df = st.session_state.partner_data.drop(columns=['매장명'], errors='ignore')
    edited_df = st.data_editor(display_df, num_rows="dynamic", use_container_width=True)
    
    total_weekly_min_val = pd.to_numeric(edited_df["주간최소시간"], errors='coerce').fillna(0).sum()
    
    role_counts = edited_df['직급'].value_counts()
    sm_count = role_counts.get('점장', 0)
    asm_count = role_counts.get('부점장', 0)
    ssv_count = role_counts.get('수퍼바이저', 0)
    bari_count = role_counts.get('바리스타', 0)

    col_btn, col_info, col_total = st.columns([2, 6, 2])
    
    with col_btn:
        if st.button("💾 파트너 명단 저장"):
            save_store_data("partners", store_name, edited_df)
            edited_df['매장명'] = store_name
            st.session_state.partner_data = edited_df
            st.success("✅ 저장 완료!")
            st.rerun()
            
    with col_info:
        st.markdown(f"<div style='text-align: center; font-size: 15px; color: #a5b1c2; padding-top: 10px;'>👥 점장 <b>{sm_count}</b>명 | 부점장 <b>{asm_count}</b>명 | 수퍼바이저 <b>{ssv_count}</b>명 | 바리스타 <b>{bari_count}</b>명</div>", unsafe_allow_html=True)

    with col_total:
        st.markdown(f"<div style='text-align: right; font-weight: bold; font-size: 18px; color: #ff4b4b; padding-top: 5px;'>합계: {total_weekly_min_val:,.0f} h</div>", unsafe_allow_html=True)
        
    partner_names = edited_df["이름"].tolist()
    total_period_min = round(total_weekly_min_val * (num_days / 7.0), 1)
    wh_diff = round(target_wh - total_period_min, 1)

    st.write("")
    kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
    kpi_col1.metric("💰 점장님 목표 WH", f"{target_wh} h")
    kpi_col2.metric("👥 필수 보장 합계 WH", f"{total_period_min} h")
    
    if wh_diff < 0:
        kpi_col3.metric("🚨 예산 초과", f"{wh_diff} h", "-")
        role_wh = {}
        for idx, row in edited_df.iterrows():
            role = row['직급']
            w_min = pd.to_numeric(row['주간최소시간'], errors='coerce')
            if pd.isna(w_min): w_min = 0
            role_wh[role] = role_wh.get(role, 0) + round(w_min * (num_days / 7.0), 1)
            
        excess_h = abs(wh_diff)
        role_breakdown = " · ".join([f"**{r}** {h}h" for r, h in role_wh.items() if h > 0])
        
        st.warning(f"⚠️ **[스케줄 조정 필요] 목표 WH 예산을 초과했습니다! 총 {excess_h}시간을 줄여주세요.**\n\n"
                    f"📊 **현재 직급별 필수 할당 워킹(WH) 현황:** {role_breakdown}\n\n"
                    f"💡 **해결 가이드:** 위 현황을 참고하여, 인력 여유가 있는 직급의 파트너에게 연차나 휴무를 부여하거나 주간최소시간을 줄여 스케줄러 내의 무게를 덜어내야 합니다.")
    else:
        kpi_col3.metric("✅ 예산 여유", f"+{wh_diff} h", "+")

    st.divider()

    st.header("3. 변수 설정 (휴무/격리)")
    col_r1, col_r2 = st.columns(2)
    with col_r1:
        st.subheader("📌 휴무 신청")
        if 'requests_data' not in st.session_state:
            st.session_state.requests_data = load_store_data("requests", store_name, ["매장명", "이름", "날짜", "신청내용"])
        
        if partner_names:
            r_name = st.selectbox("파트너", partner_names, key="r_name")
            r_date = st.date_input("날짜", value=start_date, key="r_date")
            r_type = st.selectbox("신청 내용", ["휴무", "오픈 희망", "미들 희망", "마감 희망"])
            if st.button("휴무 추가", key="btn_req"):
                new_req = pd.DataFrame([{"매장명": store_name, "이름": r_name, "날짜": str(r_date), "신청내용": r_type}])
                st.session_state.requests_data = pd.concat([st.session_state.requests_data, new_req], ignore_index=True)
                save_store_data("requests", store_name, st.session_state.requests_data.drop(columns=['매장명'], errors='ignore'))
                st.success("추가 완료!")
                
        req_disp = st.session_state.requests_data.drop(columns=['매장명'], errors='ignore')
        if not req_disp.empty:
            edited_req = st.data_editor(req_disp, num_rows="dynamic", key="req_editor")
            if not edited_req.equals(req_disp): 
                save_store_data("requests", store_name, edited_req)
                edited_req['매장명'] = store_name
                st.session_state.requests_data = edited_req

    with col_r2:
        st.subheader("🚫 동반 근무 최소화")
        if 'conflicts_data' not in st.session_state:
            st.session_state.conflicts_data = load_store_data("conflicts", store_name, ["매장명", "파트너1", "파트너2"])
        
        if partner_names:
            c_p1 = st.selectbox("파트너 1", partner_names, key='c_p1')
            c_p2 = st.selectbox("파트너 2", partner_names, key='c_p2')
            if st.button("격리 추가", key="btn_conf"):
                if c_p1 != c_p2:
                    new_conf = pd.DataFrame([{"매장명": store_name, "파트너1": c_p1, "파트너2": c_p2}])
                    st.session_state.conflicts_data = pd.concat([st.session_state.conflicts_data, new_conf], ignore_index=True)
                    save_store_data("conflicts", store_name, st.session_state.conflicts_data.drop(columns=['매장명'], errors='ignore'))
                    st.success("추가 완료!")
                    
        conf_disp = st.session_state.conflicts_data.drop(columns=['매장명'], errors='ignore')
        if not conf_disp.empty:
            edited_conf = st.data_editor(conf_disp, num_rows="dynamic", key="conf_editor")
            if not edited_conf.equals(conf_disp):
                save_store_data("conflicts", store_name, edited_conf)
                edited_conf['매장명'] = store_name
                st.session_state.conflicts_data = edited_conf

    st.divider() 
    if st.button("✨ 스마트 스케줄 생성하기", use_container_width=True, type="primary"):
        with st.spinner("알고리즘이 최적의 스케줄을 계산 중입니다. 잠시만 기다려주세요... (최대 15초 소요)"):
            model = cp_model.CpModel()
            num_partners = len(partner_names)
            leader_titles = ['점장', '부점장', '수퍼바이저']
            
            # ✅ KeyError 완벽 해결: 파트너 삭제/추가 시에도 번호가 꼬이지 않도록 수정!
            leader_indices = [i for i, role in enumerate(edited_df['직급']) if role in leader_titles]
            
            start_vars = {}
            work_vars = {}
            ext_vars = [] 
            
            for p in range(num_partners):
                p_role = edited_df.iloc[p]['직급']
                bs = int(get_role_times(p_role)[2] * 2) 
                is_leader_penalty = 10 if p_role in leader_titles else 1 
                
                for d in range(num_days):
                    day_starts = []
                    for L in range(bs, bs + 5):
                        for t in range(num_slots - L + 1):
                            var = model.NewBoolVar(f's_{p}_{d}_{t}_L{L}')
                            start_vars[(p, d, t, L)] = var
                            day_starts.append(var)
                            if L > bs:
                                ext_vars.append(var * (L - bs) * is_leader_penalty)
                    model.Add(sum(day_starts) <= 1)
                    
                    for t in range(num_slots):
                        work_vars[(p, d, t)] = model.NewBoolVar(f'w_{p}_{d}_{t}')
                        active_shifts = []
                        for L in range(bs, bs + 5):
                            for start_t in range(max(0, t - L + 1), min(t + 1, num_slots - L + 1)):
                                if (p, d, start_t, L) in start_vars:
                                    active_shifts.append(start_vars[(p, d, start_t, L)])
                        model.Add(work_vars[(p, d, t)] == sum(active_shifts))

            for d in range(num_days):
                open_sum = [start_vars[(p, d, 0, L)] for p in range(num_partners) for L in range(int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2), int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2) + 5) if (p, d, 0, L) in start_vars]
                model.Add(sum(open_sum) == req_open)
                
                close_sum = [start_vars[(p, d, num_slots - L, L)] for p in range(num_partners) for L in range(int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2), int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2) + 5) if (p, d, num_slots - L, L) in start_vars]
                model.Add(sum(close_sum) == req_close)
                
                for t in range(num_slots):
                    model.Add(sum(work_vars[(p, d, t)] for p in range(num_partners)) >= min_floor)
                    model.Add(sum(work_vars[(p, d, t)] for p in leader_indices) >= 1)

            for d in range(num_days):
                for t in range(1, first_mid_start_idx):
                    for p in range(num_partners):
                        bs = int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2)
                        for L in range(bs, bs + 5):
                            if (p, d, t, L) in start_vars:
                                model.Add(start_vars[(p, d, t, L)] == 0)
                
                starts_at_first_mid = [start_vars[(p, d, first_mid_start_idx, L)] for p in range(num_partners) for L in range(int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2), int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2) + 5) if (p, d, first_mid_start_idx, L) in start_vars]
                if starts_at_first_mid: model.Add(sum(starts_at_first_mid) >= 1)
                    
                for p in range(num_partners):
                    bs = int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2)
                    for L in range(bs, bs + 5):
                        for t in range(num_slots - L + 1):
                            end_t = t + L
                            if last_mid_end_idx < end_t < num_slots:
                                if (p, d, t, L) in start_vars: model.Add(start_vars[(p, d, t, L)] == 0)
                                    
                ends_at_last_mid = []
                for p in range(num_partners):
                    bs = int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2)
                    for L in range(bs, bs + 5):
                        t = last_mid_end_idx - L
                        if t > 0 and (p, d, t, L) in start_vars: ends_at_last_mid.append(start_vars[(p, d, t, L)])
                if ends_at_last_mid: model.Add(sum(ends_at_last_mid) >= 1)
                    
                for t in peak_slots:
                    model.Add(sum(work_vars[(p, d, t)] for p in range(num_partners)) == peak_staff)

            stagger_vars = []
            for d in range(num_days):
                for t in range(1, num_slots):
                    starts_here = []
                    for p in range(num_partners):
                        bs = int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2)
                        for L in range(bs, bs + 5):
                            if (p, d, t, L) in start_vars: starts_here.append(start_vars[(p, d, t, L)])
                    if starts_here:
                        ex_s = model.NewIntVar(0, num_partners, f'ex_s_{d}_{t}')
                        model.Add(sum(starts_here) - 1 <= ex_s)
                        stagger_vars.append(ex_s)
                    
                    ends_here = []
                    for p in range(num_partners):
                        bs = int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2)
                        for L in range(bs, bs + 5):
                            start_t = t - L
                            if start_t >= 0 and (p, d, start_t, L) in start_vars:
                                ends_here.append(start_vars[(p, d, start_t, L)])
                    if ends_here:
                        ex_e = model.NewIntVar(0, num_partners, f'ex_e_{d}_{t}')
                        model.Add(sum(ends_here) - 1 <= ex_e)
                        stagger_vars.append(ex_e)

            mao_vars = []
            mao_tracking = []
            for p in range(num_partners):
                bs = int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2)
                for d in range(num_days - 1):
                    close_today_list = [start_vars[(p, d, num_slots - L, L)] for L in range(bs, bs + 5) if (p, d, num_slots - L, L) in start_vars]
                    open_tmrw_list = [start_vars[(p, d + 1, 0, L)] for L in range(bs, bs + 5) if (p, d + 1, 0, L) in start_vars]
                    
                    if close_today_list and open_tmrw_list:
                        close_today = sum(close_today_list)
                        open_tmrw = sum(open_tmrw_list)
                        mao_var = model.NewBoolVar(f'mao_{p}_{d}')
                        model.Add(mao_var >= close_today + open_tmrw - 1)
                        mao_vars.append(mao_var)
                        mao_tracking.append({'var': mao_var, 'p_idx': p, 'd_idx': d})

            if not st.session_state.requests_data.empty:
                for _, row in st.session_state.requests_data.iterrows():
                    req_name, req_date_raw, req_type = row["이름"], row["날짜"], row["신청내용"]
                    if isinstance(req_date_raw, str): req_date = datetime.datetime.strptime(req_date_raw, "%Y-%m-%d").date()
                    else: req_date = req_date_raw
                    if req_name in partner_names:
                        p_idx = partner_names.index(req_name)
                        d_idx = (req_date - start_date).days 
                        if 0 <= d_idx < num_days:
                            bs = int(get_role_times(edited_df.iloc[p_idx]['직급'])[2] * 2)
                            if req_type == "휴무":
                                model.Add(sum(start_vars[(p_idx, d_idx, t, L)] for L in range(bs, bs+5) for t in range(num_slots - L + 1)) == 0)
                            elif req_type == "오픈 희망":
                                model.Add(sum(start_vars[(p_idx, d_idx, 0, L)] for L in range(bs, bs+5) if (p_idx, d_idx, 0, L) in start_vars) == 1)
                            elif req_type == "마감 희망":
                                model.Add(sum(start_vars[(p_idx, d_idx, num_slots - L, L)] for L in range(bs, bs+5) if (p_idx, d_idx, num_slots - L, L) in start_vars) == 1)
                            elif req_type == "미들 희망":
                                mid_s = []
                                for L in range(bs, bs+5):
                                    for t in range(1, num_slots - L):
                                        if (p_idx, d_idx, t, L) in start_vars: mid_s.append(start_vars[(p_idx, d_idx, t, L)])
                                model.Add(sum(mid_s) == 1)
                            
            overlap_vars = []
            if not st.session_state.conflicts_data.empty:
                for _, row in st.session_state.conflicts_data.iterrows():
                    p1_name, p2_name = row["파트너1"], row["파트너2"]
                    if p1_name in partner_names and p2_name in partner_names:
                        p1_idx, p2_idx = partner_names.index(p1_name), partner_names.index(p2_name)
                        for d in range(num_days):
                            for t in range(num_slots):
                                ov_var = model.NewBoolVar(f'ov_{p1_idx}_{p2_idx}_{d}_{t}')
                                model.Add(ov_var >= work_vars[(p1_idx, d, t)] + work_vars[(p2_idx, d, t)] - 1)
                                overlap_vars.append(ov_var)

            total_work_hours_vars = []
            undertimes = []
            
            for p in range(num_partners):
                p_role = edited_df.iloc[p]['직급']
                weekly_min_hrs = int(edited_df.iloc[p]['주간최소시간'])
                work_h, break_h, stay_h = get_role_times(p_role)
                bs = int(stay_h * 2)
                
                target_shifts = int(round((weekly_min_hrs / work_h) * (num_days / 7.0)))
                shift_count = sum(start_vars[(p, d, t, L)] for d in range(num_days) for L in range(bs, bs+5) for t in range(num_slots - L + 1) if (p, d, t, L) in start_vars)
                model.Add(shift_count <= target_shifts)
                
                undertime_var = model.NewIntVar(0, num_days, f'undertime_p{p}')
                model.Add(target_shifts - shift_count <= undertime_var)
                undertimes.append(undertime_var)
                
                for d in range(num_days):
                    for L in range(bs, bs + 5):
                        for t in range(num_slots - L + 1):
                            if (p, d, t, L) in start_vars:
                                actual_work_h = int(((L / 2) - break_h) * 10) 
                                total_work_hours_vars.append(start_vars[(p, d, t, L)] * actual_work_h)

            total_used_wh = sum(total_work_hours_vars)
            peak_coverage = sum(work_vars[(p, d, t)] for p in range(num_partners) for d in range(num_days) for t in peak_slots)
            
            model.Minimize(
                sum(undertimes) * 100000 + sum(overlap_vars) * 500 + total_used_wh * 10 - peak_coverage * 80 + sum(ext_vars) * 20 + sum(stagger_vars) * 150 + sum(mao_vars) * 50000
            )
                    
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 15.0
            status = solver.Solve(model)
            
            if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
                used_wh_final = solver.Value(total_used_wh) / 10.0
                saved_wh = round(target_wh - used_wh_final, 1)
                
                st.success(f"🎉 스케줄 생성 완료! 매일 흔들림 없는 완벽한 스케줄 루틴이 적용되었습니다.")
                
                mao_warnings = []
                for track in mao_tracking:
                    if solver.Value(track['var']) == 1:
                        p_name = partner_names[track['p_idx']]
                        d_date = start_date + datetime.timedelta(days=track['d_idx'])
                        next_date = d_date + datetime.timedelta(days=1)
                        mao_warnings.append(f"• **{p_name}** 파트너 : {d_date.strftime('%m/%d')} 마감 ➡️ {next_date.strftime('%m/%d')} 오픈")
                        
                if mao_warnings:
                    st.error("🚨 **[주의] 극심한 인력/휴무 부족으로 인해 어쩔 수 없이 '마오(마감-오픈)' 스케줄이 배정되었습니다!**\n\n" + "\n".join(mao_warnings))
                
                col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
                col_kpi1.metric("💰 목표 총 워킹(WH)", f"{target_wh} h")
                col_kpi2.metric("실제 배정 워킹(WH)", f"{used_wh_final} h")
                if saved_wh >= 0: col_kpi3.metric("📈 세이브된 워킹 (이익공헌률 UP)", f"+{saved_wh} h", "Success")
                else: col_kpi3.metric("📉 오버된 워킹 (이익공헌률 DOWN)", f"{saved_wh} h", "-")
                st.write("---")

                excel_sheets = {}
                partner_actual_shifts = {p: 0 for p in range(num_partners)}
                partner_extended_slots = {p: 0 for p in range(num_partners)}
                
                for p in range(num_partners):
                    bs = int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2)
                    for d in range(num_days):
                        for L in range(bs, bs + 5):
                            for t in range(num_slots - L + 1):
                                if (p, d, t, L) in start_vars and solver.Value(start_vars[(p, d, t, L)]) == 1:
                                    partner_actual_shifts[p] += 1
                                    partner_extended_slots[p] += (L - bs)
                                    break 

                st.subheader("📊 파트너별 근무 시간 요약")
                hours_summary = []
                for p in range(num_partners):
                    p_name = partner_names[p]
                    p_role = edited_df.iloc[p]['직급']
                    weekly_min_hrs = int(edited_df.iloc[p]['주간최소시간'])
                    work_h = get_role_times(p_role)[0]
                    target_shifts = int(round((weekly_min_hrs / work_h) * (num_days / 7.0)))
                    target_hrs = target_shifts * work_h
                    
                    actual_shifts = partner_actual_shifts[p]
                    assigned_hours = (actual_shifts * work_h) + (partner_extended_slots[p] * 0.5)
                    
                    if assigned_hours < target_hrs: status_text = f"⚠️ {assigned_hours}h (미달)"
                    elif partner_extended_slots[p] > 0: status_text = f"🚨 {assigned_hours}h (연장 포함)"
                    else: status_text = f"✅ {assigned_hours}h"
                        
                    hours_summary.append({"파트너 (직급)": f"{p_name} ({p_role})", "목표 WH": f"{target_hrs}h", "실제 배정 WH": status_text})
                
                df_summary = pd.DataFrame(hours_summary)
                st.dataframe(df_summary, use_container_width=True)
                excel_sheets["근무요약"] = df_summary 

                for d in range(num_days):
                    day_str = (start_date + datetime.timedelta(days=d)).strftime("%Y-%m-%d")
                    st.subheader(f"📅 {day_str} 타임라인 (루틴 및 시차 출퇴근 적용)")
                    day_rows = [] 
                    
                    for p in range(num_partners):
                        bs = int(get_role_times(edited_df.iloc[p]['직급'])[2] * 2)
                        started = False
                        start_t_idx = -1
                        actual_L = 0
                        for L in range(bs, bs + 5):
                            for t in range(num_slots - L + 1):
                                if (p, d, t, L) in start_vars and solver.Value(start_vars[(p, d, t, L)]) == 1:
                                    started = True
                                    start_t_idx = t
                                    actual_L = L
                                    break
                            if started: break
                                
                        if started:
                            p_name = partner_names[p]
                            p_role = edited_df.iloc[p]['직급']
                            is_leader = p_role in leader_titles
                            
                            s_start = dt_open + datetime.timedelta(minutes=30 * start_t_idx)
                            s_end = s_start + datetime.timedelta(minutes=30 * actual_L)
                            
                            if start_t_idx == 0:
                                marker = "오픈(관리자)" if is_leader else "오픈"
                                shift_label = "오픈"
                            elif start_t_idx + actual_L == num_slots:
                                marker = "마감(관리자)" if is_leader else "마감"
                                shift_label = "마감"
                            else:
                                marker = "미들"
                                shift_label = "미들"
                                
                            is_extended = actual_L > bs
                            ext_text = f" (+연장 {(actual_L - bs)/2}h)" if is_extended else ""
                            
                            row_dict = {'이름/출퇴근': f"[{shift_label}] {p_name}{ext_text} ({s_start.strftime('%H:%M')}~{s_end.strftime('%H:%M')})"}
                            
                            for tc_idx, tc in enumerate(time_slots):
                                if start_t_idx <= tc_idx < start_t_idx + actual_L: row_dict[tc] = marker
                                else: row_dict[tc] = ""
                                    
                            day_rows.append({'start_idx': start_t_idx, 'is_leader': 0 if is_leader else 1, 'row': row_dict})
                    
                    if day_rows:
                        day_rows.sort(key=lambda x: (x['start_idx'], x['is_leader']))
                        df_day = pd.DataFrame([item['row'] for item in day_rows])
                        if hasattr(df_day.style, 'map'): styled_df = df_day.style.map(color_schedule)
                        else: styled_df = df_day.style.applymap(color_schedule)
                        st.dataframe(styled_df, use_container_width=True, hide_index=True)
                        sheet_name = (start_date + datetime.timedelta(days=d)).strftime("%m월%d일")
                        excel_sheets[sheet_name] = df_day
                    else:
                        st.write("근무자가 없습니다.")
                        
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    for sheet_name, df_data in excel_sheets.items():
                        df_data.to_excel(writer, sheet_name=sheet_name, index=False)
                output.seek(0)
                
                st.download_button(label="📥 엑셀 파일로 다운로드", data=output, file_name=f"스케줄_{start_date.strftime('%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True, type="primary")
            else:
                st.error("🚨 15초 내에 최적의 스케줄을 찾지 못했습니다. 필수 고정 인원이나 휴무/격리 조건을 살짝 완화해 주세요!")

# ==========================================
# 🔄 앱 라우팅
# ==========================================
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'master_mode' not in st.session_state: st.session_state['master_mode'] = False
if 'show_register' not in st.session_state: st.session_state['show_register'] = None

if st.session_state['master_mode']:
    master_dashboard()
elif st.session_state['logged_in']:
    main_scheduler_app()
else:
    login_page()
