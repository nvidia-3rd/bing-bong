# app/models/webrtc.py
from __future__ import annotations
from typing import Any, Dict, Optional, Literal
from pydantic import BaseModel, Field, field_validator, ConfigDict
from pydantic.functional_validators import model_validator
from typing_extensions import Annotated
from pydantic import StringConstraints
from aiortc import RTCSessionDescription
import re

# 문자열 제약 (offer|pranswer|rollback)
SDPType = Annotated[str, StringConstraints(pattern=r"^(offer|pranswer|rollback)$")]
AnswerType = Annotated[str, StringConstraints(pattern=r"^(answer|pranswer)$")]

class VideoMeta(BaseModel):
    width: Optional[int] = Field(default=None, ge=1)
    height: Optional[int] = Field(default=None, ge=1)
    frameRate: Optional[int] = Field(default=None, ge=1)
    codec: Optional[str] = None  # e.g., 'VP8', 'H264'
    facingMode: Optional[Literal["user", "environment"]] = None

class AudioMeta(BaseModel):
    sampleRate: Optional[int] = Field(default=None, ge=8000)
    channelCount: Optional[int] = Field(default=1, ge=1, le=8)
    codec: Optional[str] = None  # e.g., 'opus'
    echoCancellation: Optional[bool] = None
    noiseSuppression: Optional[bool] = None
    autoGainControl: Optional[bool] = None
    bitrate: Optional[int] = Field(default=None, ge=6000)  # bps

class MediaData(BaseModel):
    timestamp: Optional[int] = None
    streamType: Optional[Literal["realtime_webcam", "screen_share", "custom"]] = "realtime_webcam"
    video: Optional[VideoMeta] = None
    audio: Optional[AudioMeta] = None
    quality: Optional[Literal["low", "medium", "high"]] = "high"
    continuous: Optional[bool] = True

class SessionInfo(BaseModel):
    userAgent: Optional[str] = None
    platform: Optional[str] = None
    connectionId: Optional[str] = None
    sessionStart: Optional[int] = None

class ConnectionStatus(BaseModel):
    iceState: Optional[str] = None
    pcState: Optional[str] = None

class SDPOffer(BaseModel):
    model_config = ConfigDict(extra="ignore")  # 예기치 않은 필드 무시(안전장치)

    sdp: str
    type: SDPType = "offer"
    mediaData: Optional[MediaData] = None
    sessionInfo: Optional[SessionInfo] = None
    videoFrame: Optional[Dict[str, Any]] = None
    connectionStatus: Optional[ConnectionStatus] = None

    # (선택) 하위호환: 과거 payload에서 루트 audio를 허용하려면 주석 해제
    # audio: Optional[AudioMeta] = Field(default=None, description="(Deprecated) Use mediaData.audio")

    def to_rtc(self) -> RTCSessionDescription:
        return RTCSessionDescription(sdp=self.sdp, type=self.type)

    # Pydantic v2: after 모델 검증자
    @model_validator(mode="after")
    def _hoist_legacy_audio(self):
        # 과거 루트 audio를 mediaData.audio로 승격 (필요 시만 활성화)
        if hasattr(self, "audio"):
            if self.audio:
                if self.mediaData is None:
                    self.mediaData = MediaData()
                if self.mediaData.audio is None:
                    self.mediaData.audio = self.audio
                # delattr(self, "audio")  # 원한다면 제거
        return self

    def infer_codecs_from_sdp(self) -> "SDPOffer":
        """
        SDP에서 오디오/비디오 코덱, 샘플레이트, 채널수 유추하여 mediaData에 채움.
        명시값이 이미 있으면 건드리지 않음.
        """
        if not self.sdp:
            return self
        sdp = self.sdp

        if self.mediaData is None:
            self.mediaData = MediaData()

        # ---- 오디오: m=audio 섹션 우선 탐색, opus 우선 선택 ----
        audio_section = self._extract_media_section(sdp, "audio")
        if audio_section:
            # 예: a=rtpmap:111 opus/48000/2
            candidates = re.findall(r"a=rtpmap:(\d+)\s+([A-Za-z0-9]+)/(\d+)(?:/(\d+))?", audio_section)
            chosen = None
            # opus 우선, 없으면 첫 항목
            for c in candidates:
                if c[1].lower() == "opus":
                    chosen = c; break
            if not chosen and candidates:
                chosen = candidates[0]
            if chosen:
                _pt, acodec, asr, ach = chosen
                asr_i = int(asr)
                ach_i = int(ach) if ach else 1
                if self.mediaData.audio is None:
                    self.mediaData.audio = AudioMeta()
                if self.mediaData.audio.codec is None:
                    self.mediaData.audio.codec = acodec.lower()
                if self.mediaData.audio.sampleRate is None:
                    self.mediaData.audio.sampleRate = asr_i
                if self.mediaData.audio.channelCount is None:
                    self.mediaData.audio.channelCount = ach_i

        # ---- 비디오: m=video 섹션에서 rtpmap ----
        video_section = self._extract_media_section(sdp, "video")
        if video_section:
            # 예: a=rtpmap:96 VP8/90000  또는 H264/90000
            vm = re.search(r"a=rtpmap:\d+\s+([A-Za-z0-9]+)/(\d+)", video_section)
            if vm:
                vcodec, _clock = vm.groups()
                if self.mediaData.video is None:
                    self.mediaData.video = VideoMeta()
                if self.mediaData.video.codec is None:
                    self.mediaData.video.codec = vcodec.upper()

        return self

    @staticmethod
    def _extract_media_section(sdp: str, kind: Literal["audio", "video"]) -> Optional[str]:
        """
        m=<kind> ... 와 다음 m= 섹션 사이의 블록을 추출
        """
        # DOTALL로 줄바꿈 포함 매칭
        m = re.search(rf"(m={kind}[\s\S]*?)(?=^m=|\Z)", sdp, flags=re.MULTILINE)
        return m.group(1) if m else None

class SDPAnswer(BaseModel):
    sdp: str
    type: AnswerType = "answer"
    status: str = "ok"
    connection_id: Optional[str] = None

    @classmethod
    def from_rtc(cls, desc: RTCSessionDescription, connection_id: Optional[str] = None) -> "SDPAnswer":
        return cls(sdp=desc.sdp, type=str(desc.type), status="ok", connection_id=connection_id)

class ConnectionInfo(BaseModel):
    connection_id: str
    state: str
    created_at: float
    meta: Dict[str, Any] = Field(default_factory=dict)

class WebRTCResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    timestamp: float