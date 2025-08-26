# dual_audio_service.py
import asyncio
import threading
import time
import os
from datetime import datetime
from typing import Optional, Dict, Any
from .audio_service import PyAudioCaptureService

class DualAudioService:
    """이중 오디오 캡처 서비스: streamlit-webrtc + PyAudio"""
    
    def __init__(self):
        # PyAudio 서비스 (BlackHole 모드로 설정)
        self.pyaudio_service = PyAudioCaptureService(
            use_blackhole=True  # BlackHole 사용하여 48kHz → 16kHz 변환
        )
        
        # BlackHole 디바이스의 실제 채널 수를 사용하도록 수정
        # BlackHole 16ch는 16채널, BlackHole 2ch는 2채널
        if hasattr(self.pyaudio_service, 'channels_in'):
            # 실제 디바이스 채널 수로 덮어쓰기
            self.pyaudio_service.channels_in = None  # 초기화 시 자동 감지하도록
        
        print(f"[DualAudio] 🎤 PyAudio 서비스 생성 완료:")
        print(f"  - 서비스 객체: {self.pyaudio_service}")
        print(f"  - 입력 샘플레이트: {self.pyaudio_service.rate_in}Hz")
        print(f"  - 출력 샘플레이트: {self.pyaudio_service.rate_out}Hz")
        print(f"  - 입력 채널 수: {self.pyaudio_service.channels_in}")
        print(f"  - 출력 채널 수: {self.pyaudio_service.channels_out}")
        
        # 상태 관리
        self.is_pyaudio_active = False
        
        # PyAudio 콜백 함수
        self.pyaudio_callback = None
        
        # PyAudio WAV 파일 생성을 위한 누적 데이터
        self.pyaudio_accumulated_data = bytearray()
        self.pyaudio_start_time = None
        
        # 통합 통계
        self.stats = {
            'pyaudio_frames': 0,
            'total_bytes': 0,
            'pyaudio_wav_files': 0,
            'start_time': None,
            'errors': 0
        }
        
        # 로그 중복 방지 플래그
        self._data_received_logged = False
        self._sample_analysis_logged = False
        
        print("[DualAudio] 🎤 이중 오디오 캡처 서비스 초기화")
        
        # 자동으로 PyAudio 캡처 시작 시도
        print("[DualAudio] 🔧 자동 PyAudio 캡처 시작 시도...")
        try:
            # 1단계: PyAudio 초기화
            print("  [1/3] PyAudio 초기화...")
            if not self.initialize():
                print("  ❌ PyAudio 초기화 실패")
                return
            
            # 2단계: BlackHole 디바이스 확인
            print("  [2/3] BlackHole 디바이스 확인...")
            if hasattr(self.pyaudio_service, 'selected_device'):
                device = self.pyaudio_service.selected_device
                print(f"  ✅ 선택된 디바이스: {device.get('name', 'Unknown')}")
                if 'blackhole' in device.get('name', '').lower():
                    print("  🎧 BlackHole 디바이스 감지됨")
                else:
                    print("  ⚠️ BlackHole이 아닌 다른 디바이스")
            else:
                print("  ❌ 선택된 디바이스 정보 없음")
            
            # 3단계: PyAudio 캡처 시작
            print("  [3/3] PyAudio 캡처 시작...")
            if self.start_pyaudio_capture():
                print("  ✅ 자동 PyAudio 캡처 시작 성공")
            else:
                print("  ❌ 자동 PyAudio 캡처 시작 실패")
                # 실패 시 재시도
                print("  🔄 3초 후 재시도...")
                time.sleep(3)
                if self.start_pyaudio_capture():
                    print("  ✅ 재시도 성공")
                else:
                    print("  ❌ 재시도 실패")
                    
        except Exception as e:
            print(f"[DualAudio] ⚠️ 자동 PyAudio 캡처 시작 중 오류: {e}")
            import traceback
            traceback.print_exc()
    
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
            print(f"[DualAudio] 🔍 PyAudio 캡처 시작 시도:")
            print(f"  - PyAudio 서비스 상태: {self.pyaudio_service}")
            print(f"  - PyAudio 인스턴스: {hasattr(self.pyaudio_service, 'pa')}")
            print(f"  - PyAudio 초기화 상태: {self.pyaudio_service.pa is not None if hasattr(self.pyaudio_service, 'pa') else False}")
            
            # BlackHole 디바이스 상태 확인
            if hasattr(self.pyaudio_service, 'selected_device'):
                print(f"  - 선택된 디바이스: {self.pyaudio_service.selected_device}")
            else:
                print("  - 선택된 디바이스: 없음")
            
            # PyAudio 강제 초기화 시도
            print("[DualAudio] 🔧 PyAudio 강제 초기화 시도...")
            if not self.pyaudio_service.initialize():
                print("[DualAudio] ❌ PyAudio 초기화 실패")
                return False
            print("[DualAudio] ✅ PyAudio 초기화 완료")
            
            # 초기화 후 상태 재확인
            if not hasattr(self.pyaudio_service, 'pa') or self.pyaudio_service.pa is None:
                print("[DualAudio] ❌ PyAudio 초기화 후에도 인스턴스가 없음")
                print(f"  - hasattr('pa'): {hasattr(self.pyaudio_service, 'pa')}")
                print(f"  - pa is None: {self.pyaudio_service.pa is None if hasattr(self.pyaudio_service, 'pa') else 'N/A'}")
                return False
            
            # BlackHole 디바이스 재확인
            if hasattr(self.pyaudio_service, 'selected_device'):
                print(f"  - 초기화 후 선택된 디바이스: {self.pyaudio_service.selected_device}")
                if 'blackhole' in self.pyaudio_service.selected_device.get('name', '').lower():
                    print("  - ✅ BlackHole 디바이스 감지됨")
                else:
                    print("  - ⚠️ BlackHole 디바이스가 아님")
            
            self.is_pyaudio_active = True
            self.pyaudio_callback = callback
            
            # PyAudio 녹음 시작 시 누적 데이터 초기화
            self.pyaudio_accumulated_data.clear()
            self.pyaudio_start_time = time.time()
            
            print(f"[DualAudio] 🎤 PyAudio 녹음 시작 시도...")
            print(f"  - 입력 샘플레이트: {self.pyaudio_service.rate_in}Hz")
            print(f"  - 출력 샘플레이트: {self.pyaudio_service.rate_out}Hz")
            print(f"  - 입력 채널 수: {self.pyaudio_service.channels_in}")
            print(f"  - 출력 채널 수: {self.pyaudio_service.channels_out}")
            print(f"  - PyAudio 인스턴스: {self.pyaudio_service.pa}")
            
            # PyAudio 녹음 시작
            print("  🎙️ PyAudio 녹음 시작 시도...")
            if not self.pyaudio_service.start_recording(callback=self._pyaudio_data_handler):
                print("[DualAudio] ❌ PyAudio 녹음 시작 실패")
                self.is_pyaudio_active = False
                return False
            
            print("[DualAudio] ✅ PyAudio 녹음 시작 완료")
            
            # 최종 상태 확인
            print("  📊 최종 상태 확인:")
            print(f"    - is_pyaudio_active: {self.is_pyaudio_active}")
            print(f"    - PyAudio 녹음 중: {self.pyaudio_service.is_recording}")
            print(f"    - PyAudio 스트림: {self.pyaudio_service.stream}")
            
            return True
            
        except Exception as e:
            print(f"[DualAudio] ❌ PyAudio 캡처 시작 실패: {e}")
            import traceback
            traceback.print_exc()
            self.is_pyaudio_active = False
            return False
    
    def start_all_capture(self, webrtc_callback=None, pyaudio_callback=None):
        """WebRTC와 PyAudio 캡처 모두 시작"""
        try:
            success_count = 0
            
            # WebRTC 캡처 시작
            if self.start_webrtc_capture(webrtc_callback):
                success_count += 1
            
            # PyAudio 캡처 시작
            if self.start_pyaudio_capture(pyaudio_callback):
                success_count += 1
            
            if success_count > 0:
                print(f"[DualAudio] 🎤 {success_count}/2 캡처 서비스 시작 완료")
                return True
            else:
                print("[DualAudio] ❌ 모든 캡처 서비스 시작 실패")
                return False
                
        except Exception as e:
            print(f"[DualAudio] ❌ 전체 캡처 시작 실패: {e}")
            return False
    
    def _pyaudio_data_handler(self, audio_data: bytes):
        """PyAudio 데이터 핸들러"""
        try:
            # 데이터 수신 로그는 한 번만 출력 (중복 방지)
            if not hasattr(self, '_data_received_logged'):
                print(f"[DualAudio] 🎵 PyAudio 데이터 수신 시작: {len(audio_data)} bytes")
                self._data_received_logged = True
            

            
            # 오디오 데이터 분석 (첫 100바이트만, 한 번만)
            if not hasattr(self, '_sample_analysis_logged') and len(audio_data) > 0:
                sample_data = audio_data[:min(100, len(audio_data))]
                print(f"  - 샘플 데이터 (hex): {sample_data.hex()[:50]}...")
                
                # int16으로 변환 시도
                try:
                    import numpy as np
                    samples = np.frombuffer(audio_data, dtype=np.int16)
                    print(f"  - 샘플 수: {len(samples)}")
                    print(f"  - 샘플 범위: {samples.min()} ~ {samples.max()}")
                    print(f"  - RMS 레벨: {np.sqrt(np.mean(samples.astype(np.float32)**2)):.2f}")
                    self._sample_analysis_logged = True
                except Exception as e:
                    print(f"  - 샘플 분석 실패: {e}")
                    self._sample_analysis_logged = True
            
            # 오디오 데이터 누적 (WAV 파일 생성용)
            self.pyaudio_accumulated_data.extend(audio_data)
            
            # 통계 업데이트
            self.stats['pyaudio_frames'] += 1
            self.stats['total_bytes'] += len(audio_data)
            
            # 1000프레임마다만 통계 출력 (빈도 감소)

            
            # 콜백 함수 호출 (데이터가 들어오면 처리)
            if self.pyaudio_callback:
                self.pyaudio_callback(audio_data)
                
        except Exception as e:
            print(f"[DualAudio] ❌ PyAudio 데이터 핸들러 오류: {e}")
            import traceback
            traceback.print_exc()
            self.stats['errors'] += 1
    
    def get_combined_stats(self) -> Dict[str, Any]:
        """통합 통계 반환"""
        stats = self.stats.copy()
        
        # PyAudio 통계 추가
        try:
            pyaudio_stats = self.pyaudio_service.get_stats()
            stats.update({
                'pyaudio_device': pyaudio_stats.get('selected_device', {}).get('name', 'Unknown'),
                'pyaudio_rate_in': pyaudio_stats.get('rate_in', 'Unknown'),
                'pyaudio_rate_out': pyaudio_stats.get('rate_out', 'Unknown'),
                'pyaudio_channels_in': pyaudio_stats.get('channels_in', 'Unknown'),
                'pyaudio_channels_out': pyaudio_stats.get('channels_out', 'Unknown'),
                'pyaudio_frames_captured': pyaudio_stats.get('frames_captured', 0),
                'pyaudio_blocks_created': pyaudio_stats.get('blocks_created', 0),
                'pyaudio_errors': pyaudio_stats.get('errors', 0),
                'pyaudio_is_recording': pyaudio_stats.get('is_recording', False)
            })
        except Exception as e:
            stats['pyaudio_error'] = str(e)
        
        # 상태 정보
        stats['pyaudio_active'] = self.is_pyaudio_active
        
        # PyAudio 누적 데이터 정보
        stats['pyaudio_accumulated_data_size'] = len(self.pyaudio_accumulated_data)
        
        return stats
    
    def stop_all_capture(self):
        """PyAudio 캡처 중지"""
        try:
            # PyAudio 중지
            if self.is_pyaudio_active:
                self.is_pyaudio_active = False
                self.pyaudio_service.stop_recording()
                print("[DualAudio] 🛑 PyAudio 캡처 중지")
            
            # 통계 계산
            if self.stats['start_time']:
                duration = time.time() - self.stats['start_time']
                print(f"[DualAudio] 📊 총 캡처 시간: {duration:.1f}초")
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

    def build_pyaudio_wav_bytes(self) -> Optional[bytes]:
        """PyAudio 누적 데이터로 WAV 파일 생성"""
        try:
            print(f"[DualAudio] 🔍 PyAudio WAV 파일 생성 시작:")
            print(f"  - 누적 데이터 크기: {len(self.pyaudio_accumulated_data):,} bytes")
            print(f"  - 입력 샘플레이트: {self.pyaudio_service.rate_in}Hz")
            print(f"  - 출력 샘플레이트: {self.pyaudio_service.rate_out}Hz")
            print(f"  - 입력 채널 수: {self.pyaudio_service.channels_in}")
            print(f"  - 출력 채널 수: {self.pyaudio_service.channels_out}")
            
            if not self.pyaudio_accumulated_data:
                print("[DualAudio] ⚠️ PyAudio 누적 데이터가 없습니다")
                return None
            
            # PCM 데이터에 WAV 헤더 추가 (출력 샘플레이트와 채널 사용)
            wav_bytes = self._add_wav_header(
                bytes(self.pyaudio_accumulated_data), 
                self.pyaudio_service.rate_out, 
                self.pyaudio_service.channels_out
            )
            
            print(f"[DualAudio] 🎵 PyAudio WAV 파일 생성 완료: {len(wav_bytes)} bytes")
            return wav_bytes
            
        except Exception as e:
            print(f"[DualAudio] ❌ PyAudio WAV 파일 생성 실패: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def save_pyaudio_wav_file(self, session_id: str = None) -> Optional[str]:
        """PyAudio WAV 파일을 디스크에 저장"""
        try:
            print(f"[DualAudio] 🔍 PyAudio WAV 파일 저장 시작:")
            print(f"  - 누적 데이터 크기: {len(self.pyaudio_accumulated_data):,} bytes")
            print(f"  - PyAudio 활성 상태: {self.is_pyaudio_active}")
            print(f"  - PyAudio 프레임 수: {self.stats['pyaudio_frames']}")
            
            wav_bytes = self.build_pyaudio_wav_bytes()
            if not wav_bytes:
                print("[DualAudio] ❌ WAV 파일 생성 실패")
                return None
            
            # 파일명 생성 (pyaudio_ prefix 추가)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if session_id:
                filename = f"pyaudio_session_{session_id}_{timestamp}.wav"
            else:
                filename = f"pyaudio_{timestamp}.wav"
            
            # recordings 폴더에 저장
            recordings_dir = "recordings"
            os.makedirs(recordings_dir, exist_ok=True)
            filepath = os.path.join(recordings_dir, filename)
            
            with open(filepath, "wb") as f:
                f.write(wav_bytes)
            
            # 통계 업데이트
            self.stats['pyaudio_wav_files'] += 1
            
            print(f"[DualAudio] 💾 PyAudio WAV 파일 저장 완료: {filepath} ({len(wav_bytes)} bytes)")
            return filepath
            
        except Exception as e:
            print(f"[DualAudio] ❌ PyAudio WAV 파일 저장 실패: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _add_wav_header(self, pcm_data: bytes, sample_rate: int, channels: int) -> bytes:
        """PCM 데이터에 WAV 헤더 추가"""
        try:
            import struct
            
            # WAV 헤더 (44 bytes)
            header = bytearray()
            
            # RIFF 헤더
            header.extend(b'RIFF')
            header.extend(struct.pack('<I', 36 + len(pcm_data)))  # 파일 크기
            header.extend(b'WAVE')
            
            # fmt 청크
            header.extend(b'fmt ')
            header.extend(struct.pack('<I', 16))  # fmt 청크 크기
            header.extend(struct.pack('<H', 1))   # 오디오 포맷 (PCM)
            header.extend(struct.pack('<H', channels))  # 채널 수
            header.extend(struct.pack('<I', sample_rate))  # 샘플레이트
            header.extend(struct.pack('<I', sample_rate * channels * 2))  # 바이트레이트
            header.extend(struct.pack('<H', channels * 2))  # 블록 얼라인
            header.extend(struct.pack('<H', 16))  # 비트퍼샘플
            
            # data 청크
            header.extend(b'data')
            header.extend(struct.pack('<I', len(pcm_data)))  # 데이터 크기
            
            # WAV 헤더 + PCM 데이터
            wav_file = header + pcm_data
            
            return bytes(wav_file)
            
        except Exception as e:
            print(f"[DualAudio] ❌ WAV 헤더 추가 실패: {e}")
            return pcm_data

    def get_pyaudio_status(self) -> dict:
        """PyAudio 상태 정보 반환"""
        try:
            status = {
                'is_active': self.is_pyaudio_active,
                'frames_received': self.stats['pyaudio_frames'],
                'total_bytes': self.stats['total_bytes'],
                'accumulated_data_size': len(self.pyaudio_accumulated_data),
                'start_time': self.pyaudio_start_time,
                'errors': self.stats['errors']
            }
            
            # PyAudio 서비스 상태 추가
            if hasattr(self.pyaudio_service, 'pa'):
                status['pyaudio_initialized'] = self.pyaudio_service.pa is not None
                status['pyaudio_instance'] = str(self.pyaudio_service.pa)
            else:
                status['pyaudio_initialized'] = False
                status['pyaudio_instance'] = 'None'
            
            # 샘플레이트 및 채널 정보
            status['sample_rate'] = getattr(self.pyaudio_service, 'rate', 'Unknown')
            status['channels'] = getattr(self.pyaudio_service, 'channels', 'Unknown')
            
            return status
            
        except Exception as e:
            return {'error': str(e)}
    
    def debug_pyaudio_status(self):
        """PyAudio 상태를 상세히 출력"""
        print(f"[DualAudio] 🔍 PyAudio 상태 디버그:")
        status = self.get_pyaudio_status()
        for key, value in status.items():
            print(f"  - {key}: {value}")
