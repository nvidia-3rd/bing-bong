# webrtc_handlers.py
import time
import queue
import io
import wave
import av
import cv2
import asyncio
import numpy as np
from typing import Callable, Optional, Any
from streamlit_webrtc import AudioProcessorBase

from metrics import Metrics
from http_client import post_json
from config import JPEG_QUALITY, SAMPLE_EVERY

def make_video_frame_callback(
    q: "queue.Queue[bytes]",
    metrics: Metrics,
    sample_every: int = SAMPLE_EVERY,
    jpeg_quality: int = JPEG_QUALITY,
) -> Callable[[av.VideoFrame], av.VideoFrame]:
    counter = {"i": 0}

    def video_frame_callback(frame: av.VideoFrame) -> av.VideoFrame:
        # FPS 측정용 틱
        try:
            metrics.tick_frame()
        except Exception:
            pass
        counter["i"] += 1
        if counter["i"] % sample_every == 0:
            try:
                img = frame.to_ndarray(format="bgr24")
                ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
                if ok:
                    if q.full():
                        try: q.get_nowait()
                        except Exception: pass
                    q.put_nowait(buf.tobytes())
                    metrics.inc_enq()
            except Exception:
                # 변환 실패는 드롭
                pass
        return frame
    return video_frame_callback
    """
    streamlit-webrtc 컨텍스트에서 데이터채널이 열리면 /ingest/data로 포워딩.
    """
    channels = getattr(ctx.state, "data_channels", None)
    if not (ctx.state.playing and channels):
        return False

    dc = channels[0]  # 필요시 여러 채널 순회

    async def post_data(label: str, message: str):
        http = http_client_getter()
        payload = {
            "session_id": session_id,
            "label": label,
            "data": message if isinstance(message, str) else str(message),
            "ts": time.time(),
        }
        try:
            await post_json(http, "/ingest/data", payload)
        except Exception:
            # 백그라운드: 조용히 무시(원하면 로거 사용)
            pass

    # 중복 바인딩 방지용 플래그
    if getattr(ctx.state, "_dc_wired", False):
        return True

    @dc.on("message")
    def _on_msg(message):
        asyncio.create_task(post_data(dc.label or "default", message))

    ctx.state._dc_wired = True
    return True


class AudioAccumulator:
    """
    세션 동안 수신된 오디오 프레임을 PCM16으로 누적하여
    stop 시점에 단일 WAV 바이트로 직렬화하는 헬퍼.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._sample_rate: Optional[int] = None
        self._channels: Optional[int] = None
        self._frames: int = 0
        self._samples: int = 0

    def reset(self) -> None:
        self._chunks.clear()
        self._sample_rate = None
        self._channels = None
        self._frames = 0
        self._samples = 0

    def add_frame(self, frame: av.AudioFrame) -> None:
        # ndarray: (channels, samples)
        arr = frame.to_ndarray()
        if arr.dtype == np.float32 or arr.dtype == np.float64:
            # [-1.0, 1.0] -> int16
            arr = np.clip(arr, -1.0, 1.0)
            arr = (arr * 32767.0).astype(np.int16)
        elif arr.dtype != np.int16:
            # 기타 포맷은 일단 int16로 다운캐스트
            arr = arr.astype(np.int16, copy=False)

        # (channels, samples) -> (samples, channels) -> interleaved bytes
        if arr.ndim == 1:
            channels = 1
            interleaved = arr.tobytes()
        else:
            channels = arr.shape[0]
            interleaved = arr.T.reshape(-1, channels).astype(np.int16, copy=False).tobytes()

        # 통계 업데이트
        samples = int(interleaved.__len__() // (2 * channels))  # 2 bytes per sample per channel
        self._frames += 1
        self._samples += samples

        if self._sample_rate is None:
            self._sample_rate = frame.sample_rate
        if self._channels is None:
            self._channels = channels

        self._chunks.append(interleaved)

    def build_wav_bytes(self) -> Optional[bytes]:
        if not self._chunks or self._sample_rate is None or self._channels is None:
            return None
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(int(self._channels))
            wf.setsampwidth(2)  # PCM16
            wf.setframerate(int(self._sample_rate))
            wf.writeframes(b"".join(self._chunks))
        return buf.getvalue()

    def stats(self) -> dict:
        return {
            "frames": self._frames,
            "samples": self._samples,
            "bytes": sum(len(c) for c in self._chunks),
            "sr": self._sample_rate,
            "ch": self._channels,
        }


class AudioProcessor(AudioProcessorBase):
    def __init__(self, acc: AudioAccumulator) -> None:
        self._acc = acc

    def recv(self, frame: av.AudioFrame) -> av.AudioFrame:
        try:
            self._acc.add_frame(frame)
        except Exception:
            pass
        return frame


def make_audio_processor_factory(acc: AudioAccumulator):
    def _factory() -> AudioProcessor:
        return AudioProcessor(acc)
    return _factory


def make_audio_frame_callback(acc: AudioAccumulator) -> Callable[[av.AudioFrame], av.AudioFrame]:
    def audio_frame_callback(frame: av.AudioFrame) -> av.AudioFrame:
        try:
            acc.add_frame(frame)
        except Exception:
            # 무음 처리: 콜백은 반드시 프레임을 반환해야 함
            pass
        return frame
    return audio_frame_callback