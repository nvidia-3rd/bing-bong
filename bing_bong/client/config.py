# config.py
import os
from streamlit_webrtc import RTCConfiguration

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")

RTC_CONFIG = RTCConfiguration(
    {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
)

SAMPLE_EVERY = 10      # 10프레임마다 1장 업로드
JPEG_QUALITY = 80      # JPEG 인코딩 품질
QUEUE_MAXSIZE = 8      # 프레임 업로드 큐 크기
HEARTBEAT_SEC = 5.0    # 하트비트 주기(초)