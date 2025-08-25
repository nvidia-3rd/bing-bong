# dual_audio_service.py
import asyncio
import threading
import time
from typing import Optional, Dict, Any
from .audio_service import PyAudioCaptureService

class DualAudioService:
    """이중 오디오 캡처 서비스: streamlit-webrtc + PyAudio"""
    
    def __init__(self):
        # PyAudio 서비스
        self.pyaudio_service = PyAudioCaptureService(
            sample_rate=16000,
            channels=1
        )
        
        # 상태 관리
        self.is_webrtc_active = False
        self.is_pyaudio_active = False
        
        # 오디오 데이터 통합
        self.webrtc_audio_queue = asyncio.Queue(maxsize=100)
        self.pyaudio_audio_queue = asyncio.Queue(maxsize=100)
        
        # 통합 통계
        self.stats = {
            'webrtc_frames': 0,
            'pyaudio_frames': 0,
            'total_bytes': 0,
            'start_time': None,
            'errors': 0
        }
        
        # 콜백 함수들
        self.webrtc_callback = None
        self.pyaudio_callback = None
        
        print("[DualAudio] 🎤 이중 오디오 캡처 서비스 초기화")
    
    def initialize(self) -> bool:
        """서비스 초기화"""
        try:
            # PyAudio 초기화
            if not self.pyaudio_service.initialize():
                print("[DualAudio] ❌ PyAudio 초기화 실패")
                return False
            
            print("[DualAudio] ✅ 초기화 완료")
            return True
            
        except Exception as e:
            print(f"[DualAudio] ❌ 초기화 실패: {e}")
            return False
    
    def start_webrtc_capture(self, callback=None):
        """WebRTC 캡처 시작"""
        try:
            self.is_webrtc_active = True
            self.webrtc_callback = callback
            self.stats['start_time'] = time.time()
            print("[DualAudio] 🎤 WebRTC 캡처 시작")
            return True
            
        except Exception as e:
            print(f"[DualAudio] ❌ WebRTC 캡처 시작 실패: {e}")
            return False
    
    def start_pyaudio_capture(self, callback=None):
        """PyAudio 캡처 시작"""
        try:
            if not self.pyaudio_service.is_initialized:
                print("[DualAudio] ❌ PyAudio가 초기화되지 않았습니다")
                return False
            
            self.is_pyaudio_active = True
            self.pyaudio_callback = callback
            
            # PyAudio 녹음 시작
            if not self.pyaudio_service.start_recording(callback=self._pyaudio_data_handler):
                print("[DualAudio] ❌ PyAudio 녹음 시작 실패")
                return False
            
            print("[DualAudio] 🎤 PyAudio 캡처 시작")
            return True
            
        except Exception as e:
            print(f"[DualAudio] ❌ PyAudio 캡처 시작 실패: {e}")
            return False
    
    def _pyaudio_data_handler(self, audio_data: bytes):
        """PyAudio 데이터 핸들러"""
        try:
            # 큐에 추가
            if not self.pyaudio_audio_queue.full():
                asyncio.create_task(self.pyaudio_audio_queue.put(audio_data))
            
            # 통계 업데이트
            self.stats['pyaudio_frames'] += 1
            self.stats['total_bytes'] += len(audio_data)
            
            # 콜백 함수 호출
            if self.pyaudio_callback:
                self.pyaudio_callback(audio_data)
                
        except Exception as e:
            print(f"[DualAudio] PyAudio 데이터 핸들러 오류: {e}")
            self.stats['errors'] += 1
    
    def handle_webrtc_audio(self, audio_data: bytes):
        """WebRTC 오디오 데이터 처리"""
        try:
            # 큐에 추가
            if not self.webrtc_audio_queue.full():
                asyncio.create_task(self.webrtc_audio_queue.put(audio_data))
            
            # 통계 업데이트
            self.stats['webrtc_frames'] += 1
            self.stats['total_bytes'] += len(audio_data)
            
            # 콜백 함수 호출
            if self.webrtc_callback:
                self.webrtc_callback(audio_data)
                
        except Exception as e:
            print(f"[DualAudio] WebRTC 데이터 핸들러 오류: {e}")
            self.stats['errors'] += 1
    
    async def get_webrtc_audio(self, timeout: float = 0.1) -> Optional[bytes]:
        """WebRTC 오디오 데이터 가져오기"""
        try:
            return await asyncio.wait_for(self.webrtc_audio_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    async def get_pyaudio_audio(self, timeout: float = 0.1) -> Optional[bytes]:
        """PyAudio 오디오 데이터 가져오기"""
        try:
            return await asyncio.wait_for(self.pyaudio_audio_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    def get_combined_stats(self) -> Dict[str, Any]:
        """통합 통계 반환"""
        stats = self.stats.copy()
        
        # PyAudio 통계 추가
        pyaudio_stats = self.pyaudio_service.get_stats()
        stats.update({
            'pyaudio_device': pyaudio_stats.get('device_name', 'Unknown'),
            'pyaudio_queue_size': pyaudio_stats.get('queue_size', 0),
            'pyaudio_errors': pyaudio_stats.get('errors', 0)
        })
        
        # 큐 크기 추가
        stats['webrtc_queue_size'] = self.webrtc_audio_queue.qsize()
        stats['pyaudio_queue_size'] = self.pyaudio_audio_queue.qsize()
        
        # 상태 정보
        stats['webrtc_active'] = self.is_webrtc_active
        stats['pyaudio_active'] = self.is_pyaudio_active
        
        return stats
    
    def stop_all_capture(self):
        """모든 캡처 중지"""
        try:
            # WebRTC 중지
            if self.is_webrtc_active:
                self.is_webrtc_active = False
                print("[DualAudio] 🛑 WebRTC 캡처 중지")
            
            # PyAudio 중지
            if self.is_pyaudio_active:
                self.is_pyaudio_active = False
                self.pyaudio_service.stop_recording()
                print("[DualAudio] 🛑 PyAudio 캡처 중지")
            
            # 통계 계산
            if self.stats['start_time']:
                duration = time.time() - self.stats['start_time']
                print(f"[DualAudio] 📊 총 캡처 시간: {duration:.1f}초")
                print(f"[DualAudio] 📊 WebRTC 프레임: {self.stats['webrtc_frames']}개")
                print(f"[DualAudio] 📊 PyAudio 프레임: {self.stats['pyaudio_frames']}개")
                print(f"[DualAudio] 📊 총 바이트: {self.stats['total_bytes']:,} bytes")
            
        except Exception as e:
            print(f"[DualAudio] ❌ 캡처 중지 오류: {e}")
    
    def cleanup(self):
        """리소스 정리"""
        try:
            self.stop_all_capture()
            self.pyaudio_service.cleanup()
            print("[DualAudio] 🧹 리소스 정리 완료")
            
        except Exception as e:
            print(f"[DualAudio] ❌ 리소스 정리 오류: {e}")
