"""
비디오 프레임 큐 관리 서비스
- 원본 비디오 데이터를 큐에 저장
- 추론 서비스로 비동기 처리 위임
- 결과 관리 및 모니터링
"""

import asyncio
import time
import uuid
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import numpy as np
import cv2
from av import VideoFrame
import logging

logger = logging.getLogger(__name__)


@dataclass
class VideoFrameData:
    """비디오 프레임 데이터 구조"""
    frame_id: str
    connection_id: str
    frame_number: int
    timestamp: float
    original_frame: VideoFrame
    numpy_array: np.ndarray
    width: int
    height: int
    format: str
    metadata: Dict[str, Any]


@dataclass
class InferenceRequest:
    """추론 요청 데이터 구조"""
    request_id: str
    frame_data: VideoFrameData
    model_type: str = "face_detection"  # face_detection, object_detection, emotion_recognition 등
    priority: int = 1  # 1(높음) ~ 5(낮음)
    created_at: float = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = time.time()


@dataclass
class InferenceResult:
    """추론 결과 데이터 구조"""
    request_id: str
    frame_id: str
    connection_id: str
    model_type: str
    success: bool
    results: Dict[str, Any]  # 추론 결과 (바운딩 박스, 확률 등)
    processing_time: float
    completed_at: float
    error_message: Optional[str] = None


class VideoQueueService:
    """비디오 프레임 큐 관리 서비스"""
    
    def __init__(self, max_queue_size: int = 100, max_workers: int = 3):
        self.max_queue_size = max_queue_size
        self.max_workers = max_workers
        
        # 추론 서비스 import (지연 import로 순환 import 방지)
        self.inference_service = None
        self.emotion_service = None
        
        # 큐 시스템
        self.frame_queue: asyncio.Queue[VideoFrameData] = asyncio.Queue(maxsize=max_queue_size)
        self.inference_queue: asyncio.Queue[InferenceRequest] = asyncio.Queue(maxsize=max_queue_size)
        self.result_queue: asyncio.Queue[InferenceResult] = asyncio.Queue(maxsize=max_queue_size * 2)
        
        # 상태 관리
        self.active_connections: Dict[str, Dict[str, Any]] = {}
        self.inference_results: Dict[str, InferenceResult] = {}  # frame_id -> result
        self.statistics = {
            'total_frames_received': 0,
            'total_frames_processed': 0,
            'total_inference_requests': 0,
            'total_inference_completed': 0,
            'queue_full_drops': 0,
            'processing_errors': 0,
            'inference_service_calls': 0,
            'successful_service_calls': 0,
            'emotion_analysis_calls': 0,
            'successful_emotion_calls': 0
        }
        
        # 이미지 추출 카운터 (연결별)
        self.image_extraction_counters: Dict[str, int] = {}
        
        # static 디렉토리 준비
        self.static_dir = Path("static/extracted_images")
        self.static_dir.mkdir(parents=True, exist_ok=True)
        
        # 첫 번째 이미지 저장 여부 추적
        self.first_image_saved: Dict[str, bool] = {}
        
        # 워커 태스크
        self.workers: List[asyncio.Task] = []
        self.result_processor_task: Optional[asyncio.Task] = None
        self.is_running = False
    
    async def start(self):
        """큐 서비스 시작"""
        if self.is_running:
            return
            
        self.is_running = True
        print(f"🚀 VideoQueueService 시작 - 워커: {self.max_workers}개, 큐 크기: {self.max_queue_size}")
        
        # 추론 서비스 초기화
        await self._initialize_inference_service()
        
        # 추론 워커들 시작
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._inference_worker(f"worker-{i+1}"))
            self.workers.append(worker)
            print(f"🔧 워커-{i+1} 시작됨")
        
        # 결과 처리기 시작
        self.result_processor_task = asyncio.create_task(self._result_processor())
        print(f"📊 결과 처리기 시작됨")
        
        print("✅ VideoQueueService 시작 완료 - 이미지 추출 준비됨!")
    
    async def stop(self):
        """큐 서비스 종료"""
        if not self.is_running:
            return
            
        logger.info("🛑 VideoQueueService 종료 중...")
        self.is_running = False
        
        # 워커들 종료
        for worker in self.workers:
            worker.cancel()
        
        if self.result_processor_task:
            self.result_processor_task.cancel()
        
        # 완료 대기
        await asyncio.gather(*self.workers, self.result_processor_task, return_exceptions=True)
        
        logger.info("✅ VideoQueueService 종료 완료")
    
    async def _initialize_inference_service(self):
        """추론 서비스 초기화"""
        try:
            # 감정 분석 서비스 import
            from .inference_service import emotion_inference_service
            self.emotion_service = emotion_inference_service
            
            print("🎭 EmotionInferenceService 연결 중...")
            success = await self.emotion_service.initialize_models()
            
            if success:
                print("✅ [VideoQueueService] EmotionInferenceService 연결 완료!")
                print("📋 [VideoQueueService] 감정 분석 서비스 준비됨")
            else:
                print("❌ [VideoQueueService] EmotionInferenceService 초기화 실패")
                
        except Exception as e:
            print(f"❌ [VideoQueueService] EmotionInferenceService 연결 실패: {e}")
            logger.error(f"EmotionInferenceService 초기화 오류: {e}")
    
    async def add_video_frame(self, connection_id: str, frame: VideoFrame, frame_number: int, metadata: Dict[str, Any] = None) -> bool:
        """비디오 프레임을 큐에 추가"""
        try:
            # 프레임 데이터 생성
            frame_data = VideoFrameData(
                frame_id=str(uuid.uuid4()),
                connection_id=connection_id,
                frame_number=frame_number,
                timestamp=time.time(),
                original_frame=frame,
                numpy_array=frame.to_ndarray(format='rgb24'),
                width=frame.width,
                height=frame.height,
                format=str(frame.format),
                metadata=metadata or {}
            )
            
            # 큐에 추가 (논블로킹)
            try:
                self.frame_queue.put_nowait(frame_data)
                self.statistics['total_frames_received'] += 1
                
                # 연결 상태 업데이트
                if connection_id not in self.active_connections:
                    self.active_connections[connection_id] = {
                        'first_frame_at': time.time(),
                        'last_frame_at': time.time(),
                        'total_frames': 0
                    }
                
                self.active_connections[connection_id]['last_frame_at'] = time.time()
                self.active_connections[connection_id]['total_frames'] += 1
                
                # 프레임 추가 성공 로그 (간단하게)
                if frame_number % 50 == 0:  # 50프레임마다만 로그
                    print(f"🎬 큐에 프레임 추가됨: #{frame_number} ({frame.width}x{frame.height}) - 큐 크기: {self.frame_queue.qsize()}")
                return True
                
            except asyncio.QueueFull:
                self.statistics['queue_full_drops'] += 1
                logger.warning(f"⚠️ 프레임 큐 가득참 - 프레임 드롭: {connection_id} - #{frame_number}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 프레임 추가 오류: {e}")
            return False
    
    async def _inference_worker(self, worker_name: str):
        """추론 워커 - 큐에서 프레임을 가져와 추론 모델에 요청"""
        logger.info(f"🔧 추론 워커 시작: {worker_name}")
        
        while self.is_running:
            try:
                # 프레임 큐에서 데이터 가져오기 (타임아웃 설정)
                try:
                    frame_data = await asyncio.wait_for(self.frame_queue.get(), timeout=1.0)
                    print(f"🔧 [{worker_name}] 큐에서 프레임 가져옴: #{frame_data.frame_number}")
                except asyncio.TimeoutError:
                    continue
                
                # 추론 요청 생성 - 감정 분석으로 변경
                inference_request = InferenceRequest(
                    request_id=str(uuid.uuid4()),
                    frame_data=frame_data,
                    model_type="emotion_analysis"  # 감정 분석으로 변경
                )
                
                print(f"🎭 [{worker_name}] 감정 분석 시작: 프레임 #{frame_data.frame_number}")
                
                # 실제 추론 모델 호출
                result = await self._run_inference(inference_request)
                
                # 결과를 결과 큐에 추가
                await self.result_queue.put(result)
                
                # 통계 업데이트
                self.statistics['total_frames_processed'] += 1
                self.statistics['total_inference_requests'] += 1
                
                if result.success:
                    self.statistics['total_inference_completed'] += 1
                else:
                    self.statistics['processing_errors'] += 1
                
                # 큐 태스크 완료 표시
                self.frame_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [{worker_name}] 워커 오류: {e}")
                self.statistics['processing_errors'] += 1
        
        logger.info(f"🔧 추론 워커 종료: {worker_name}")
    
    async def _run_inference(self, request: InferenceRequest) -> InferenceResult:
        """감정 분석 서비스를 호출하여 실제 추론 실행"""
        start_time = time.time()
        
        print(f"📞 [VideoQueueService] 감정 분석 서비스 호출 시작 - 타입: {request.model_type}")
        self.statistics['emotion_analysis_calls'] += 1
        
        try:
            frame_data = request.frame_data
            img = frame_data.numpy_array
            
            # 감정 분석 서비스가 초기화되지 않은 경우 대비
            if not self.emotion_service:
                raise RuntimeError("EmotionInferenceService가 초기화되지 않음")
            
            # 10프레임마다만 실제 감정 분석 실행
            if frame_data.frame_number % 10 == 0:
                # 연결별 카운터 초기화
                if frame_data.connection_id not in self.image_extraction_counters:
                    self.image_extraction_counters[frame_data.connection_id] = 0
                
                self.image_extraction_counters[frame_data.connection_id] += 1
                current_count = self.image_extraction_counters[frame_data.connection_id]
                
                # 감정 분석 서비스 호출
                print(f"🔄 [VideoQueueService] EmotionInferenceService.run_emotion_analysis() 호출")
                
                # RGB 프레임을 BGR로 변환 (현재 프레임이 RGB이므로)
                img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                
                emotion_result = await self.emotion_service.run_emotion_analysis(
                    frame_bgr=img_bgr,
                    metadata={
                        'connection_id': frame_data.connection_id,
                        'frame_number': frame_data.frame_number,
                        'analysis_count': current_count,
                        'timestamp': frame_data.timestamp
                    }
                )
                
                if emotion_result['success']:
                    print(f"✅ [VideoQueueService] EmotionInferenceService 호출 성공! 결과 수신됨")
                    self.statistics['successful_emotion_calls'] += 1
                    
                    # 첫 번째 이미지 저장
                    if frame_data.connection_id not in self.first_image_saved:
                        saved_path = self.save_first_image_to_static(frame_data.connection_id, img_bgr, frame_data.frame_number)
                        if saved_path:
                            print(f"💾 [VideoQueueService] 첫 번째 이미지 저장 완료: {saved_path}")
                            self.first_image_saved[frame_data.connection_id] = True
                    
                    # 결과 정리
                    results = {
                        "emotion_analysis": True,
                        "face_count": emotion_result['face_count'],
                        "emotions": emotion_result['emotions'],
                        "analysis_count": current_count,
                        "saved_first_image": frame_data.connection_id in self.first_image_saved,
                        "processing_time": emotion_result['processing_time']
                    }
                    
                    # 감정 정보 요약 로그
                    if emotion_result['face_count'] > 0:
                        emotions = emotion_result['emotions'][0]
                        dominant = max(emotions, key=emotions.get)
                        confidence = emotions[dominant]
                        print(f"🎭 [VideoQueueService] → 감정 분석 결과: {emotion_result}")
                    else:
                        print(f"🎭 [VideoQueueService] → 감정 분석 결과: 얼굴 없음")
                    
                else:
                    print(f"❌ [VideoQueueService] EmotionInferenceService 호출 실패: {emotion_result.get('error', 'Unknown error')}")
                    results = {
                        "emotion_analysis": False, 
                        "error": emotion_result.get('error'),
                        "analysis_count": current_count
                    }
                
            else:
                # 10프레임이 아닌 경우 - 감정 분석 건너뛰기
                print(f"⏭️  [VideoQueueService] 프레임 #{frame_data.frame_number} 건너뛰기 (10프레임마다 감정 분석)")
                results = {
                    "emotion_analysis": False,
                    "reason": "not_analysis_frame",
                    "frame_number": frame_data.frame_number,
                    "next_analysis_frame": ((frame_data.frame_number // 10) + 1) * 10
                }
            
            processing_time = time.time() - start_time
            
            return InferenceResult(
                request_id=request.request_id,
                frame_id=frame_data.frame_id,
                connection_id=frame_data.connection_id,
                model_type=request.model_type,
                success=True,
                results=results,
                processing_time=processing_time,
                completed_at=time.time()
            )
            
        except Exception as e:
            processing_time = time.time() - start_time
            error_msg = f"감정 분석 서비스 호출 실패: {str(e)}"
            print(f"❌ [VideoQueueService] {error_msg}")
            logger.error(error_msg)
            
            return InferenceResult(
                request_id=request.request_id,
                frame_id=request.frame_data.frame_id,
                connection_id=request.frame_data.connection_id,
                model_type=request.model_type,
                success=False,
                results={},
                processing_time=processing_time,
                completed_at=time.time(),
                error_message=str(e)
            )
    
    # 이 메서드는 더 이상 사용하지 않음 - InferenceService로 이관됨
    
    def save_first_image_to_static(self, connection_id: str, img: np.ndarray, frame_number: int) -> Optional[str]:
        """
        첫 번째 이미지를 static/extracted_images 폴더에 저장
        """
        try:
            # 파일명 생성 (연결 ID와 프레임 번호 포함)
            timestamp = int(time.time())
            filename = f"first_frame_{connection_id[:8]}_{frame_number}_{timestamp}.jpg"
            file_path = self.static_dir / filename
            
            # RGB를 BGR로 변환 (OpenCV는 BGR 형식 사용)
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            
            # 이미지 저장 (JPEG 품질 95%)
            success = cv2.imwrite(str(file_path), img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            
            if success:
                # 상대 경로로 반환 (웹에서 접근 가능한 경로)
                relative_path = f"static/extracted_images/{filename}"
                return relative_path
            else:
                print(f"❌ 이미지 저장 실패: {file_path}")
                return None
                
        except Exception as e:
            print(f"❌ 이미지 저장 오류: {e}")
            return None
    
    # 이 메서드들도 더 이상 사용하지 않음 - InferenceService로 이관됨
    
    async def _result_processor(self):
        """추론 결과 처리기"""
        logger.info("📊 결과 처리기 시작")
        
        while self.is_running:
            try:
                # 결과 큐에서 결과 가져오기
                try:
                    result = await asyncio.wait_for(self.result_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                # 결과 저장
                self.inference_results[result.frame_id] = result
                
                # 결과 로깅 (이미지 추출 성공 시만)
                if result.success and result.results.get('extracted', False):
                    # 이미지 추출 성공 로그는 _extract_and_count_images에서 이미 출력됨
                    pass
                elif not result.success:
                    print(f"❌ 이미지 추출 실패: {result.connection_id[:8]}... - {result.error_message}")
                
                # 오래된 결과 정리 (메모리 관리)
                await self._cleanup_old_results()
                
                # 큐 태스크 완료
                self.result_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 결과 처리 오류: {e}")
        
        logger.info("📊 결과 처리기 종료")
    
    async def _cleanup_old_results(self):
        """오래된 결과 정리 (메모리 절약)"""
        current_time = time.time()
        cutoff_time = current_time - 300  # 5분 이상 된 결과 삭제
        
        to_delete = []
        for frame_id, result in self.inference_results.items():
            if result.completed_at < cutoff_time:
                to_delete.append(frame_id)
        
        for frame_id in to_delete:
            del self.inference_results[frame_id]
        
        if to_delete:
            logger.debug(f"🧹 오래된 결과 {len(to_delete)}개 정리됨")
    
    def get_statistics(self) -> Dict[str, Any]:
        """큐 시스템 통계 반환"""
        # 총 이미지 추출 카운트 계산
        total_extracted_images = sum(self.image_extraction_counters.values())
        
        # 저장된 첫 번째 이미지 수 계산
        saved_first_images = len(self.first_image_saved)
        
        # 감정 분석 서비스 호출 성공률 계산
        emotion_call_success_rate = 0
        if self.statistics['emotion_analysis_calls'] > 0:
            emotion_call_success_rate = (self.statistics['successful_emotion_calls'] / 
                                       self.statistics['emotion_analysis_calls'] * 100)
        
        base_stats = {
            **self.statistics,
            'frame_queue_size': self.frame_queue.qsize(),
            'inference_queue_size': self.inference_queue.qsize(),
            'result_queue_size': self.result_queue.qsize(),
            'active_connections': len(self.active_connections),
            'cached_results': len(self.inference_results),
            'is_running': self.is_running,
            'worker_count': len(self.workers),
            # 이미지 추출 통계
            'total_extracted_images': total_extracted_images,
            'image_extraction_by_connection': dict(self.image_extraction_counters),
            'saved_first_images': saved_first_images,
            'static_images_dir': str(self.static_dir),
            # 감정 분석 서비스 호출 통계
            'emotion_analysis_call_success_rate': round(emotion_call_success_rate, 2)
        }
        
        # 감정 분석 서비스 통계 추가
        if self.emotion_service:
            emotion_stats = self.emotion_service.get_statistics()
            base_stats['emotion_service_stats'] = emotion_stats
        
        return base_stats
    
    def get_connection_info(self, connection_id: str) -> Optional[Dict[str, Any]]:
        """특정 연결의 정보 반환"""
        return self.active_connections.get(connection_id)
    
    def get_recent_results(self, connection_id: str, limit: int = 10) -> List[InferenceResult]:
        """특정 연결의 최근 추론 결과들 반환"""
        results = []
        for result in self.inference_results.values():
            if result.connection_id == connection_id:
                results.append(result)
        
        # 최신순 정렬
        results.sort(key=lambda x: x.completed_at, reverse=True)
        return results[:limit]


# 전역 인스턴스
video_queue_service = VideoQueueService(max_queue_size=100, max_workers=3)
