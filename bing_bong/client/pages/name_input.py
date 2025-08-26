import streamlit as st

def render_name_input():
    """이름 입력 컴포넌트를 렌더링합니다."""
    
    # 이름 입력 기능
    st.markdown("---")
    st.markdown("### 👤 이름을 입력해주세요")
    
    # 세션 상태에서 이름 가져오기
    if "user_name" not in st.session_state:
        st.session_state.user_name = ""
    
    # 이름 입력 필드
    name_input = st.text_input(
        "이름",
        value=st.session_state.user_name,
        placeholder="여기에 이름을 입력하세요",
        key="name_input"
    )
    
    # 이름 저장
    if name_input and name_input != st.session_state.user_name:
        st.session_state.user_name = name_input
        st.success(f"👋 안녕하세요, {name_input}님!")
    
    return st.session_state.user_name

def render_conversation_button(user_name):
    """대화 버튼을 렌더링합니다."""
    
    if user_name:
        st.markdown(f"### 🎯 {user_name}님과의 대화")
        
        # 대화 페이지로 이동 버튼
        if st.button("빙봉과 이야기 나눠볼까요?", use_container_width=False):
            # 멀티페이지 구조에서는 쿼리 파라미터로 이동 유도
            st.switch_page("pages/talk_to_bing_bong.py")
        return True
    else:
        st.warning("⚠️ 이름을 입력해주세요!")
        st.info("이름을 입력하면 대화를 시작할 수 있습니다.")
        return False



def set_user_name(name):
    """사용자 이름을 설정합니다."""
    st.session_state.user_name = name

    # 이름 입력 모달
    st.markdown("---")
    st.markdown("### 🚀 시작하기")
    
    # 세션 상태에서 이름 가져오기
    if "user_name" not in st.session_state:
        st.session_state.user_name = ""
    
    # 이름 입력 필드
    name_input = st.text_input(
        "👤 이름을 입력해주세요",
        value=st.session_state.user_name,
        placeholder="여기에 이름을 입력하세요",
        key="name_input"
    )
    
    # 이름 저장 및 검증
    if name_input and name_input != st.session_state.user_name:
        st.session_state.user_name = name_input
        st.success(f"👋 안녕하세요, {name_input}님!")
    
    # 이름이 입력된 경우에만 대화 버튼 표시
    if st.session_state.user_name:
        st.markdown(f"### 🎯 {st.session_state.user_name}님과의 대화")
        
        # 대화 페이지로 이동 버튼
        if st.button("🎥 Talk to Bing Bong 시작하기", type="primary", use_container_width=True):
            # 이름이 설정되었는지 확인
            if st.session_state.user_name.strip():
                st.success(f"🎉 {st.session_state.user_name}님, Bing Bong과의 대화를 시작합니다!")
                st.info("📁 pages/talk_to_bing_bong.py 파일을 열어주세요.")
                
                # 이름 정보를 세션에 저장 (다른 페이지에서 사용)
                st.session_state.user_name = st.session_state.user_name.strip()
                
                # 페이지 이동 안내
                st.markdown("""
                ### 🔗 페이지 이동 안내
                **1단계**: `pages/talk_to_bing_bong.py` 파일을 열어주세요
                **2단계**: Bing Bong과 실시간 대화를 시작하세요!
                
                ### 📱 사용 방법
                1. **카메라 권한 허용** (웹캠, 마이크)
                2. **START 버튼** 클릭하여 스트리밍 시작
                3. **음성 입력** 또는 **텍스트 입력**으로 대화
                4. **STOP 버튼** 클릭하여 AI 응답 받기
                """)
            else:
                st.error("⚠️ 이름을 입력해주세요!")
    else:
        st.warning("⚠️ 이름을 입력해주세요!")
        st.info("이름을 입력하면 Bing Bong과의 대화를 시작할 수 있습니다.")
        
        # 이름 입력 예시
        st.markdown("""
        ### 💡 이름 입력 예시
        - **실명**: 홍길동, 김철수, 이영희
        - **닉네임**: 빙봉러버, AI친구, 대화왕
        - **영어**: John, Sarah, Mike
        """)
