# app.py
import uuid
import asyncio
import threading
import queue
import streamlit as st
from streamlit_webrtc import WebRtcMode, webrtc_streamer

from config import RTC_CONFIG, QUEUE_MAXSIZE, SAMPLE_EVERY, JPEG_QUALITY
from http_client import get_http
from metrics import get_metrics, get_errbuf
from webrtc_handlers import (
    make_video_frame_callback,
    bind_data_channels,
    AudioAccumulator,
    make_audio_processor_factory,
)
from lifecycle import start_session, stop_session, post_audio
from services.async_exec import ensure_bg_loop, submit_coro
from ui.main import render_main

# 백그라운드 asyncio 실행 루프 준비(서비스 유틸 사용)
ensure_bg_loop()

st.set_page_config(page_title="WebRTC Proxy → FastAPI", layout="centered")

http = get_http()
metrics = get_metrics()
errbuf = get_errbuf()

# 상태 초기화
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "frame_q" not in st.session_state:
    st.session_state.frame_q = queue.Queue(maxsize=QUEUE_MAXSIZE)
if "stop_evt" not in st.session_state:
    st.session_state.stop_evt = asyncio.Event()
if "bg_tasks" not in st.session_state:
    st.session_state.bg_tasks = []
if "wired_lifecycle" not in st.session_state:
    st.session_state.wired_lifecycle = False
if "was_playing" not in st.session_state:
    st.session_state.was_playing = False

session_id = st.session_state.session_id

# WebRTC 컨텍스트
video_cb = make_video_frame_callback(st.session_state.frame_q, metrics, SAMPLE_EVERY, JPEG_QUALITY)

if "audio_acc" not in st.session_state:
    st.session_state.audio_acc = AudioAccumulator()

ctx = webrtc_streamer(
    key="proxy",
    mode=WebRtcMode.SENDRECV,
    rtc_configuration=RTC_CONFIG,
    media_stream_constraints={"video": True, "audio": True},
    video_frame_callback=video_cb,
    audio_processor_factory=make_audio_processor_factory(st.session_state.audio_acc),
    async_processing=True,
)

# 수명주기 콜백
def on_state_change(state: str):
    if state == "connected" and not st.session_state.wired_lifecycle:
        st.session_state.wired_lifecycle = True
        # 새로운 세션 시작 시 오디오 누적기 초기화
        st.session_state.audio_acc.reset()
        submit_coro(start_session(
            http,
            session_id,
            st.session_state.stop_evt,
            st.session_state.bg_tasks,
            st.session_state.frame_q,
            metrics,
            errbuf,
        ))
    elif state in ("failed", "disconnected", "closed") and st.session_state.wired_lifecycle:
        st.session_state.wired_lifecycle = False
        # stop 직전에 오디오 업로드 시도
        wav_bytes = st.session_state.audio_acc.build_wav_bytes()
        st.session_state.audio_acc.reset()
        if wav_bytes:
            submit_coro(post_audio(http, session_id, wav_bytes, errbuf))
        submit_coro(stop_session(
            http,
            session_id,
            st.session_state.stop_evt,
            st.session_state.bg_tasks,
            errbuf,
        ))
        # 데이터채널 재바인딩을 허용하기 위해 플래그 초기화
        if hasattr(ctx.state, "_dc_wired"):
            delattr(ctx.state, "_dc_wired")

ctx.on_connection_state_change = on_state_change

# 데이터채널 바인딩(연결 중일 때만 시도)
bind_data_channels(ctx, session_id, get_http)

# UI 렌더링 위임
render_main(ctx=ctx, session_id=session_id, metrics=metrics, errbuf=errbuf, frame_q=st.session_state.frame_q)

# 테스트용 수동 전송 및 더미 업로드 버튼 제거됨

playing_now = ctx.state.playing
was_playing = st.session_state.was_playing
st.session_state.was_playing = playing_now

if playing_now:
    # playing에 진입했으나 라이프사이클이 아직 안 묶였으면 즉시 시작 보강
    if not st.session_state.wired_lifecycle:
        st.session_state.wired_lifecycle = True
        st.session_state.audio_acc.reset()
        submit_coro(start_session(
            http,
            session_id,
            st.session_state.stop_evt,
            st.session_state.bg_tasks,
            st.session_state.frame_q,
            metrics,
            errbuf,
        ))
    st.success("Streaming… 프레임을 FastAPI로 주기 업로드 중")
else:
    st.write("START 버튼으로 연결을 시작하세요.")

# playing -> False 전이 시, 오디오 업로드 및 세션 정리 수행
if (not playing_now) and was_playing and st.session_state.wired_lifecycle:
    wav_bytes = st.session_state.audio_acc.build_wav_bytes()
    st.session_state.audio_acc.reset()
    if wav_bytes:
        submit_coro(post_audio(http, session_id, wav_bytes, errbuf))
    # 연결 자체는 유지될 수 있으므로 명시적으로 세션 정리
    st.session_state.wired_lifecycle = False
    submit_coro(stop_session(
        http,
        session_id,
        st.session_state.stop_evt,
        st.session_state.bg_tasks,
        errbuf,
    ))
    if hasattr(ctx.state, "_dc_wired"):
        delattr(ctx.state, "_dc_wired")

# AudioProcessor 경로는 별도의 드레인 스레드가 필요 없습니다.