# app.py
import streamlit as st
from pathlib import Path
import asyncio, threading, time, importlib.util

from streamlit_webrtc import WebRtcMode, webrtc_streamer, RTCConfiguration
from infrastructure.config import RTC_CONFIG, QUEUE_MAXSIZE, SAMPLE_EVERY
from infrastructure.http_client import get_http
from infrastructure.metrics import get_metrics, get_errbuf
from infrastructure.VADBlockAssembler import VADBlockAssembler
from infrastructure.webrtc_handlers import (
    make_video_frame_callback,
    AudioAccumulator,
    make_vad_audio_callback_with_accumulator,
)
from core.data_pipeline import DataPipeline
from core.stream_orchestrator import StreamOrchestrator
from services.dual_audio_service import DualAudioService
from core.session_manager import get_session_manager

# ───────────────────────────────────────────────────────────────
# 세션/상태 초기화
# ───────────────────────────────────────────────────────────────
session_manager = get_session_manager()
http = get_http()
metrics = get_metrics()
errbuf = get_errbuf()

if "mic_on" not in st.session_state: st.session_state.mic_on = True
if "cam_on" not in st.session_state: st.session_state.cam_on = True
if "messages" not in st.session_state:
    st.session_state.messages = [
        ("sys", "Chat (3)"),
        ("user", "안녕하세요!"),
        ("peer", "반가워요. 오늘 기분은 어때요?"),
    ]

if "data_pipeline" not in st.session_state:
    st.session_state.data_pipeline = DataPipeline(QUEUE_MAXSIZE)
if "stop_evt" not in st.session_state:
    st.session_state.stop_evt = asyncio.Event()
if "wired_lifecycle" not in st.session_state:
    st.session_state.wired_lifecycle = False
if "was_playing" not in st.session_state:
    st.session_state.was_playing = False
if "audio_acc" not in st.session_state:
    st.session_state.audio_acc = AudioAccumulator()
if "vad_assembler" not in st.session_state:
    st.session_state.vad_assembler = VADBlockAssembler()
if "vad_thread" not in st.session_state:
    st.session_state.vad_thread = None
if "orchestrator" not in st.session_state:
    st.session_state.orchestrator = None
if "dual_audio_service" not in st.session_state:
    st.session_state.dual_audio_service = DualAudioService()

data_pipeline = st.session_state.data_pipeline

# ───────────────────────────────────────────────────────────────
# UI 테마 / 레이아웃 스타일
# ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="BingBong Call", page_icon="🎥", layout="wide")

DARK = """
<style>
:root{
  --bg:#1f232b; --panel:#2a2f3a; --border:#39d353; --text:#f2f3f5; --muted:#9aa4b2;
  --btn-green:#25a244; --btn-yellow:#f59e0b; --btn-red:#dc2626;
}
.stApp { background: var(--bg); color: var(--text); }
.block-container { padding-top: 0.5rem; padding-bottom: 0.5rem; }
.bb-card { background: var(--panel); border:2px solid var(--border); border-radius:14px;
  position:relative; overflow:hidden; box-shadow:0 6px 20px rgba(0,0,0,.25);}
.bb-top-right { position:absolute; top:8px; right:10px; color:var(--muted);}
.bb-bottom-bar { position:absolute; bottom:8px; left:12px; right:12px;
  display:flex; justify-content:space-between; color:var(--muted);}
.bb-chat-panel { background:var(--panel); border-radius:16px; padding:16px;
  height:calc(100vh - 160px); overflow-y:auto; border:1px solid rgba(255,255,255,.06);}
.bb-msg{margin:10px 0; display:flex; gap:12px;}
.bb-avatar{width:36px;height:36px;border-radius:50%;background:#3b4150;
  display:flex;align-items:center;justify-content:center;}
.bb-bubble{background:#313846;padding:10px 12px;border-radius:12px;max-width:70%;}
.bb-controls { position:fixed; left:50%; transform:translateX(-50%); bottom:20px; display:flex; gap:16px;}
.bb-ctl-btn{width:58px;height:58px;border-radius:50%; border:none; color:white; font-size:20px;}
.bb-ctl-green{background:var(--btn-green);} .bb-ctl-yellow{background:var(--btn-yellow);} .bb-ctl-red{background:var(--btn-red);}
</style>
"""
st.markdown(DARK, unsafe_allow_html=True)

# ───────────────────────────────────────────────────────────────
# 좌/우 레이아웃
# ───────────────────────────────────────────────────────────────
top_l, top_r = st.columns([3,1])
with top_l: st.markdown("### BingBong Call")

left, right = st.columns([7,4], gap="large")

# 좌측 영상 타일
with left:
    img_path = Path("assets/bingbong.png")
    video_cb = make_video_frame_callback(data_pipeline.raw_frame_q, metrics, SAMPLE_EVERY)

    st.markdown('<div class="bb-card" style="height:46vh;">', unsafe_allow_html=True)
    ctx = webrtc_streamer(
        key="remote",
        mode=WebRtcMode.SENDRECV,
        rtc_configuration=RTC_CONFIG,
        media_stream_constraints={"video": st.session_state.cam_on, "audio": st.session_state.mic_on},
        video_frame_callback=video_cb,
        audio_frame_callback=make_vad_audio_callback_with_accumulator(st.session_state.vad_assembler, st.session_state.audio_acc),
        async_processing=True,
    )
    st.markdown('<div class="bb-bottom-bar"><div>Username 2</div><div>🎤</div></div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.write("")
    st.markdown('<div class="bb-card" style="height:26vh;">', unsafe_allow_html=True)
    if img_path.exists(): st.image(str(img_path), use_column_width=True)
    else: st.markdown("<div style='height:100%;display:flex;align-items:center;justify-content:center;color:#9aa4b2;'>👤</div>", unsafe_allow_html=True)
    st.markdown('<div class="bb-bottom-bar"><div>Username 1</div><div>🎤</div></div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# 우측 채팅
with right:
    st.markdown('<div class="bb-chat-panel">', unsafe_allow_html=True)
    for role, text in st.session_state.messages:
        if role=="sys": st.markdown(f"#### {text}"); continue
        avatar = "👤" if role=="user" else "🧑‍💻"
        st.markdown(f"<div class='bb-msg'><div class='bb-avatar'>{avatar}</div><div class='bb-bubble'>{text}</div></div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    with st.form("chat_input", clear_on_submit=True):
        msg = st.text_input("메시지", placeholder="메시지를 입력하세요")
        sent = st.form_submit_button("전송")
        if sent and msg.strip(): st.session_state.messages.append(("user", msg))

# ───────────────────────────────────────────────────────────────
# WebRTC 상태 → orchestrator wiring
# ───────────────────────────────────────────────────────────────
playing_now = ctx.state.playing if ctx else False
was_playing = st.session_state.was_playing
st.session_state.was_playing = playing_now

if playing_now and not st.session_state.wired_lifecycle:
    st.session_state.wired_lifecycle = True
    st.session_state.audio_acc.reset(); st.session_state.vad_assembler.reset()
    st.session_state.stop_evt.clear()
    time.sleep(1.0)

    # 세션 시작
    if session_manager.start_session():
        st.session_state.orchestrator = StreamOrchestrator(
            http_client=http,
            session_id=session_manager.get_session_id(),
            data_pipeline=data_pipeline,
            audio_accumulator=st.session_state.audio_acc,
            metrics=metrics,
            errbuf=errbuf,
            dual_audio_service=st.session_state.dual_audio_service,
        )
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(st.session_state.orchestrator.start())
        loop.close()
        st.success("Streaming… 프레임과 오디오를 처리 중")

elif not playing_now and was_playing and st.session_state.wired_lifecycle:
    st.session_state.stop_evt.set()
    if st.session_state.orchestrator:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(st.session_state.orchestrator.stop())
        loop.close()
    st.session_state.wired_lifecycle = False
    st.info("⏸️ WebRTC 연결이 종료됨")

# ───────────────────────────────────────────────────────────────
# 하단 컨트롤 버튼
# ───────────────────────────────────────────────────────────────
c1,c2,c3 = st.columns(3)
with c2:
    st.markdown('<div class="bb-controls">', unsafe_allow_html=True)
    colg, coly, colr = st.columns(3)
    with colg:
        if st.button("🎤", key="mic_btn"): st.session_state.mic_on = not st.session_state.mic_on; st.rerun()
    with coly:
        if st.button("📷", key="cam_btn"): st.session_state.cam_on = not st.session_state.cam_on; st.rerun()
    with colr:
        if st.button("📞", key="hang_btn"): st.session_state.stop_evt.set(); st.session_state.cam_on=False; st.session_state.mic_on=False; st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)