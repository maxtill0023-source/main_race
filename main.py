import streamlit as st
import pandas as pd
import re
from io import BytesIO
import PyPDF2

# ----------------------
# Streamlit Horse Analyzer - Complete
# ----------------------

st.set_page_config(page_title="가치 기반 경마 분석기 - Complete", layout="wide")
st.title("가치 기반 경마 분석기 — 완전 자동화 버전 🐎")

# ----------------------
# PDF 텍스트 추출 (수정 완료: 파일 경로 및 PyPDF2 처리 안정화)
# ----------------------

def extract_text_from_pdf(file_like):
    """file_like: 파일 경로(str) 또는 파일 객체(BytesIO/UploadedFile) 지원"""
    reader = None
    try:
        if isinstance(file_like, str):
            # 파일 경로인 경우, with 문을 사용하여 자동으로 파일을 닫습니다.
            with open(file_like, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
        else:
            # BytesIO 또는 UploadedFile인 경우
            reader = PyPDF2.PdfReader(file_like)
    except FileNotFoundError:
        st.error(f"PDF 파일을 찾을 수 없습니다: {file_like}")
        return ""
    except Exception as e:
        st.error(f"PDF 열기 오류: {e}")
        return ""

    if reader is None:
        return ""

    text = ""
    try:
        for page in reader.pages:
            # None 반환 시 빈 문자열로 안전하게 처리
            text += (page.extract_text() or "") + "\n"
    except Exception as e:
        st.error(f"PDF 텍스트 추출 중 오류: {e}")
        return ""
        
    return text.strip()

# ----------------------
# 파싱: 무게 토큰 역추적 방식 (정규식 보완)
# ----------------------

def parse_race_pdf_text(text):
    if not text:
        return pd.DataFrame()

    # 공백 정규화
    norm = re.sub(r"[ \t]+", " ", text)
    # 무게 토큰 검색: 5x.x 형태 뒤에 공백, 괄호 등이 오는 경우를 포괄적으로 검색
    weight_iter = list(re.finditer(r"([0-9]{2}\.[0-9])(?:[\s\)\(]|$)", norm))

    horses = []
    for w in weight_iter:
        weight = float(w.group(1))
        pos = w.start()
        head = norm[max(0, pos-140):pos]

        mlist = list(re.finditer(r"(\d+)\s+([가-힣A-Za-z0-9\u00B7\-\s]{2,40})", head))
        if not mlist:
            continue
        m = mlist[-1]
        try:
            num = int(m.group(1))
        except Exception:
            continue
        name = m.group(2).strip()
        name = re.sub(r"\s+", "", name)

        tail = norm[pos: pos+250]
        jockey = ""
        age = ""
        gender = ""
        color = ""

        # 기수, 나이, 성별, 모색 정보를 찾는 정규식 (복잡하여 그대로 유지)
        jm = re.search(r"([가-힣]{2,4})\s*([0-9]{1,2})세\s*\(([0-9]{2}\.[0-9]{2}\.[0-9]{2})\)\s*(암|수)?\s*([가-힣]{1,3})?", tail)
        if jm:
            jockey = jm.group(1) or ""
            age = jm.group(2) or ""
            gender = jm.group(4) or ""
            color = jm.group(5) or ""
        else:
            jm2 = re.search(r"([가-힣]{2,4})\s*([0-9]{1,2})세", tail)
            if jm2:
                jockey = jm2.group(1)
                age = jm2.group(2)
                gm = re.search(r"\b(암|수)\b", tail)
                if gm:
                    gender = gm.group(1)
                cm = re.search(r"\b(갈|밤|회|백|초|흑)\b", tail)
                if cm:
                    color = cm.group(1)
            else:
                small = re.search(r"\b([가-힣]{2,4})\b", tail)
                if small:
                    tok = small.group(1)
                    if tok not in ["출전","조교사","통산","과거","최근5회","조교","마주"]:
                        jockey = tok

        horses.append({
            "마번": num,
            "마명": name,
            "기수": jockey,
            "나이": age,
            "성별": gender,
            "모색": color,
            "무게(kg)": weight
        })

    df = pd.DataFrame(horses)
    if df.empty:
        return df
    df = df.drop_duplicates(subset="마번", keep="first").sort_values(by="마번").reset_index(drop=True)
    return df

# ----------------------
# 분석 로직 (간단화)
# ----------------------

def apply_dtp_protocol(df_horse, track_condition, active_strategies=None):
    if df_horse is None or df_horse.empty:
        return pd.DataFrame()
    if active_strategies is None:
        active_strategies = []

    dtp_results = []
    base_risk = 0
    if track_condition == "습함":
        base_risk = 1
    elif track_condition == "불량":
        base_risk = 2

    for _, horse in df_horse.iterrows():
        risk_count = base_risk
        notes = []
        try:
            weight = float(horse.get('무게(kg)', 0) or 0)
        except Exception:
            weight = 0.0
        try:
            num = int(horse.get('마번', 0) or 0)
        except Exception:
            num = 0

        if num in [1,3] or weight > 56.0:
            if num % 2 == 0:
                risk_count += 1
                notes.append('짝수 마번 정적 리스크 +1')
            if "PROTOCOL_5" in active_strategies and weight >= 57.0:
                risk_count += 1
                notes.append('학습: 고중량 보수적 평가 +1')

        grade = 'A그룹 (유지)' if risk_count < 3 else 'B그룹 (강등)'
        dtp_results.append({
            '마번': num,
            '마명': horse.get('마명',''),
            'DTP 적용 등급': grade,
            'DTP 리스크 점수': risk_count,
            'DTP 분석 노트': '; '.join(notes)
        })

    return pd.DataFrame(dtp_results)


def calculate_kelly_allocation(df_analysis):
    if df_analysis is None or df_analysis.empty:
        return [{'name':'분석 불가','percentage':100.0}], [{'name':'분석 불가','percentage':100.0}]
    df = df_analysis.copy()
    
    try:
        df['DTP 리스크 점수'] = pd.to_numeric(df['DTP 리스크 점수'], errors='coerce')
        df = df.dropna(subset=['DTP 리스크 점수'])
    except:
        return [{'name':'점수 계산 오류','percentage':100.0}], [{'name':'점수 계산 오류','percentage':100.0}]

    df['AI_Score'] = 100 - (df['DTP 리스크 점수'] * 10)
    top = df.sort_values(by=['AI_Score','마번'], ascending=[False, True]).head(4)
    top_n = top['마번'].tolist()
    names = top.set_index('마번')['마명'].to_dict()

    def info(lst):
        return ' - '.join([f"{n}({names.get(n,'?')})" for n in lst])

    if len(top_n) >= 4:
        n1,n2,n3,n4 = top_n[0],top_n[1],top_n[2],top_n[3]
        bok = [ {'name':f"{info([n1,n2])} (핵심)", 'percentage':40.0},
                {'name':f"{info([n1,n3])} (방어)", 'percentage':25.0},
                {'name':f"{info([n2,n3])} (부축)", 'percentage':15.0},
                {'name':f"{info([n1,n4])} (복병)", 'percentage':10.0},
                {'name':f"{info([n2,n4])} (복병)", 'percentage':10.0} ]
    elif len(top_n) == 3:
        n1,n2,n3 = top_n
        bok = [ {'name':f"{info([n1,n2])} (핵심)", 'percentage':50.0},
                {'name':f"{info([n1,n3])} (방어)", 'percentage':30.0},
                {'name':f"{info([n2,n3])} (부축)", 'percentage':20.0} ]
    else:
        bok = [{'name':'분석 불가 (유력 후보 부족)','percentage':100.0}]

    if len(top_n) >= 3:
        if len(top_n) >= 4:
            n1,n2,n3,n4 = top_n[0],top_n[1],top_n[2],top_n[3]
            box = [ {'name':f"BOX ({info([n1,n2,n3])}) (핵심)", 'percentage':70.0},
                    {'name':f"BOX ({info([n1,n2,n4])}) (방어)", 'percentage':30.0} ]
        else:
            n1,n2,n3 = top_n
            box = [ {'name':f"BOX ({info([n1,n2,n3])}) (핵심)", 'percentage':100.0} ]
    else:
        box = [{'name':'분석 불가 (유력 후보 부족)','percentage':100.0}]

    return bok, box

# ----------------------
# UI 및 상태 관리 (수정 완료: st.session_state 사용)
# ----------------------

# 세션 상태 초기화: DataFrame을 저장할 키 설정
if 'df_parsed' not in st.session_state:
    st.session_state['df_parsed'] = pd.DataFrame()
if 'df_dtp' not in st.session_state:
    st.session_state['df_dtp'] = pd.DataFrame()

st.sidebar.header('입력 설정')
use_sample = st.sidebar.checkbox('샘플 PDF 사용 (j_run_hr_251121_01.pdf를 스크립트 폴더에 두세요)', value=False)
uploaded_file = st.sidebar.file_uploader('출전표 PDF 업로드', type=['pdf'])

race_text = ""
source = '업로드 필요'

if use_sample and not uploaded_file:
    sample_path = "j_run_hr_251121_01.pdf" 
    race_text = extract_text_from_pdf(sample_path) 
    source = sample_path
elif uploaded_file is not None:
    race_text = extract_text_from_pdf(BytesIO(uploaded_file.read()))
    source = uploaded_file.name

st.markdown(f"**소스:** {source}")

st.subheader('출전표 추출 텍스트 (편집 가능)')
# 텍스트 에어리어의 내용을 세션 상태에 저장하여 버튼 클릭 시 접근 가능하게 함
st.session_state.txt_input = st.text_area('추출 텍스트', 
                                          value=race_text if race_text else '', 
                                          height=240, 
                                          key='current_text')


# '파싱 -> 표 생성' 버튼 처리 함수
def handle_parsing():
    # 텍스트 에어리어의 최신 내용으로 파싱 시도
    df = parse_race_pdf_text(st.session_state.current_text)
    if df.empty:
        st.warning('파싱 결과가 없습니다. 추출된 텍스트를 확인하거나 다른 PDF를 업로드 해주세요.')
        st.session_state['df_parsed'] = pd.DataFrame()
    else:
        st.success(f'파싱 완료: {len(df)}마리')
        st.session_state['df_parsed'] = df

st.button('파싱 -> 표 생성', on_click=handle_parsing)


# 파싱된 DataFrame이 세션 상태에 있을 경우에만 데이터 편집기 및 분석 버튼 표시
if not st.session_state['df_parsed'].empty:
    st.markdown("### 📊 파싱 결과 (편집 가능)")
    # 편집된 결과를 'df_edited' 키에 저장하며, 자동으로 세션 상태 관리
    edited_df = st.data_editor(st.session_state['df_parsed'], num_rows='dynamic', key='df_edited')
    
    st.markdown("---")
    st.markdown("### 🐴 분석 설정")
    track_condition = st.selectbox('주로 상태', ['양호','다소 습함','습함','불량','건조'], key='track_select')
    
    # DTP 분석 및 포트폴리오 생성 버튼
    if st.button('DTP 분석 및 포트폴리오 생성'):
        # 사용자가 편집한 edited_df를 분석 함수에 전달
        df_dtp = apply_dtp_protocol(edited_df, track_condition)
        
        # 분석 결과를 세션 상태에 저장
        st.session_state['df_dtp'] = df_dtp
        
        bok, box = calculate_kelly_allocation(df_dtp)
        
        st.header('DTP 결과')
        st.dataframe(df_dtp, use_container_width=True)

        st.header('추천 포트폴리오')
        c1, c2 = st.columns(2)
        with c1:
            st.subheader('복승식')
            for it in bok:
                st.markdown(f"**{it['name']}** — {it['percentage']}%")
        with c2:
            st.subheader('삼복승식')
            for it in box:
                st.markdown(f"**{it['name']}** — {it['percentage']}%")

    # CSV 다운로드 버튼 (DTP 분석이 완료된 후에만 표시)
    if not st.session_state['df_dtp'].empty:
        st.markdown("---")
        if st.button('최종 결과 CSV 다운로드'):
            # 편집된 데이터와 DTP 분석 결과를 병합
            final_df = edited_df.merge(st.session_state['df_dtp'], on=['마번', '마명'], how='left')
            csv = final_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button('CSV 다운로드', csv, file_name='analyzed_horses.csv', mime='text/csv')

st.caption('자동 파서는 PDF의 무게 토큰을 기준으로 앞뒤 텍스트를 분석합니다. 일부 항목은 수동 편집이 필요할 수 있습니다.')