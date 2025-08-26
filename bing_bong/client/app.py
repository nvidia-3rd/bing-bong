# app.py
import streamlit as st
from pathlib import Path

st.set_page_config(
    page_title="BingBong",
    page_icon="🟣",
    layout="wide"
)

# CSS 로드
with open("styles.css", "r", encoding="utf-8") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# 상단 네비(우측 '사용방법' 링크 대체)
st.markdown(
    """
    <div class="bb-topbar">
      <div class="bb-brand">BingBong</div>
      <div class="bb-menu"><a href="/?page=사용방법" target="_self">사용방법</a></div>
    </div>
    """,
    unsafe_allow_html=True
)

# 좌/우 2열 레이아웃
# ✅ 교체
left, right = st.columns([5,7])

with left:
    st.markdown('<div class="bb-title">BingBong</div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="bb-subtitle">내 표정을 인식하는 대화봇.</div>
        <div class="bb-body">“당신의 표정을 인식해 대화를 이어갑니다”</div>
        """,
        unsafe_allow_html=True
    )

    # 대화 페이지로 이동 버튼
    if st.button("빙봉과 이야기 나눠볼까요?", use_container_width=False):
        # 멀티페이지 구조에서는 쿼리 파라미터로 이동 유도
        st.switch_page("pages/talk_to_bing_bong.py")


with right:
    img_path = Path("assets/bingbong.png")
    if img_path.exists():
        st.image(str(img_path), use_column_width=True)
    else:
        st.info("assets/bingbong.png 파일을 넣으면 우측에 이미지가 보입니다.")

# 하단 푸터
st.markdown(
    """
    <div class="bb-footer">
      <div>2025</div>
      <div>Team BingBong<br/>표정 인식 공감 봇</div>
      <div class="bb-footmark">BingBong</div>
    </div>
    """,
    unsafe_allow_html=True
)