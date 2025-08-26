import streamlit as st


def render_main(session_id: str, metrics, errbuf, frame_q, data_pipeline) -> None:
    # 헤더
    st.title("🎥 WebRTC Proxy (Streamlit → FastAPI)")
    
    # WebRTC 스트리머 안내
    st.write("**📹 카메라 및 마이크 권한을 허용한 후 START 버튼을 클릭하세요:**")
    
    # WebRTC 스트리머 상태 표시 (렌더링은 app.py에서)
    if hasattr(st.session_state, 'webrtc_ctx') and st.session_state.webrtc_ctx:
        st.success("✅ WebRTC 스트리머가 활성화되었습니다.")
        st.info("🎥 WebRTC 스트리머는 아래에서 렌더링됩니다.")
    else:
        st.warning("⚠️ WebRTC 스트리머가 초기화되지 않았습니다. 페이지를 새로고침해주세요.")

    # 메트릭 카드
    st.info(f"Session ID: {session_id}")
    snap = metrics.snapshot()
    c1, c2, c3 = st.columns(3)
    c1.metric("Queued", snap["enq"])  # 업로드 대기 큐에 enqueue된 프레임 수
    c2.metric("Uploaded", snap["deq"])  # 성공적으로 업로드된 프레임 수
    c3.metric("Q size", frame_q.qsize())

    # FPS
    c4, _ = st.columns([1, 2])
    c4.metric("FPS", f"{snap.get('fps', 0.0):.1f}")

    # 오디오 통계
    astats = st.session_state.audio_acc.stats()
    u1, u2, u3 = st.columns(3)
    u1.metric("Aud frames", astats.get("frames", 0))
    u2.metric("Aud samples", astats.get("samples", 0))
    u3.metric("Aud bytes", astats.get("bytes", 0))

    # 수집 상태 캡션
    st.caption(
        f"SR: {astats.get('sr')} CH: {astats.get('ch')}"
    )

    # 최근 오류
    errs = errbuf.take()
    if errs:
        with st.expander("최근 오류", expanded=False):
            for e in errs[:10]:
                st.warning(e)

    # 감정 요약 실시간 표시 (emotion_summary_q → 최근 1개)
    EMOJI = {
        "happy": "😊",
        "sad": "😢",
        "angry": "😠",
        "fear": "😨",
        "surprise": "😮",
        "disgust": "🤢",
        "neutral": "😐",
    }

    emo_placeholder = st.empty()
    last_summary = st.session_state.get("last_emotion_summary")
    
    # data_pipeline에서 감정 요약 큐 드레인
    if data_pipeline and data_pipeline.emotion_summary_q:
        while True:
            try:
                item = data_pipeline.emotion_summary_q.get_nowait()
                last_summary = item
            except Exception:
                break
    
    if last_summary:
        st.session_state.last_emotion_summary = last_summary
        label = str(last_summary.get("label", "neutral"))
        score = float(last_summary.get("score", 0.0))
        emoji = EMOJI.get(label, "😐")
        emo_placeholder.markdown(f"{emoji} 현재 감정: **{label}** ({score:.2f})")

    # 최근 전사 텍스트 표시 (transcript_q → 최근 1개)
    tr_placeholder = st.empty()
    last_txt = st.session_state.get("last_transcript_text")
    
    # data_pipeline에서 전사 큐 드레인
    if data_pipeline and data_pipeline.transcript_q:
        while True:
            try:
                t = data_pipeline.transcript_q.get_nowait()
                last_txt = t
            except Exception:
                break
    
    if last_txt:
        st.session_state.last_transcript_text = last_txt
        tr_placeholder.markdown(f"��️ 전사: {last_txt}")
