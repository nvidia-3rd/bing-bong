import time
import asyncio
from typing import Optional, Dict, Any
import numpy as np
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection
from aiortc.rtcrtpparameters import RTCRtpCodecCapability
from av import VideoFrame

from ..models.webrtc import SDPOffer, SDPAnswer, ConnectionInfo
from ..core.exceptions import WebRTCError, ConnectionError
from .connection_service import ConnectionService, ConnectionContext
from .video_queue_service import video_queue_service

class WebRTCService:
    """
    WebRTC 비즈니스 로직을 담당하는 서비스
    - 비디오 스트림 처리
    - AI 추론 파이프라인 관리
    - 미디어 데이터 처리
    """
    
    def __init__(self):
        self.connection_service = ConnectionService()
        self.video_queue_service = video_queue_service
        self._setup_lifecycle_handlers()

    def _setup_lifecycle_handlers(self):
        """
        ConnectionService의 생명주기 이벤트에 비즈니스 로직 연결
        """
        self.connection_service.register_lifecycle_handler(
            'connected', self._on_connection_established
        )
        self.connection_service.register_lifecycle_handler(
            'closed', self._on_connection_closed
        )

    async def create_connection(
        self, 
        offer: SDPOffer, 
        connection_id: str | None = None,
        await_ice_complete: bool = False,
        max_queue_size: int = 8
    ) -> SDPAnswer:
        """
        WebRTC 연결 생성 및 비디오 처리 파이프라인 설정
        """
        # ConnectionService에 연결 생성 위임
        context = await self.connection_service.create_connection_context(offer, connection_id)
        
        # 비즈니스 로직 설정 (비디오 처리)
        await self._setup_media_processing(context, max_queue_size)
        
        return context.get_answer()

    async def _setup_media_processing(self, context: ConnectionContext, max_queue_size: int):
        """
        미디어 처리 파이프라인 설정
        """
        connection_id = context.connection_id
        pc = context.pc
        
        print(f"[webrtc:{connection_id}] Setting up media processing pipeline")
        
        # 트랜시버 설정
        await self._configure_transceivers(pc, connection_id)
        
        # 트랙 핸들러 설정
        await self._setup_track_handlers(pc, context, max_queue_size)

    async def _configure_transceivers(self, pc, connection_id: str):
        """
        트랜시버 코덱 설정
        """
        print(f"[webrtc:{connection_id}] Configuring transceivers")
        for transceiver in pc.getTransceivers():
            if transceiver.kind == "video":
                print(f"[webrtc:{connection_id}] Video transceiver direction: {transceiver.direction}")
                # 코덱 선호도 설정
                try:
                    vp8 = RTCRtpCodecCapability(mimeType="video/VP8", clockRate=90000)
                    h264 = RTCRtpCodecCapability(mimeType="video/H264", clockRate=90000)
                    transceiver.setCodecPreferences([vp8, h264])
                except Exception as e:
                    print(f"[webrtc:{connection_id}] setCodecPreferences warn: {e}")
            elif transceiver.kind == "audio":
                print(f"[webrtc:{connection_id}] Audio transceiver direction: {transceiver.direction}")

    async def _setup_track_handlers(self, pc, context: ConnectionContext, max_queue_size: int):
        """
        트랙 수신 핸들러 설정
        """
        connection_id = context.connection_id
        
        @pc.on("track")
        def _on_track(track):
            print(f"[webrtc:{connection_id}] 🎥 Track received!")
            
            if track.kind == "video":
                print(f"[webrtc:{connection_id}] 🚀 Starting video processing...")
                # 비디오 프레임을 처리하는 태스크 시작
                reader_task = asyncio.create_task(
                    self._video_reader_loop(connection_id, track, max_queue_size)
                )
                context.add_task(reader_task)
                
                print(f"[webrtc:{connection_id}] Video reader task started")
                
            elif track.kind == "audio":
                print(f"[webrtc:{connection_id}] 🔊 Audio track received (not processing)")

    async def _on_connection_established(self, connection_id: str):
        """
        연결 확립 시 비즈니스 로직
        """
        print(f"[webrtc:{connection_id}] 🎯 Business logic: Connection established")
        
        # 비디오 큐 서비스 시작 확인
        if not self.video_queue_service.is_running:
            await self.video_queue_service.start()
            print(f"[webrtc:{connection_id}] 🚀 Video queue service started")

    async def _on_connection_closed(self, connection_id: str):
        """
        연결 종료 시 비즈니스 로직
        """
        print(f"[webrtc:{connection_id}] 🎯 Business logic: Connection closed")
        # 필요시 추가 정리 로직




    async def _video_reader_loop(self, conn_id: str, track, max_queue_size: int) -> None:
        """
        비디오 트랙에서 프레임을 수신하고 큐에 추가
        """
        frame_count = 0
        successful_frames = 0
        error_count = 0
        
        print(f"[webrtc:{conn_id}] 🎬 Video reader loop started - 큐 시스템 연동")
        
        # 큐 서비스 시작 확인
        if not self.video_queue_service.is_running:
            await self.video_queue_service.start()
            print(f"[webrtc:{conn_id}] 🚀 Video queue service started")
        
        try:
            while True:
                try:
                    # 비디오 프레임 수신 (타임아웃 설정)
                    frame = await asyncio.wait_for(track.recv(), timeout=5.0)
                    frame_count += 1

                    # 비디오 큐에 원본 프레임 추가
                    success = await self.video_queue_service.add_video_frame(
                        connection_id=conn_id,
                        frame=frame,
                        frame_number=frame_count,
                        metadata={'received_at': time.time(), 'track_kind': track.kind}
                    )
                    
                    if success:
                        successful_frames += 1
                    else:
                        error_count += 1
                        print(f"[webrtc:{conn_id}] ❌ Frame {frame_count} 큐 추가 실패 (큐 가득참?)")
                    
                    # 실제 이미지 추출만 로그 출력 (10프레임마다 상태는 제거)
                    
                except asyncio.TimeoutError:
                    print(f"[webrtc:{conn_id}] ⏰ Timeout waiting for frame (5s)")
                    error_count += 1
                    if error_count > 3:
                        print(f"[webrtc:{conn_id}] ❌ Too many timeouts, stopping reader")
                        break
                    continue
                    
                except Exception as e:
                    print(f"[webrtc:{conn_id}] ⚠️ Frame processing error: {e}")
                    error_count += 1
                    if error_count > 10:
                        print(f"[webrtc:{conn_id}] ❌ Too many errors, stopping reader")
                        break
                    continue
                
        except asyncio.CancelledError:
            print(f"[webrtc:{conn_id}] Video reader loop cancelled")
        except Exception as e:
            print(f"[webrtc:{conn_id}] Video reader loop error: {e}")
        finally:
            print(f"[webrtc:{conn_id}] 🏁 Video reader loop ended")
            print(f"[webrtc:{conn_id}]   - Total frames: {frame_count}")
            print(f"[webrtc:{conn_id}]   - Successful: {successful_frames}")
            print(f"[webrtc:{conn_id}]   - Errors: {error_count}")

    async def _process_video_frame(self, conn_id: str, frame: VideoFrame, frame_num: int) -> bool:
        """
        개별 비디오 프레임 처리
        """
        try:
            # 프레임을 numpy 배열로 변환
            img = frame.to_ndarray(format="rgb24")
            print(f"[webrtc:{conn_id}] Frame {frame_num} converted to numpy: {img.shape}")
            
            # 여기에 실제 처리 로직 추가
            # - 얼굴 인식
            # - 객체 탐지
            # - 이미지 저장
            # - AI 모델 추론 등
            
            # 예시: 프레임 저장 (테스트용)
            # import cv2
            # cv2.imwrite(f"frame_{conn_id}_{frame_num}.jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
            
            return True
            
        except Exception as e:
            print(f"[webrtc:{conn_id}] Frame {frame_num} processing error: {e}")
            return False

    async def get_connection_stats(self, conn_id: str) -> Optional[Dict[str, Any]]:
        """연결 통계 반환 (ConnectionService에 위임)"""
        return await self.connection_service.get_connection_stats(conn_id)
    
    async def cleanup_all_connections(self):
        """모든 연결 정리 (ConnectionService에 위임)"""
        print("[WebRTCService] Cleaning up all connections...")
        await self.connection_service.cleanup_all_connections()
        print("[WebRTCService] All connections cleaned up")

    def list_active_connections(self) -> Dict[str, Dict[str, Any]]:
        """활성 연결 목록 반환 (ConnectionService에 위임)"""
        return self.connection_service.list_active_connections()

