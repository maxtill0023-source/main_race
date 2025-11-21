import streamlit as st
import pandas as pd
import json
import re
# google-genai 라이브러리가 필요합니다.
from google import genai 
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
    .stButton>button { border: 2px solid #00BCD4; color: #00BCD4; }
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] { border-bottom: 3px solid #00BCD4; }
    .stAlert { border-left: 5px solid #FF9800 !important; }
</style>
""", unsafe_allow_html=True)


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

# --- 2. 분석 전 의무 학습 및 Gemini 분석 ---

def mandatory_pre_analysis_learning(db_client):
    """분석 전 Firebase에서 모든 학습 노트를 불러와 전략 규칙을 활성화."""
    if not db_client:
        return []

    notes_ref = db_client.collection('ai_knowledge_base').stream()
    active_strategies = []
    
    for doc in notes_ref:
        data = doc.to_dict()
        if data.get('result') == 'Miss' and '주로 상태' in data.get('grade_summary', ''):
            active_strategies.append('RISK_APPLY_TRACK_CONDITION')
        if data.get('result') == 'Miss' and '잠재력' in data.get('grade_summary', ''):
            active_strategies.append('RISK_APPLY_POTENTIAL_LIMIT')
            
    st.session_state['active_strategy_count'] = len(set(active_strategies))
    st.info(f"🧠 Firebase 학습 완료: 총 {len(active_strategies)}개의 활성화된 전략 규칙이 DTP 엔진에 로드되었습니다.")
    return list(set(active_strategies))

def analyze_report_with_gemini(report_text):
    """Gemini를 사용하여 심판 리포트에서 질적 태그를 추출."""
    if not report_text:
        return {'tags': [], 'summary': '리포트 텍스트 없음'}
    
    # Gemini Secrets가 없거나 api_key가 비어있으면 분석을 건너뜁니다.
    if "gemini" not in st.secrets or not st.secrets["gemini"].get("api_key"):
        st.warning("⚠️ Gemini API 키가 없어 질적 분석을 건너뜁니다.")
        return {'tags': [], 'summary': 'Gemini API 키 없음'}

    try:
        api_key = st.secrets["gemini_api_key"]
        client = genai.Client(api_key=api_key) 
        
        prompt = (
            f"다음 심판/조교 리포트를 분석하여, 마필의 행동 특성이나 악벽을 나타내는 **핵심 태그 3가지**를 추출하고, 이 태그가 DTP 분석에 필요한 이유를 간결하게 요약하세요. 태그는 쉼표로 구분하세요.\n\n리포트 텍스트: {report_text}"
        )
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        
        tags = [t.strip() for t in response.text.split('\n')[0].split(',') if t.strip()]
        st.success("🤖 Gemini AI 리포트 분석 완료!")
        
        return {
            'tags': tags,
            'summary': response.text
        }
        
    except Exception as e:
        st.error(f"❌ Gemini API 호출 오류: {e}")
        return {'tags': [], 'summary': 'Gemini 분석 실패 (API 오류)'}

# --- 3. 핵심 분석 프로토콜 (DTP & Kelly Criterion) ---

def apply_dtp_protocol(df_horse, gemini_analysis, active_strategies):
    """Gemini 태그 및 Firebase 학습 전략을 반영한 DTP 프로토콜."""
    dtp_results = []
    qualitative_tags = gemini_analysis['tags']
    dtp_summary_note = f"**[Gemini 리포트 분석 요약]**\n{gemini_analysis['summary']}"
    
    for index, horse in df_horse.iterrows():
        risk_count = 0 
        analysis_note = [dtp_summary_note]
        
        is_strong_candidate = horse['마번'] in [1, 3] or horse['무게(kg)'] > 56.0
        
        if is_strong_candidate:
            # 1. 정적 리스크
            if horse['마번'] % 2 == 0: risk_count += 1
                
            # 2. Gemini 질적 리스크
            if '출발 지연' in qualitative_tags and horse['마번'] == 1: 
                risk_count += 2 
                analysis_note.append("🚨 **리스크: Gemini 태그** '출발 지연'으로 인한 안쪽 게이트 리스크.")

            # 3. 학습된 전략 리스크
            if 'RISK_APPLY_POTENTIAL_LIMIT' in active_strategies and horse['무게(kg)'] >= 57.0:
                risk_count += 1 
                analysis_note.append("🚨 **리스크: 학습 반영** 고중량 마필에 대한 보수적 평가 전략 적용.")

            if risk_count >= 3:
                horse_grade = "B그룹 (강등)"
            else:
                horse_grade = "A그룹 (유지)"
        else:
            horse_grade = "C그룹 (후착)"

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
    df_analysis['AI_Score'] = 100 - (df_analysis['DTP 리스크 점수'] * 10)
    
    top_3 = df_analysis.sort_values(by='AI_Score', ascending=False).head(3)['마번'].tolist()

    if len(top_3) >= 2:
        복승_allocation = [
            {'name': f"{top_3[0]}-{top_3[1]} 조합 (핵심)", 'percentage': 55.0},
            {'name': f"{top_3[0]}-{top_3[2]} 조합 (방어)", 'percentage': 30.0},
            {'name': f"{top_3[1]}-{top_3[2]} 조합 (부축)", 'percentage': 15.0}
        ]
        삼복승_allocation = [
            {'name': f"BOX ({top_3[0]}-{top_3[1]}-{top_3[2]}) (핵심)", 'percentage': 70.0},
            {'name': f"{top_3[0]}-{top_3[1]}-복병 (방어)", 'percentage': 30.0}
        ]
    else:
        복승_allocation = [{'name': '분석 불가', 'percentage': 100.0}]
        삼복승_allocation = [{'name': '분석 불가', 'percentage': 100.0}]

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

        race_card_text = st.text_area("📝 출전표 정보를 여기에 붙여넣으세요.", height=150, placeholder="1.선진발(김철수) 57.0 ...")
        qualitative_report_text = st.text_area("📝 심판/조교 리포트 텍스트를 여기에 붙여넣으세요.", height=150, placeholder="Gemini AI가 분석할 리포트 원문...")
        
        # db가 연결되지 않았다면, 학습 버튼은 비활성화됩니다.
        run_analysis = st.button("🚀 분석 실행", use_container_width=True, disabled=(not race_card_text))

        if run_analysis:
            # 1. 학습 데이터 로드 (DB 연결 성공 시에만 작동)
            active_strategies = []
            if db:
                active_strategies = mandatory_pre_analysis_learning(db)
            else:
                st.info("💡 Firebase 연결 실패로 학습 전략은 적용되지 않습니다.")
            
            # 2. Gemini 분석 실행 (Secrets.toml에 키가 있으면 작동)
            gemini_analysis = analyze_report_with_gemini(qualitative_report_text)
            
            # 3. 데이터 파싱 (임시 데이터 사용)
            data = {
                '마번': [1, 2, 3, 4],
                '마명': ['선진발', '경종한리', '가온천희', '인마속도'],
                '기수': ['김철수', '박지민', '이영희', '최민호'],
                '무게(kg)': [57.0, 54.5, 53.0, 55.0]
            }
            df_race_card = pd.DataFrame(data)

            # 4. 최종 DTP 및 켈리 계산
            with st.spinner('🚨 DTP (레드 팀 분석) 프로토콜 적용 중...'):
                df_dtp_result = apply_dtp_protocol(df_race_card, gemini_analysis, active_strategies)
                복승_allocation, 삼복승_allocation = calculate_kelly_allocation(df_dtp_result)

            st.session_state['df_dtp_result'] = df_dtp_result
            st.session_state['allocations'] = {'복승': 복승_allocation, '삼복승': 삼복승_allocation}
            st.session_state['analysis_run'] = True

    with col_main:
        if st.session_state.get('analysis_run', False):
            df_dtp_result = st.session_state['df_dtp_result']
            allocations = st.session_state['allocations']
            
            # Firebase 연결 실패 시 복기 탭을 비활성화/경고 처리합니다.
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
                    if st.button("🏆 적중으로 기록하고 AI 지식 베이스에 저장"):
                        review_data = {'is_hit': True, 'note': f"성공 복기: {df_dtp_result.iloc[0]['마번']}번 마필 적중."}
                        if save_review_data(review_data):
                            st.balloons()
                            st.success("🎉 분석 데이터가 AI 지식 베이스에 성공적으로 저장되었습니다.")
                    
            # --- [3. 전략 연구소] 탭 ---
            with tab_strategy:
                st.subheader("💡 AI 전략 연구소: 필승 규칙 발견")
                if not db:
                    st.error("전략 연구소 비활성화: Firebase 연결이 필요합니다.")
                else:
                    st.info(f"현재 Firebase에 저장된 복기 데이터를 기반으로 {st.session_state.get('active_strategy_count', 0)}개의 학습 전략이 활성화되었습니다. (추후 심층 분석 기능 추가 예정)")
        else:
            with col_main:
                st.info("👆 분석을 시작하려면 왼쪽 컨트롤 패널에 정보를 입력하고 [분석 실행] 버튼을 눌러주세요.")


if __name__ == "__main__":
    # Firebase 연결 성공 여부와 관계 없이 main() 함수는 실행됩니다.
    main()