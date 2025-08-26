# config.py
import os
from streamlit_webrtc import RTCConfiguration

# API 설정
API_BASE = "http://localhost:8000"  # 포트 8000으로 복원

# WebRTC 설정 개선 - 웹캠 연결 안정성 향상
RTC_CONFIG = RTCConfiguration({
    "iceServers": [
        {"urls": ["stun:stun.l.google.com:19302"]},
        {"urls": ["stun:stun1.l.google.com:19302"]},
        {"urls": ["stun:stun2.l.google.com:19302"]}
    ],
    "iceCandidatePoolSize": 10,
    "bundlePolicy": "max-bundle",
    "rtcpMuxPolicy": "require"
})

SAMPLE_EVERY = 10      # 10프레임마다 1장 업로드
JPEG_QUALITY = 80      # JPEG 인코딩 품질
QUEUE_MAXSIZE = 8      # 프레임 업로드 큐 크기
HEARTBEAT_SEC = 5.0    # 하트비트 주기(초)