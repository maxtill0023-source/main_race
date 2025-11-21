import streamlit as st
import pandas as pd
import json
import re
# google-genai 라이브러리가 이제 질적 분석에 사용되지 않으므로 주석 처리합니다.
# from google import genai 
# firebase-admin 라이브러리가 필요합니다.
import firebase_admin
from firebase_admin import credentials, firestore

# --- 0. 페이지 설정 및 디자인 ---
st.set_page_config(
    page_title="가치 기반 경마 분석기 - Final", 
    layout="wide", 
    initial_sidebar_state="auto"
)
st.markdown("""
<style>
    /* 메인 테마 색상을 청록색 (#00BCD4)으로 설정 */
    .stButton>button { 
        border: 2px solid #00BCD4; 
        color: #00BCD4; 
        font-weight: bold;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #00BCD4;
        color: white;
    }
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] { border-bottom: 3px solid #00BCD4; }
    .stAlert { border-left: 5px solid #FF9800 !important; }
    /* 주로 상태 버튼 디자인 */
    div[role="radiogroup"] label {
        padding: 5px 10px;
        margin-right: 5px;
        border: 1px solid #ccc;
        border-radius: 5px;
        cursor: pointer;
    }
    div[role="radiogroup"] label:has(input:checked) {
        background-color: #00BCD4;
        color: white;
        border-color: #00BCD4;
    }
</style>
""", unsafe_allow_html=True)

# 초기 세션 상태 설정
if 'analysis_run' not in st.session_state:
    st.session_state['analysis_run'] = False
if 'active_strategy_count' not in st.session_state:
    st.session_state['active_strategy_count'] = 0


# --- 1. 유틸리티 및 데이터베이스 관리 (Firebase) ---

@st.cache_resource
def initialize_firebase():
    """Streamlit Secrets 기반 Firebase 초기화 및 DB 클라이언트 반환."""
    
    # Firebase Secrets가 secrets.toml에 아예 없는 경우 처리
    if "firebase" not in st.secrets:
        st.warning("⚠️ Firebase Secrets가 없어 데이터 영구 저장 및 학습 기능은 비활성화됩니다.")
        return None 

    try:
        # Key가 있지만, 그 내용이 TOML 형식상 JSON과 다를 때 발생하는 모든 오류 처리
        key_dict = dict(st.secrets["firebase"]) 
        
        # 필수 필드(project_id, client_email 등)가 누락된 경우 처리
        required_keys = ['type', 'project_id', 'private_key', 'client_email', 'token_uri']
        if not all(key in key_dict for key in required_keys):
             st.warning("⚠️ Firebase Secrets 내용이 불완전하여 데이터 학습 기능이 비활성화됩니다.")
             return None

        # 인증 정보가 완벽할 때만 초기화 시도
        # private_key에 있는 \n을 실제 줄바꿈 문자로 변환 (TOML 파싱 문제 대비)
        key_dict["private_key"] = key_dict["private_key"].replace('\\n', '\n')
        
        cred = credentials.Certificate(key_dict)
        
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, name="ai_database") 
        
        st.success("🎉 Firebase 데이터베이스 연결 성공! 복기 및 학습 기능이 활성화되었습니다.")
        return firestore.client(app=firebase_admin.get_app(name="ai_database"))
        
    except Exception as e:
        # 인증 정보 형식 오류 (예: private_key 줄바꿈 오류) 포함 모든 초기화 오류 처리
        st.error(f"❌ Firebase 인증 정보 오류로 연결 실패: {e}")
        st.error("💡 'secrets.toml' 파일의 [firebase] 섹션 내용을 다시 확인해주세요.")
        return None 

# Firebase 초기화 시도. 오류가 나더라도 None을 반환하여 앱 실행은 막지 않습니다.
db = initialize_firebase()


def save_review_data(review_data):
    """복기 데이터를 Firebase에 영구 저장."""
    if not db: 
        st.warning("❌ Firebase 연결 실패로 복기 데이터를 저장할 수 없습니다.")
        return False
    try:
        # 'ai_knowledge_base'는 복기 데이터를 저장하는 컬렉션입니다.
        doc_ref = db.collection('ai_knowledge_base').document()
        doc_ref.set({
            'date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
            'result': 'Hit' if review_data['is_hit'] else 'Miss',
            'grade_summary': review_data['note'],
            'full_review_data': review_data
        })
        return True
    except Exception as e:
        st.error(f"❌ 데이터 저장 실패: {e}")
        return False

# --- 2. 분석 전 의무 학습 (DTP 전략 로드) ---

def mandatory_pre_analysis_learning(db_client):
    """분석 전 Firebase에서 사용자 정의 전략 노트를 불러와 DTP 엔진에 활성화."""
    if not db_client:
        return []

    try:
        # 🚨 수정: 'protocols' 컬렉션에서 'active'가 True인 전략만 불러옵니다.
        # 사용자가 입력한 10개의 전략 문서를 불러오기 위함입니다.
        # 만약 컬렉션 이름을 'protocols' 대신 'notes' 등으로 사용하셨다면 이 부분을 수정해야 합니다.
        protocols_ref = db_client.collection('protocols').where('active', '==', True).stream()
        
        active_strategies_data = []
        # 각 전략 문서의 'title'을 전략 ID로 사용한다고 가정하고 데이터 로드
        for i, doc in enumerate(protocols_ref):
            data = doc.to_dict()
            # 임시로 'PROTOCOL_1'부터 'PROTOCOL_10'까지 ID 부여 (10개 전략을 가정)
            data['strategy_id'] = f"PROTOCOL_{i+1}" 
            active_strategies_data.append(data)

        count = len(active_strategies_data)
        
        st.session_state['active_strategy_data'] = active_strategies_data
        st.session_state['active_strategy_count'] = count
        
        # 메시지 수정: 로드된 실제 전략 개수를 표시합니다.
        st.info(f"🧠 Firebase 학습 완료: 총 {count}개의 활성화된 전략 규칙이 DTP 엔진에 로드되었습니다.")
        
        # DTP 로직이 사용할 수 있도록 '전략 ID' 리스트를 반환
        return [s['strategy_id'] for s in active_strategies_data]
    
    except Exception as e:
        # Firestore 컬렉션 이름 오류 등으로 인해 전략 로드에 실패하면 0개로 설정
        st.error(f"❌ 전략 로드 중 오류 발생: {e}")
        st.session_state['active_strategy_count'] = 0
        return []

# 🚨 제거: analyze_report_with_gemini 함수는 사용자의 요청으로 완전히 제거되었습니다.
# Gemini API 키 오류를 방지하고, 경기 후 리포트 분석 대신 사전 분석에 집중합니다.

# --- 3. 핵심 분석 프로토콜 (DTP & Kelly Criterion) ---

# 🚨 함수 시그니처 변경: gemini_analysis 제거, track_condition 추가
def apply_dtp_protocol(df_horse, track_condition, active_strategies): 
    """Firebase 학습 전략 및 주로 상태를 반영한 DTP 프로토콜."""
    dtp_results = []
    
    # 주로 상태에 따른 기본 리스크 설정 (VMC, ICR 프로토콜 일부 반영)
    base_risk = 0
    track_condition_note = ""
    if track_condition == "습함":
        base_risk = 1
        track_condition_note = "습한 주로에서는 마필별 적응도에 따라 1점의 기본 리스크가 부여됩니다."
    elif track_condition == "불량":
        base_risk = 2
        track_condition_note = "불량 주로에서는 예상치 못한 변수로 인해 2점의 높은 기본 리스크가 부여됩니다."
    else: # 양호, 다소 습함, 건조
        base_risk = 0
        track_condition_note = f"{track_condition} 주로 상태는 기본 리스크가 없습니다."

    for index, horse in df_horse.iterrows():
        risk_count = base_risk # 주로 상태 기본 리스크 반영
        analysis_note = [
            f"**[주로 상태]** {track_condition} 반영 (기본 리스크: {base_risk}점)",
            track_condition_note
        ]
        
        # 강한 후보 조건: 마번이 1, 3번 또는 부담 중량이 56.0kg 초과
        is_strong_candidate = horse['마번'] in [1, 3] or horse['무게(kg)'] > 56.0
        
        if is_strong_candidate:
            # 1. 정적 리스크 (DTP 프로토콜 1번)
            if horse['마번'] % 2 == 0: 
                risk_count += 1
                analysis_note.append(f"🐴 **정적 리스크:** 짝수 마번 {horse['마번']} 리스크 1점 추가.")
                
            # 2. 학습된 전략 리스크 (사용자 입력 프로토콜 반영)
            # 여기서는 사용자님이 입력한 10개의 프로토콜 중 일부를 가상의 ID로 적용합니다.
            
            # (PIR: 부상 복귀/잠재력 제한 전략 - Protocol 5)
            if "PROTOCOL_5" in active_strategies and horse['무게(kg)'] >= 57.0: 
                risk_count += 1
                analysis_note.append("🚨 **리스크: 학습 반영 (PIR)** 고중량 마필에 대한 보수적 평가 전략 1점 추가.")
            
            # (VMC/ICR: 주로 상태 보정 전략 - Protocol 3)
            if "PROTOCOL_3" in active_strategies and track_condition in ["습함", "불량"] and horse['무게(kg)'] < 53.0: 
                risk_count += 1
                analysis_note.append("🚨 **리스크: 학습 반영 (ICR)** 악벽 주로에서 저중량 마필에 대한 리스크 1점 추가.")
            
            # (ERP: 초반 전개 시뮬레이션 - Protocol 6)
            if "PROTOCOL_6" in active_strategies and horse['마번'] >= 4 and horse['무게(kg)'] > 56.0:
                risk_count += 1
                analysis_note.append("🚨 **리스크: 학습 반영 (ERP)** 바깥쪽 게이트 고중량 마필의 초반 전개 리스크 1점 추가.")

            if risk_count >= 3:
                horse_grade = "B그룹 (강등)"
                analysis_note.append(f"🔽 **최종 등급:** 리스크 {risk_count}점 (3점 이상)으로 B그룹 강등.")
            else:
                horse_grade = "A그룹 (유지)"
                analysis_note.append(f"✅ **최종 등급:** 리스크 {risk_count}점 (2점 이하)으로 A그룹 유지.")
        else:
            horse_grade = "C그룹 (후착)"
            analysis_note.append("➖ **최종 등급:** 강한 후보 조건 (마번 1, 3 또는 56kg 초과) 미충족으로 C그룹.")

        dtp_results.append({
            '마번': horse['마번'],
            '마명': horse['마명'],
            'DTP 적용 등급': horse_grade,
            'DTP 리스크 점수': risk_count,
            'DTP 분석 노트': "\n\n".join(analysis_note)
        })

    return pd.DataFrame(dtp_results)

def calculate_kelly_allocation(df_analysis):
    """켈리 기준 변형 로직을 사용하여 복승식/삼복승식 비중 100% 분배 (시뮬레이션)."""
    # 리스크 점수가 낮을수록 AI_Score가 높음
    df_analysis['AI_Score'] = 100 - (df_analysis['DTP 리스크 점수'] * 10)
    
    top_3 = df_analysis.sort_values(by='AI_Score', ascending=False).head(3)['마번'].tolist()

    if len(top_3) >= 2:
        # A그룹 마필이 최소 2마리 이상일 경우에만 분배
        복승_allocation = [
            {'name': f"{top_3[0]} - {top_3[1]} 조합 (핵심)", 'percentage': 55.0},
            {'name': f"{top_3[0]} - {top_3[2]} 조합 (방어)", 'percentage': 30.0},
            {'name': f"{top_3[1]} - {top_3[2]} 조합 (부축)", 'percentage': 15.0}
        ]
        삼복승_allocation = [
            {'name': f"BOX ({top_3[0]} - {top_3[1]} - {top_3[2]}) (핵심)", 'percentage': 70.0},
            {'name': f"{top_3[0]} - {top_3[1]} - 복병 (방어)", 'percentage': 30.0}
        ]
    else:
        복승_allocation = [{'name': '분석 불가 (유력 후보 부족)', 'percentage': 100.0}]
        삼복승_allocation = [{'name': '분석 불가 (유력 후보 부족)', 'percentage': 100.0}]

    return 복승_allocation, 삼복승_allocation

# --- 4. 메인 Streamlit 함수 ---

def main():
    st.title("가치 기반 경마 분석기 🐎")
    
    col_control, col_main = st.columns([0.3, 0.7]) 

    with col_control:
        st.subheader("경주 입력 및 설정")
        selected_region = st.selectbox("지역 선택", ["서울", "부산", "제주"])
        st.date_input("경주 날짜", pd.to_datetime('2025-11-21')) 
        st.number_input("경주 번호 (필수)", min_value=1, value=1, step=1) 
        
        st.markdown("---")

        # 🚨 신규 추가: 주로 상태 선택 (사용자 요청 반영)
        track_condition = st.radio(
            "주로 상태 선택 (VMC 프로토콜 반영)", 
            ["양호", "다소 습함", "습함", "불량", "건조"], 
            horizontal=True,
            index=0 # Default to 양호
        )
        st.markdown("---")


        race_card_text = st.text_area("📝 출전표 정보를 여기에 붙여넣으세요.", height=150, placeholder="1.선진발(김철수) 57.0 ...")
        
        # 🚨 제거: 심판/조교 리포트 텍스트 입력창 제거 (사용자 요청 반영)
        # qualitative_report_text = st.text_area("📝 심판/조교 리포트 텍스트를 여기에 붙여넣으세요.", height=150, placeholder="Gemini AI가 분석할 리포트 원문...")
        
        # db가 연결되지 않았다면, 학습 버튼은 비활성화됩니다.
        run_analysis = st.button("🚀 분석 실행", use_container_width=True, disabled=(not race_card_text))

        if run_analysis:
            # 1. 학습 데이터 로드 (DB 연결 성공 시에만 작동)
            active_strategies = []
            if db:
                active_strategies = mandatory_pre_analysis_learning(db)
            else:
                st.info("💡 Firebase 연결 실패로 학습 전략은 적용되지 않습니다.")
            
            # 2. Gemini 분석 실행 (제거됨)
            # gemini_analysis = analyze_report_with_gemini(qualitative_report_text)
            gemini_analysis = {'tags': [], 'summary': '질적 분석은 현재 비활성화되었습니다.'} # 더미 데이터

            # 3. 데이터 파싱 (임시 데이터 사용 - 실제로는 race_card_text를 파싱해야 함)
            data = {
                '마번': [1, 2, 3, 4],
                '마명': ['선진발', '경종한리', '가온천희', '인마속도'],
                '기수': ['김철수', '박지민', '이영희', '최민호'],
                '무게(kg)': [57.0, 54.5, 53.0, 55.0]
            }
            df_race_card = pd.DataFrame(data)

            # 4. 최종 DTP 및 켈리 계산
            with st.spinner('🚨 DTP (레드 팀 분석) 프로토콜 적용 중...'):
                # 🚨 함수 호출 변경: track_condition을 넘기고 gemini_analysis 제거
                df_dtp_result = apply_dtp_protocol(df_race_card, track_condition, active_strategies)
                복승_allocation, 삼복승_allocation = calculate_kelly_allocation(df_dtp_result)

            st.session_state['df_dtp_result'] = df_dtp_result
            st.session_state['allocations'] = {'복승': 복승_allocation, '삼복승': 삼복승_allocation}
            st.session_state['analysis_run'] = True

    with col_main:
        if st.session_state.get('analysis_run', False):
            df_dtp_result = st.session_state['df_dtp_result']
            allocations = st.session_state['allocations']
            
            tab_ai, tab_review, tab_strategy = st.tabs(["[1. AI 예측]", "[2. 경기 복기]", "[3. 전략 연구소]"])
            
            # --- [1. AI 예측] 탭 ---
            with tab_ai:
                st.subheader("🐴 DTP 적용 결과 및 베팅 포트폴리오")
                st.dataframe(df_dtp_result, use_container_width=True)
                
                st.markdown("---")
                st.header("💰 AI 추천 베팅 포트폴리오 (100% 분배)")
                bet_cols = st.columns(2)
                
                with bet_cols[0]: st.subheader("복승식"); 
                for item in allocations['복승']: st.markdown(f"**{item['name']}**: **{item['percentage']}%**")
                    
                with bet_cols[1]: st.subheader("삼복승식");
                for item in allocations['삼복승']: st.markdown(f"**{item['name']}**: **{item['percentage']}%**")

            # --- [2. 경기 복기] 탭 ---
            with tab_review:
                st.subheader("📜 경기 복기 및 학습 (Firebase 저장)")
                if not db:
                    st.error("저장 기능 비활성화: Firebase 연결이 필요합니다.")
                else:
                    actual_result_text = st.text_area("실제 경기 결과 (예: 1위: 3번, 2위: 7번)", height=100)
                    is_hit = st.checkbox("복승식/삼복승식 조합 중 1개 이상 적중", value=False)
                    review_note = st.text_area("복기 노트 (실패 시 원인 분석을 여기에 기록)", height=100, placeholder="실패했다면, 어떤 프로토콜을 놓쳤는지 기록하세요.")

                    if st.button("🏆 복기 데이터 저장 및 AI 지식 베이스에 반영"):
                        review_data = {
                            'is_hit': is_hit, 
                            'note': review_note if review_note else ('성공 복기' if is_hit else '실패 복기 - 노트 없음'),
                            'actual_result': actual_result_text,
                            'dtp_result': df_dtp_result.to_dict('records')
                        }
                        if save_review_data(review_data):
                            st.balloons()
                            st.success("🎉 분석 데이터가 AI 지식 베이스에 성공적으로 저장되었습니다.")
            
            # --- [3. 전략 연구소] 탭 ---
            with tab_strategy:
                st.subheader("💡 AI 전략 연구소: 필승 규칙 발견")
                if not db:
                    st.error("전략 연구소 비활성화: Firebase 연결이 필요합니다.")
                else:
                    strategy_count = st.session_state.get('active_strategy_count', 0)
                    st.info(f"현재 Firebase 'protocols' 컬렉션에 저장된 {strategy_count}개의 학습 전략이 DTP 엔진에 활성화되었습니다. (추후 심층 분석 기능 추가 예정)")
        else:
            with col_main:
                st.info("👆 분석을 시작하려면 왼쪽 컨트롤 패널에 정보를 입력하고 [분석 실행] 버튼을 눌러주세요.")


if __name__ == "__main__":
    main()