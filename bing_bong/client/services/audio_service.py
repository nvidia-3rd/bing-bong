# audio_service.py
import pyaudio
import numpy as np
import threading
import time
import queue
from typing import Optional, Callable
import wave

# ===================== Config =====================
# Audio : 기본 오디오 설정
RATE = 16000               # 샘플링 레이트(Hz). webrtcvad는 8/16/32/48k 지원
CHANNELS = 1               # 채널 수(모노)
SAMPLE_WIDTH = 2           # 샘플 폭(바이트) -> 16-bit PCM = 2바이트
FRAME_MS = 20              # 프레임 길이(ms). webrtcvad는 10/20/30ms만 허용
FRAME_BYTES = int(RATE * (FRAME_MS / 1000.0)) * SAMPLE_WIDTH * CHANNELS  # i.e. 20ms 프레임의 총 바이트 수(= 640 bytes @16kHz)

# VAD params : 음성 감지 및 블록 유효성 검사 기준
VAD_AGGR = 3               # webrtcvad 공격성(0~3). 높을수록 음성으로 더 쉽게 판단
VOICE_RATIO_MIN = 0.10     # 블록 내에서 음성 프레임 비율 최소값(예: 10% 이상이어야 유효)
MIN_VOICE_MS = 1000        # 블록 내 최소 음성 시간(ms). 1초(1000ms) 이상이어야 유효

# Early flush params : 무음 지속 시 조기 전송 기준
SILENCE_TIMEOUT_SEC = 2    # 마지막 음성 이후 2초 이상 무음이면 조기 플러시(Default: 0.5초)
EARLY_MIN_VOICE_MS = 300   # 조기 플러시 시 완화된 최소 누적 음성 시간(0.3초) <- MIN_VOICE_MS보다는 작게
EARLY_VOICE_RATIO_MIN = 0.05 # 조기 플러시 시 완화된 음성 프레임 비율(5%)

# Queues : Thread 간 통신용 큐 설정
FRAME_QUEUE_MAX = 256      # 20ms frames -> Frame queue 크기는 확장 필요해보임.
BLOCK_QUEUE_MAX = 8        # 15s blocks

# 입력장치 선택 (None = 시스템 기본장치)
INPUT_DEVICE_INDEX = None

class PyAudioCaptureService:
    """PyAudio를 사용한 로컬 마이크 캡처 서비스 (참고 코드 스타일)"""
    
    def __init__(self, sample_rate=None, channels=None, chunk_size=None):
        # PyAudio 인스턴스
        self.pa = None
        self.stream = None
        
        # 오디오 설정 (매개변수로 전달받거나 기본값 사용)
        self.rate = sample_rate if sample_rate is not None else RATE
        self.channels = channels if channels is not None else CHANNELS
        self.chunk_size = chunk_size if chunk_size is not None else int(self.rate * (FRAME_MS / 1000.0))
        
        # 상태 관리
        self.stop_event = threading.Event()
        self.is_recording = False
        
        # 오디오 데이터 큐 (참고 코드와 동일한 구조)
        self.frame_queue = queue.Queue(maxsize=FRAME_QUEUE_MAX)   # producer: 20ms frames
        self.block_queue = queue.Queue(maxsize=BLOCK_QUEUE_MAX)   # consumer: 15s (or early) blocks
        
        # VAD 관련 상태
        self.block = bytearray()
        self.voiced_frames = 0
        self.total_frames = 0
        self.last_voice_ts = None
        self.have_any_voice = False
        self.block_start_ts = time.time()
        
        # 스레드
        self.recorder_thread = None
        self.vad_blocker_thread = None
        
        # 통계
        self.stats = {
            'frames_captured': 0,
            'blocks_created': 0,
            'blocks_dropped': 0,
            'start_time': None,
            'errors': 0
        }
        
        print(f"[PyAudio] 🎤 서비스 초기화: {self.rate}Hz, {self.channels}ch, {FRAME_MS}ms 프레임")
    
    def initialize(self) -> bool:
        """PyAudio 초기화"""
        try:
            self.pa = pyaudio.PyAudio()
            
            # 사용 가능한 디바이스 확인
            device_count = self.pa.get_device_count()
            print(f"[PyAudio] 사용 가능한 오디오 디바이스: {device_count}개")
            
            # 입력 디바이스 찾기
            input_devices = []
            for i in range(device_count):
                try:
                    device_info = self.pa.get_device_info_by_index(i)
                    if device_info['maxInputChannels'] > 0:
                        input_devices.append({
                            'index': i,
                            'name': device_info['name'],
                            'channels': device_info['maxInputChannels'],
                            'sample_rate': device_info['defaultSampleRate']
                        })
                        print(f"[PyAudio] 입력 디바이스 {i}: {device_info['name']} ({device_info['maxInputChannels']}ch, {device_info['defaultSampleRate']}Hz)")
                except Exception as e:
                    print(f"[PyAudio] 디바이스 {i} 정보 조회 실패: {e}")
            
            if not input_devices:
                print("[PyAudio] ❌ 사용 가능한 입력 디바이스가 없습니다")
                return False
            
            # 기본 입력 디바이스 선택 (첫 번째 사용 가능한 것)
            self.selected_device = input_devices[0]
            print(f"[PyAudio] ✅ 선택된 디바이스: {self.selected_device['name']}")
            
            return True
            
        except Exception as e:
            print(f"[PyAudio] ❌ 초기화 실패: {e}")
            return False
    
    def open_stream(self, device_index=None):
        """Open mic stream; frames_per_buffer matches FRAME_MS."""
        '''pyaudio 스트림을 열고 20ms 프레임 단위로 읽도록 설정'''
        frames_per_buffer = int(self.rate * (FRAME_MS / 1000.0))  # samples per frame
        stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=frames_per_buffer,
            start=True,
        )
        return stream
    
    def start_recording(self):
        """녹음 시작"""
        if self.is_recording:
            print("[PyAudio] ⚠️ 이미 녹음 중입니다")
            return False
        
        try:
            # 오디오 스트림 열기
            self.stream = self.open_stream(device_index=INPUT_DEVICE_INDEX)
            print(f"🎙️ Recording... (Ctrl+C to stop)")
            
            # 워밍업: 첫 프레임을 한번 읽어 내부 버퍼 안정화
            try:
                _ = self.stream.read(int(RATE * (FRAME_MS / 1000.0)), exception_on_overflow=False)
            except Exception:
                pass
            
            # 녹음 스레드 시작
            self.is_recording = True
            self.stop_event.clear()
            self.stats['start_time'] = time.time()
            
            self.recorder_thread = threading.Thread(target=self._recorder_thread, name="recorder", daemon=False)
            self.vad_blocker_thread = threading.Thread(target=self._vad_blocker_thread, name="vad", daemon=False)
            
            self.recorder_thread.start()
            self.vad_blocker_thread.start()
            
            print(f"[PyAudio] 🎤 녹음 시작: {self.selected_device['name']}")
            return True
            
        except Exception as e:
            print(f"[PyAudio] ❌ 녹음 시작 실패: {e}")
            return False
    
    def _recorder_thread(self):
        """마이크에서 20ms 프레임을 지속적으로 읽어 frame_queue에 적재"""
        try:
            samples_per_frame = int(self.rate * (FRAME_MS / 1000.0))
            frame_bytes = samples_per_frame * SAMPLE_WIDTH * self.channels
            
            while not self.stop_event.is_set():
                data = self.stream.read(samples_per_frame, exception_on_overflow=False)
                if not data:
                    continue
                
                # 예상 프레임 크기와 다르면 버림
                if len(data) != frame_bytes:
                    continue
                
                # stream된 프레임을 frame_queue에 넣기(최대 대기시간 0.2)
                try:
                    self.frame_queue.put(data, timeout=0.2)
                    
                # frame_queue가 가득 찼을 때 처리 정책
                except queue.Full:
                    print("queue full, dropping oldest frame")
                    try:
                        _ = self.frame_queue.get_nowait()
                        self.frame_queue.task_done()
                    except queue.Empty:
                        pass
                    try:
                        self.frame_queue.put(data, timeout=0.05)
                    except queue.Full:
                        pass
                        
        except Exception as e:
            if not self.stop_event.is_set():
                print(f"[recorder] error: {e}")
        finally:
            print("🎤 Recorder stopped")
    
    def _vad_blocker_thread(self):
        """
        STT에 보낼 블록을 생성하는 함수.
        20ms 프레임들을 모아 정확히 15초 블록을 만들거나,
        무음이 오래 지속되면(e.g. 2초 이상은 발화 종료로 간주) 15초 이전에 조기 플러시 블록 생성.
        VAD 비율/누적 음성시간 기준으로 유효한 블록만 block_queue로 전달.
        """
        try:
            import webrtcvad
            vad = webrtcvad.Vad(VAD_AGGR)
        except ImportError:
            print("[PyAudio] webrtcvad 모듈이 없습니다. VAD 기능을 사용할 수 없습니다.")
            return
        
        while not self.stop_event.is_set():
            frame = None
            try:
                frame = self.frame_queue.get(timeout=0.2)  # 20ms frame
            except queue.Empty:
                pass

            now = time.time()

            if frame is not None:
                # VAD로 현재 프레임이 음성인지 판정(20ms 단위)
                try:
                    is_voiced = vad.is_speech(frame, self.rate)
                except Exception:
                    is_voiced = False

                # 블록 바이트에 프레임 추가하고 카운터 갱신
                self.block.extend(frame)
                self.total_frames += 1
                if is_voiced:
                    self.voiced_frames += 1
                    self.last_voice_ts = now
                    self.have_any_voice = True

                # 15초 분량이 채워진 경우 -> 유효성 판단 후 전송/드롭
                if len(self.block) >= (self.rate * 15 * SAMPLE_WIDTH * self.channels):  # 15초 블록
                    voice_ratio = (self.voiced_frames / self.total_frames) if self.total_frames else 0.0
                    voiced_ms = self.voiced_frames * FRAME_MS

                    # 비율/누적시간 기준을 만족하면 block_queue에 전송
                    if voice_ratio >= VOICE_RATIO_MIN and voiced_ms >= MIN_VOICE_MS:
                        try:
                            self.block_queue.put(bytes(self.block), timeout=0.5)
                            self.stats['blocks_created'] += 1
                        except queue.Full:
                            print("[vad] block queue full; drop full block")
                            self.stats['blocks_dropped'] += 1

                    # reset for next block
                    self.block.clear()
                    self.voiced_frames = 0
                    self.total_frames = 0
                    self.have_any_voice = False
                    self.last_voice_ts = None
                    self.block_start_ts = now

                self.frame_queue.task_done()

            # 조기 플러시 상황
            if (len(self.block) > 0 and self.have_any_voice and 
                self.last_voice_ts is not None and 
                (now - self.last_voice_ts) >= SILENCE_TIMEOUT_SEC and
                len(self.block) < (self.rate * 15 * SAMPLE_WIDTH * self.channels)):
                
                voice_ratio = (self.voiced_frames / self.total_frames) if self.total_frames else 0.0
                voiced_ms = self.voiced_frames * FRAME_MS

                # 조기 플러시 기준(완화된 임계치) 통과 시 전송
                if voiced_ms >= EARLY_MIN_VOICE_MS and voice_ratio >= EARLY_VOICE_RATIO_MIN:
                    try:
                        self.block_queue.put(bytes(self.block), timeout=0.5)
                        self.stats['blocks_created'] += 1
                    except queue.Full:
                        print("[vad] block queue full; drop early block")
                        self.stats['blocks_dropped'] += 1

                # reset
                self.block.clear()
                self.voiced_frames = 0
                self.total_frames = 0
                self.have_any_voice = False
                self.last_voice_ts = None
                self.block_start_ts = now
    
    def stop_recording(self):
        """녹음 중지"""
        if not self.is_recording:
            return
        
        try:
            self.is_recording = False
            self.stop_event.set()
            
            # 스레드 대기
            if self.recorder_thread and self.recorder_thread.is_alive():
                self.recorder_thread.join(timeout=5)
            if self.vad_blocker_thread and self.vad_blocker_thread.is_alive():
                self.vad_blocker_thread.join(timeout=5)
            
            # 스트림 닫기
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
            
            # 통계 계산
            if self.stats['start_time']:
                duration = time.time() - self.stats['start_time']
                print(f"[PyAudio] 🛑 녹음 중지: {duration:.1f}초, {self.stats['blocks_created']} 블록 생성, {self.stats['blocks_dropped']} 블록 드롭")
            
        except Exception as e:
            print(f"[PyAudio] 녹음 중지 오류: {e}")
    
    def get_audio_block(self, timeout: float = 0.2) -> Optional[bytes]:
        """오디오 블록 가져오기"""
        try:
            return self.block_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_stats(self) -> dict:
        """통계 정보 반환"""
        stats = self.stats.copy()
        if stats['start_time']:
            stats['duration'] = time.time() - stats['start_time']
            stats['frame_queue_size'] = self.frame_queue.qsize()
            stats['block_queue_size'] = self.block_queue.qsize()
            stats['is_recording'] = self.is_recording
            stats['selected_device'] = getattr(self, 'selected_device', {}).get('name', 'Unknown')
        return stats
    
    def cleanup(self):
        """리소스 정리"""
        self.stop_recording()
        
        if self.pa:
            self.pa.terminate()
            self.pa = None
        
        print("[PyAudio] 🧹 리소스 정리 완료")

class AudioService:
    """클라이언트용 AudioService - HTTP 클라이언트를 통해 서버와 통신"""
    
    def __init__(self, http_client):
        self.http_client = http_client
    
    async def process_audio(self, audio_data: bytes) -> dict:
        """오디오 데이터를 서버로 전송하여 처리"""
        try:
            # 서버로 오디오 데이터 전송
            response = await self.http_client.post_audio(audio_data)
            return response
        except Exception as e:
            print(f"[AudioService] 오디오 처리 오류: {e}")
            return {"error": str(e)}
    
    async def upload_audio(self, session_id: str, audio_data: bytes, errbuf) -> dict:
        """오디오 데이터를 서버에 업로드"""
        try:
            # http_client에서 post_audio 함수 import
            from infrastructure.http_client import post_audio
            from infrastructure.http_client import get_http
            
            # HTTP 클라이언트 가져오기
            http = get_http()
            
            # metrics는 None으로 전달 (post_audio에서 사용하지 않음)
            metrics = None
            
            # 서버로 오디오 데이터 전송
            response = await post_audio(http, session_id, audio_data, metrics, errbuf)
            
            if response is None:
                return {"error": "오디오 업로드 실패"}
            
            return {"success": True, "response": response}
        except Exception as e:
            error_msg = f"오디오 업로드 오류: {e}"
            print(f"[AudioService] {error_msg}")
            errbuf.add_error(error_msg)
            return {"error": error_msg}
    
    def extract_transcript(self, response: dict) -> str:
        """응답에서 전사 결과 추출"""
        try:
            if "error" in response:
                return f"오류: {response['error']}"
            
            # 응답 구조에 따라 전사 결과 추출
            if "transcript" in response:
                return response["transcript"]
            elif "text" in response:
                return response["text"]
            elif "result" in response:
                return response["result"]
            else:
                return "전사 결과를 찾을 수 없습니다"
        except Exception as e:
            print(f"[AudioService] 전사 결과 추출 오류: {e}")
            return f"전사 결과 추출 오류: {e}"
    
    def cleanup(self):
        """리소스 정리"""
        pass
