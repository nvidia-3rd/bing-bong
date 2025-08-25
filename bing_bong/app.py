import streamlit as st
from streamlit_webrtc import webrtc_streamer
import av
import cv2
import io
import os
import pandas as pd
from openai import OpenAI
from dotenv import load_dotenv
from ai_doll_module import AIDoll  # 기존 모듈 임포트

# -----------------------------
# 환경변수
# -----------------------------
load_dotenv("env.txt")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

st.title("🎥 Video + 🔊 OpenAI TTS Demo")

# -----------------------------
# 영상 처리 부분
# -----------------------------
class VideoProcessor:
    def __init__(self) -> None:
        self.threshold1 = 100
        self.threshold2 = 200

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        img = cv2.cvtColor(cv2.Canny(img, self.threshold1, self.threshold2), cv2.COLOR_GRAY2BGR)
        return av.VideoFrame.from_ndarray(img, format="bgr24")

ctx = webrtc_streamer(
    key="example",
    video_processor_factory=VideoProcessor,
    rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
)
if ctx.video_processor:
    ctx.video_processor.threshold1 = st.slider("Threshold1", 0, 1000, 100, 1)
    ctx.video_processor.threshold2 = st.slider("Threshold2", 0, 1000, 200, 1)

# -----------------------------
# AICharacter 인스턴스 + 세션 유지
# -----------------------------
if "ai_dolls" not in st.session_state:
    st.session_state.ai_dolls = {}  # username별 AICharacter 인스턴스 저장

# -----------------------------
# 유저 선택 및 추가
# -----------------------------
st.sidebar.subheader("유저 관리")
existing_users = list(st.session_state.ai_dolls.keys())
new_user = st.sidebar.text_input("새 유저 이름 추가")
if new_user.strip() and new_user not in existing_users:
    st.session_state.ai_dolls[new_user] = AIDoll()
    existing_users.append(new_user)

selected_user = st.sidebar.selectbox("대화할 유저 선택", existing_users) if existing_users else None
if selected_user:
    ai_doll = st.session_state.ai_dolls[selected_user]

# -----------------------------
# 오디오(TTS) + AI 응답 부분
# -----------------------------
st.markdown("---")
st.subheader("AI 인형과 대화 (TTS)")

with st.form(key="ai_chat_form"):
    text_input = st.text_area("AI에게 말할 텍스트 입력", "테스트 메시지: 영상과 오디오가 함께 동작합니다.")
    sentiment_input = st.selectbox("감정 선택", ["Neutral", "Happy", "Sad", "Angry", "Disgust", "Surprise", "Fear"])
    submit_button = st.form_submit_button("실행")

if submit_button and selected_user and text_input.strip():
    ai_response = ai_doll.handle_user_input(
        username=selected_user,
        starttime=pd.Timestamp.now().isoformat(),
        text=text_input,
        sentiment=sentiment_input
    )

    tts_response = client.audio.speech.create(
        model="gpt-4o-mini-tts",
        voice="alloy",
        input=ai_response
    )
    st.audio(io.BytesIO(tts_response.read()), format="audio/mp3")
    st.text_area("AI 응답", ai_response, height=150)
