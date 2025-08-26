# audio_service.py
import pyaudio
import numpy as np
import threading
import time
import queue
from typing import Optional, Callable
import wave
import webrtcvad
from scipy.signal import resample_poly

# ===================== Config =====================
# Audio : 기본 오디오 설정
RATE = 16000               # 샘플링 레이트(Hz). webrtcvad는 8/16/32/48k 지원
CHANNELS = 1               # 채널 수(모노)
SAMPLE_WIDTH = 2           # 샘플 폭(바이트) -> 16-bit PCM = 2바이트
FRAME_MS = 20              # 프레임 길이(ms). webrtcvad는 10/20/30ms만 허용
FRAME_BYTES = int(RATE * (FRAME_MS / 1000.0)) * SAMPLE_WIDTH * CHANNELS  # i.e. 20ms 프레임의 총 바이트 수(= 640 bytes @16kHz)

# BlackHole 지원 설정
BLACKHOLE_RATE_IN = 48000  # BlackHole 기본 48kHz
BLACKHOLE_CH_IN = 2        # BlackHole 기본 2채널
BLACKHOLE_RATE_OUT = 16000 # 출력 16kHz
BLACKHOLE_FRAME_MS = 100   # 100ms 프레임 (처리 속도 조절)

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
    """PyAudio를 사용한 로컬 마이크 캡처 서비스 (BlackHole 지원)"""
    
    def __init__(self, sample_rate=None, channels=None, chunk_size=None, use_blackhole=False):
        # PyAudio 인스턴스
        self.pa = None
        self.stream = None
        
        # BlackHole 사용 여부
        self.use_blackhole = use_blackhole
        
        # 오디오 설정 (BlackHole 사용 시 48kHz, 일반 사용 시 16kHz)
        if self.use_blackhole:
            self.rate_in = BLACKHOLE_RATE_IN
            self.rate_out = BLACKHOLE_RATE_OUT
            self.channels_in = BLACKHOLE_CH_IN
            self.channels_out = CHANNELS
            self.chunk_size = int(self.rate_in * (BLACKHOLE_FRAME_MS / 1000.0))
        else:
            self.rate_in = sample_rate if sample_rate is not None else RATE
            self.rate_out = self.rate_in
            self.channels_in = channels if channels is not None else CHANNELS
            self.channels_out = self.channels_in
            self.chunk_size = chunk_size if chunk_size is not None else int(self.rate_in * (FRAME_MS / 1000.0))
        
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
        
        # 콜백 함수
        self.callback = None
        
        # 통계
        self.stats = {
            'frames_captured': 0,
            'blocks_created': 0,
            'blocks_dropped': 0,
            'start_time': None,
            'errors': 0
        }
        
        print(f"[PyAudio] 🎤 서비스 초기화: {self.rate_in}Hz → {self.rate_out}Hz, {self.channels_in}ch → {self.channels_out}ch, {FRAME_MS}ms 프레임")
        if self.use_blackhole:
            print(f"[PyAudio] 🎧 BlackHole 모드 활성화")
            print(f"[PyAudio] ⏱️ 프레임 크기: {BLACKHOLE_FRAME_MS}ms (처리 속도 조절)")
    
    def to_mono_16k_i16(self, raw: bytes) -> np.ndarray:
        """48kHz 다채널 → 16kHz 모노 변환 (BlackHole용)"""
        try:
            # BlackHole 디바이스의 실제 채널 수 확인 (PyAudio 호환성 고려)
            if hasattr(self, 'selected_device') and self.selected_device:
                device_channels = self.selected_device.get('channels', self.channels_in)
                # PyAudio 호환성을 위해 1채널(모노)로 강제 설정
                actual_channels = 1
            else:
                actual_channels = 1
            
            # 리샘플링 로그는 한 번만 출력 (중복 방지)
            if not hasattr(self, '_resample_logged'):
                print(f"[PyAudio] 🔄 리샘플링: {device_channels}채널 → 1채널 (PyAudio는 1채널로 처리)")
                self._resample_logged = True
            
            # 바이트를 int16 배열로 변환
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32).reshape(-1, actual_channels)
            
            # 다채널을 모노로 변환 (평균)
            mono = np.mean(x, axis=1)
            
            # 48kHz → 16kHz 리샘플링
            y = resample_poly(mono, self.rate_out, self.rate_in).astype(np.float32)
            
            # 클리핑하여 int16 범위로 제한
            y = np.clip(y, -32768, 32767).astype(np.int16)
            
            return y
        except Exception as e:
            print(f"[PyAudio] 리샘플링 오류: {e}")
            # 오류 시 원본 데이터 반환
            return np.frombuffer(raw, dtype=np.int16)
    
    def find_blackhole_device(self):
        """BlackHole 디바이스 찾기"""
        if not self.pa:
            return None
            
        device_count = self.pa.get_device_count()
        blackhole_devices = []
        
        for i in range(device_count):
            try:
                device_info = self.pa.get_device_info_by_index(i)
                device_name = device_info['name'].lower()
                
                # BlackHole 관련 키워드로 검색
                if any(keyword in device_name for keyword in ['blackhole', 'black hole', 'bh']):
                    blackhole_devices.append({
                        'index': i,
                        'name': device_info['name'],
                        'channels': device_info['maxInputChannels'],
                        'sample_rate': device_info['defaultSampleRate']
                    })
                    print(f"[PyAudio] 🎧 BlackHole 디바이스 발견: {device_info['name']} (인덱스: {i})")
            except Exception as e:
                continue
        
        return blackhole_devices
    
    def initialize(self) -> bool:
        """PyAudio 초기화"""
        try:
            self.pa = pyaudio.PyAudio()
            
            # 사용 가능한 디바이스 확인
            device_count = self.pa.get_device_count()
            print(f"[PyAudio] 사용 가능한 오디오 디바이스: {device_count}개")
            
            # BlackHole 사용 시 BlackHole 디바이스 우선 검색
            if self.use_blackhole:
                blackhole_devices = self.find_blackhole_device()
                if blackhole_devices:
                    # BlackHole 2ch를 우선적으로 선택 (PyAudio 호환성)
                    preferred_device = None
                    for device in blackhole_devices:
                        if '2ch' in device['name'].lower():
                            preferred_device = device
                            break
                    
                    # 2ch가 없으면 첫 번째 디바이스 선택
                    if not preferred_device:
                        preferred_device = blackhole_devices[0]
                    
                    self.selected_device = preferred_device
                    print(f"[PyAudio] ✅ BlackHole 디바이스 선택: {self.selected_device['name']}")
                    
                    # PyAudio 호환성을 위해 채널 수 조정
                    if self.selected_device['channels'] > 8:
                        print(f"[PyAudio] ⚠️ {self.selected_device['channels']}채널은 PyAudio에서 지원하지 않음")
                        print(f"[PyAudio] 🔧 2채널로 제한하여 사용")
                        self.selected_device['channels'] = 2
                    
                    return True
                else:
                    print("[PyAudio] ⚠️ BlackHole 디바이스를 찾을 수 없습니다. 일반 모드로 전환합니다.")
                    self.use_blackhole = False
                    self.rate_in = RATE
                    self.rate_out = RATE
                    self.channels_in = CHANNELS
                    self.channels_out = CHANNELS
                    self.chunk_size = int(self.rate_in * (FRAME_MS / 1000.0))
            
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
        if self.use_blackhole:
            # BlackHole 사용 시 48kHz, 실제 채널 수로 스트림 열기
            frames_per_buffer = int(self.rate_in * (BLACKHOLE_FRAME_MS / 1000.0))
            
            # BlackHole 디바이스의 실제 채널 수 확인
            if hasattr(self, 'selected_device') and self.selected_device:
                device_channels = self.selected_device.get('channels', self.channels_in)
                print(f"[PyAudio] 🎧 BlackHole 디바이스 채널 수: {device_channels}")
                
                # PyAudio 호환성을 위해 1채널(모노)로 강제 설정
                print(f"[PyAudio] 🔧 PyAudio 호환성을 위해 1채널(모노)로 강제 설정")
                actual_channels = 1
            else:
                actual_channels = 1  # 기본값도 1채널
            
            try:
                stream = self.pa.open(
                    format=pyaudio.paInt16,
                    channels=actual_channels,
                    rate=self.rate_in,
                    input=True,
                    input_device_index=device_index,
                    frames_per_buffer=frames_per_buffer,
                    start=True,
                )
                print(f"[PyAudio] ✅ 스트림 열기 성공: {actual_channels}채널, {self.rate_in}Hz")
            except Exception as e:
                print(f"[PyAudio] ❌ 스트림 열기 실패: {e}")
                print(f"[PyAudio] 🔧 1채널로 재시도...")
                try:
                    stream = self.pa.open(
                        format=pyaudio.paInt16,
                        channels=1,  # 강제로 1채널
                        rate=self.rate_in,
                        input=True,
                        input_device_index=device_index,
                        frames_per_buffer=frames_per_buffer,
                        start=True,
                    )
                    print(f"[PyAudio] ✅ 1채널 스트림 열기 성공")
                except Exception as e2:
                    print(f"[PyAudio] ❌ 1채널 스트림도 실패: {e2}")
                    raise e2
        else:
            # 일반 모드
            frames_per_buffer = int(self.rate_in * (FRAME_MS / 1000.0))
            stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=self.channels_in,
                rate=self.rate_in,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=frames_per_buffer,
                start=True,
            )
        return stream
    
    def start_recording(self, callback=None):
        """녹음 시작"""
        if self.is_recording:
            print("[PyAudio] ⚠️ 이미 녹음 중입니다")
            return False
        
        # 콜백 함수 저장
        self.callback = callback
        
        try:
            # 오디오 스트림 열기
            self.stream = self.open_stream(device_index=INPUT_DEVICE_INDEX)
            print(f"🎙️ Recording... (Ctrl+C to stop)")
            
            # 워밍업: 첫 프레임을 한번 읽어 내부 버퍼 안정화
            try:
                if self.use_blackhole:
                    _ = self.stream.read(int(self.rate_in * (BLACKHOLE_FRAME_MS / 1000.0)), exception_on_overflow=False)
                else:
                    _ = self.stream.read(int(self.rate_in * (FRAME_MS / 1000.0)), exception_on_overflow=False)
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
            if self.use_blackhole:
                samples_per_frame = int(self.rate_in * (BLACKHOLE_FRAME_MS / 1000.0))
                # BlackHole 디바이스의 실제 채널 수 사용 (PyAudio 호환성 고려)
                if hasattr(self, 'selected_device') and self.selected_device:
                    device_channels = self.selected_device.get('channels', self.channels_in)
                    # PyAudio 호환성을 위해 1채널(모노)로 강제 설정
                    actual_channels = 1
                else:
                    actual_channels = 1
                frame_bytes = samples_per_frame * SAMPLE_WIDTH * actual_channels
            else:
                samples_per_frame = int(self.rate_in * (FRAME_MS / 1000.0))
                frame_bytes = samples_per_frame * SAMPLE_WIDTH * self.channels_in
            
            while not self.stop_event.is_set():
                data = self.stream.read(samples_per_frame, exception_on_overflow=False)
                if not data:
                    continue
                
                # 예상 프레임 크기와 다르면 버림
                if len(data) != frame_bytes:
                    continue
                
                # BlackHole 사용 시 리샘플링 수행
                if self.use_blackhole:
                    try:
                        # 48kHz → 16kHz 변환
                        pcm16_16k = self.to_mono_16k_i16(data)
                        # 변환된 데이터를 바이트로 변환하여 큐에 추가
                        processed_data = pcm16_16k.tobytes()
                    except Exception as e:
                        print(f"[PyAudio] 리샘플링 오류, 원본 데이터 사용: {e}")
                        processed_data = data
                else:
                    processed_data = data
                
                # 콜백 함수 호출 (있는 경우)
                if hasattr(self, 'callback') and self.callback:
                    try:
                        self.callback(processed_data)
                    except Exception as e:
                        print(f"[PyAudio] 콜백 호출 오류: {e}")
                
                # stream된 프레임을 frame_queue에 넣기(최대 대기시간 0.2)
                try:
                    self.frame_queue.put(processed_data, timeout=0.2)
                    self.stats['frames_captured'] += 1
                    
                # frame_queue가 가득 찼을 때 처리 정책
                except queue.Full:
                    print("queue full, dropping oldest frame")
                    try:
                        _ = self.frame_queue.get_nowait()
                        self.frame_queue.task_done()
                    except queue.Empty:
                        pass
                    try:
                        self.frame_queue.put(processed_data, timeout=0.05)
                    except queue.Full:
                        pass
                        
        except Exception as e:
            if not self.stop_event.is_set():
                print(f"[recorder] error: {e}")
                self.stats['errors'] += 1
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
                    is_voiced = vad.is_speech(frame, self.rate_out) # VAD는 출력 샘플링 레이트를 사용
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
                if len(self.block) >= (self.rate_out * 15 * SAMPLE_WIDTH * self.channels_out):  # 15초 블록
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
                len(self.block) < (self.rate_out * 15 * SAMPLE_WIDTH * self.channels_out)):
                
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
            
            print(f"[AUDIO] 🚀 오디오 업로드 시작: {len(audio_data)} bytes")
            
            # 서버로 오디오 데이터 전송
            response = await post_audio(http, session_id, audio_data, metrics, errbuf)
            
            if response is None:
                return {"error": "오디오 업로드 실패"}
            
            print(f"[AUDIO] 📡 서버 응답: {response}")
            return {"success": True, "response": response}
        except Exception as e:
            error_msg = f"오디오 업로드 오류: {e}"
            print(f"[AUDIO] {error_msg}")
            errbuf.add_error(error_msg)
            return {"error": error_msg}
    
    def extract_transcript(self, response: dict) -> str:
        """응답에서 전사 결과 추출"""
        try:
            print(f"[AUDIO] 📝 전사 결과 추출 시작")
            print(f"[AUDIO] 📝 입력 응답: {response}")
            print(f"[AUDIO] 📝 응답 타입: {type(response)}")
            
            if "error" in response:
                print(f"[AUDIO] ❌ 오류 발견: {response['error']}")
                return f"오류: {response['error']}"
            
            # success=True, response=Response 객체 구조인지 확인
            if "success" in response and "response" in response:
                print(f"[AUDIO] 🔍 중첩 응답 구조 발견")
                http_response = response["response"]
                print(f"[AUDIO] 🔍 HTTP 응답: {http_response}")
                
                # HTTP 응답 객체에서 JSON 데이터 추출
                if hasattr(http_response, 'json'):
                    try:
                        json_data = http_response.json()
                        print(f"[AUDIO] 🔍 JSON 데이터: {json_data}")
                        
                        if "transcript" in json_data:
                            transcript = json_data["transcript"]
                            print(f"[AUDIO] ✅ transcript 발견: {transcript}")
                            if transcript is None or transcript == "":
                                return "전사 결과가 비어있습니다"
                            return transcript
                        else:
                            print(f"[AUDIO] ❌ JSON에서 transcript 필드를 찾을 수 없음. 사용 가능한 키: {list(json_data.keys())}")
                            return "전사 결과를 찾을 수 없습니다"
                    except Exception as json_e:
                        print(f"[AUDIO] ❌ JSON 파싱 오류: {json_e}")
                        return f"JSON 파싱 오류: {json_e}"
                else:
                    print(f"[AUDIO] ❌ HTTP 응답에 json() 메서드가 없음")
                    return "HTTP 응답을 파싱할 수 없습니다"
            
            # 직접 응답에서 transcript 찾기
            if "transcript" in response:
                transcript = response["transcript"]
                print(f"[AUDIO] ✅ transcript 발견: {transcript}")
                if transcript is None or transcript == "":
                    return "전사 결과가 비어있습니다"
                return transcript
            elif "text" in response:
                print(f"[AUDIO] ✅ text 발견: {response['text']}")
                return response["text"]
            elif "result" in response:
                print(f"[AUDIO] ✅ result 발견: {response['result']}")
                return response["result"]
            else:
                print(f"[AUDIO] ❌ 전사 필드를 찾을 수 없음. 사용 가능한 키: {list(response.keys())}")
                return "전사 결과를 찾을 수 없습니다"
        except Exception as e:
            print(f"[AUDIO] ❌ 전사 결과 추출 오류: {e}")
            return f"전사 결과 추출 오류: {e}"
    

