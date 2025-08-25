# video_service.py
import cv2
import av
import asyncio
import queue
from typing import Optional


class VideoService:
    """
    비디오 처리 서비스
    - 프레임 인코딩
    - 프레임 업로드
    - 감정 분석 결과 처리
    """
    
    def __init__(self, jpeg_quality: int = 80):
        self.jpeg_quality = jpeg_quality
    
    async def encode_frame(self, frame: av.VideoFrame) -> Optional[bytes]:
        """비디오 프레임을 JPEG로 인코딩합니다."""
        try:
            img = frame.to_ndarray(format="bgr24")
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
            if ok:
                return buf.tobytes()
        except Exception as e:
            print(f"프레임 인코딩 오류: {e}")
        return None
    
    async def process_frame_pipeline(
        self,
        raw_frame_q: queue.Queue,
        frame_q: queue.Queue,
        stop_evt: asyncio.Event
    ) -> None:
        """
        프레임 파이프라인을 처리합니다.
        raw_frame_q에서 프레임을 가져와 JPEG로 인코딩하여 frame_q에 넣습니다.
        """
        while not stop_evt.is_set():
            try:
                frame = await asyncio.to_thread(raw_frame_q.get, True, 0.1)
                encoded_frame = await self.encode_frame(frame)

                if encoded_frame:
                    frame_q.put_nowait(encoded_frame)
                                       
            except queue.Empty:
                continue
            except Exception as e:
                print(f"프레임 파이프라인 처리 오류: {e}")
    
    def get_frame_stats(self, raw_frame_q: queue.Queue, frame_q: queue.Queue) -> dict:
        """프레임 큐 상태를 반환합니다."""
        return {
            "raw_frame_q_size": raw_frame_q.qsize(),
            "frame_q_size": frame_q.qsize(),
            "raw_frame_q_maxsize": raw_frame_q.maxsize,
            "frame_q_maxsize": frame_q.maxsize
        }
