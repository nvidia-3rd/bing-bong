import streamlit as st

# --- 세션에서 이름 체크
if "name" not in st.session_state:
    st.session_state.name = None

# --- 쿼리 파라미터 확인
params = st.experimental_get_query_params()
if not st.session_state.name:
    name_from_url = params.get("username", [None])[0]
    if name_from_url:
        st.session_state.name = name_from_url.strip()
        st.experimental_set_query_params()  # URL 정리

need_name = not st.session_state.name

# --- 모달 스타일
st.markdown("""
<style>
.bb-modal-backdrop {
  position: fixed; inset: 0; background: rgba(0,0,0,.55);
  display: flex; align-items: center; justify-content: center; z-index: 9999;
}
.bb-modal {
  width: min(480px, 92vw);
  background: #1f232b; color: #f2f3f5;
  border-radius: 16px; padding: 22px 18px;
  border: 1px solid rgba(255,255,255,.08);
  box-shadow: 0 20px 60px rgba(0,0,0,.5);
}
.bb-modal h3 { margin: 0 0 12px 0; font-size: 20px; }
.bb-modal p  { margin: 0 0 14px 0; color: #9aa4b2; font-size: 14px; }
.bb-modal form { display: grid; gap: 12px; }
.bb-input {
  padding: 10px 12px; border-radius: 10px; border: 1px solid #39414f;
  background: #2a2f3a; color: #fff; outline: none; font-size: 16px;
}
.bb-btn {
  padding: 10px 14px; border-radius: 10px; border: none; cursor: pointer;
  background: #25a244; color: #fff; font-weight: 700; font-size: 15px;
}
</style>
""", unsafe_allow_html=True)

# --- 모달 띄우기
if need_name:
    st.markdown(f"""
    <div class="bb-modal-backdrop">
      <div class="bb-modal">
        <h3>이름을 입력해주세요</h3>
        <p>대화 중 표시될 이름이에요.</p>
        <form method="get">
          <input class="bb-input" type="text" name="username" placeholder="예: 진수" required autofocus/>
          <button class="bb-btn" type="submit">확인</button>
        </form>
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# --- 이름 세팅 후 UI
st.write(f"👋 반가워요, **{st.session_state.name}**!")