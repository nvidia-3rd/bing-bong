import streamlit as st


def render_main(ctx, session_id: str, metrics, errbuf, frame_q) -> None:
    # 헤더
    st.title("🎥 WebRTC Proxy (Streamlit → FastAPI)")

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
        f"Receiver: {bool(getattr(ctx, 'audio_receiver', None))} / "
        f"SR: {astats.get('sr')} CH: {astats.get('ch')}"
    )

    # 최근 오류
    errs = errbuf.take()
    if errs:
        with st.expander("최근 오류", expanded=False):
            for e in errs[:10]:
                st.warning(e)


