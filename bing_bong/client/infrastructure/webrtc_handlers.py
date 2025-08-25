# webrtc_handlers.py - Streamlit WebRTC 오디오 품질 개선
import time
import queue
import io
import soundfile as sf
import av
import cv2
import asyncio
import numpy as np
from typing import Callable, Optional, Any
from streamlit_webrtc import AudioProcessorBase
from infrastructure.VADBlockAssembler import VADBlockAssembler
import webrtcvad  # VAD 직접 사용


from infrastructure.metrics import Metrics
from infrastructure.http_client import post_json
from infrastructure.config import JPEG_QUALITY, SAMPLE_EVERY

def make_video_frame_callback(
    raw_frame_q: "queue.Queue[av.VideoFrame]",
    metrics: Metrics,
    sample_every: int = SAMPLE_EVERY,
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
            # 원본 프레임만 큐에 넣고 즉시 반환(CPU 작업 없음)
            try:
                if raw_frame_q.full():
                    try: raw_frame_q.get_nowait()
                    except Exception: pass
                raw_frame_q.put_nowait(frame)
                metrics.inc_enq()
            except Exception:
                pass
        return frame
    return video_frame_callback


class AudioAccumulator:
    """WAV 파일 누적기: VAD 블록들을 WAV 파일로 변환하여 누적"""
    
    def __init__(self):
        self._wav_files = []  # WAV 파일들을 누적하는 리스트
        self._total_duration = 0.0  # 총 오디오 길이 (초)
        self._total_bytes = 0  # 총 바이트 수
        self._sample_rate = 16000  # 기본 샘플레이트
        self._channels = 1  # 기본 채널 수
        
        print("[AudioAccumulator] 🎵 WAV 파일 누적기 초기화")
    
    def add_vad_block(self, vad_block: bytes):
        """VAD 블록을 WAV 파일로 변환하여 누적"""
        try:
            if not vad_block or len(vad_block) == 0:
                print("[AudioAccumulator] ⚠️ 빈 VAD 블록 무시")
                return
            
            # VAD 블록을 WAV 파일로 변환
            wav_bytes = self._convert_vad_block_to_wav(vad_block)
            
            # WAV 파일 정보 계산
            duration = len(wav_bytes) / (self._sample_rate * 2 * self._channels)  # 16-bit = 2 bytes
            
            # 누적
            self._wav_files.append({
                'wav_bytes': wav_bytes,
                'duration': duration,
                'size_bytes': len(wav_bytes),
                'timestamp': time.time()
            })
            
            self._total_duration += duration
            self._total_bytes += len(wav_bytes)
            
            print(f"[AudioAccumulator] ✅ WAV 파일 누적: {duration:.1f}초, {len(wav_bytes)} bytes (총 {len(self._wav_files)}개, {self._total_duration:.1f}초)")
            
        except Exception as e:
            print(f"[AudioAccumulator] ❌ VAD 블록 추가 실패: {e}")
    
    def _convert_vad_block_to_wav(self, vad_block: bytes) -> bytes:
        """VAD 블록을 WAV 파일로 변환"""
        try:
            # VAD 블록은 이미 16kHz, 16-bit, mono PCM 데이터
            # WAV 헤더 추가
            wav_bytes = self._add_wav_header(vad_block, self._sample_rate, self._channels)
            return wav_bytes
            
        except Exception as e:
            print(f"[AudioAccumulator] ❌ WAV 변환 실패: {e}")
            return vad_block
    
    def _add_wav_header(self, pcm_data: bytes, sample_rate: int, channels: int) -> bytes:
        """PCM 데이터에 WAV 헤더 추가"""
        try:
            # WAV 파일 구조 생성
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
            print(f"[AudioAccumulator] ❌ WAV 헤더 추가 실패: {e}")
            return pcm_data
    
    def get_accumulated_wav_files(self) -> list:
        """누적된 WAV 파일들 반환"""
        return self._wav_files.copy()
    
    def get_combined_wav(self) -> Optional[bytes]:
        """모든 WAV 파일을 하나로 결합"""
        try:
            if not self._wav_files:
                print("[AudioAccumulator] ⚠️ 누적된 WAV 파일이 없습니다")
                return None
            
            # 모든 WAV 파일의 PCM 데이터 추출 및 결합
            combined_pcm = bytearray()
            
            for wav_file in self._wav_files:
                # WAV 헤더 제거 (44 bytes)하고 PCM 데이터만 추출
                pcm_data = wav_file['wav_bytes'][44:]  # WAV 헤더 제거
                combined_pcm.extend(pcm_data)
            
            # 결합된 PCM 데이터에 WAV 헤더 추가
            combined_wav = self._add_wav_header(bytes(combined_pcm), self._sample_rate, self._channels)
            
            print(f"[AudioAccumulator] 🎵 WAV 파일 결합 완료: {len(self._wav_files)}개 → 1개, 총 길이: {self._total_duration:.1f}초, {len(combined_wav)} bytes")
            
            return combined_wav
            
        except Exception as e:
            print(f"[AudioAccumulator] ❌ WAV 파일 결합 실패: {e}")
            return None
    
    def get_audio_info(self) -> dict:
        """오디오 정보 반환"""
        return {
            'wav_files_count': len(self._wav_files),
            'total_duration': self._total_duration,
            'total_bytes': self._total_bytes,
            'sample_rate': self._sample_rate,
            'channels': self._channels,
            'has_data': len(self._wav_files) > 0
        }
    
    def stats(self) -> dict:
        """통계 정보 반환"""
        return {
            'wav_files': len(self._wav_files),
            'total_duration': self._total_duration,
            'total_bytes': self._total_bytes,
            'avg_file_duration': self._total_duration / max(len(self._wav_files), 1),
            'avg_file_size': self._total_bytes / max(len(self._wav_files), 1)
        }
    
    def reset(self):
        """누적기 초기화"""
        self._wav_files.clear()
        self._total_duration = 0.0
        self._total_bytes = 0
        print("[AudioAccumulator] 🔄 누적기 초기화 완료")
    
    def build_wav_bytes(self) -> Optional[bytes]:
        """최종 WAV 파일 생성 (기존 메서드와 호환성 유지)"""
        return self.get_combined_wav()


class AudioProcessor(AudioProcessorBase):
    def __init__(self, acc: AudioAccumulator) -> None:
        self._acc = acc

    def recv(self, frame: av.AudioFrame) -> av.AudioFrame:

        # 오디오 프레임을 그대로 반환 (처리 없음)
        return frame


def make_audio_processor_factory(acc: AudioAccumulator):
    def _factory() -> AudioProcessor:
        return AudioProcessor(acc)
    return _factory


def make_vad_audio_callback_with_accumulator(vad_assembler: VADBlockAssembler, acc: AudioAccumulator):
    """
    VAD 블록 기반 오디오 콜백:
    1. 사람의 말인지 먼저 확인
    2. 사람의 말인 경우에만 VAD에 프레임 전달하여 음성 블록 생성
    3. 완성된 VAD 블록을 AudioAccumulator에 누적
    """
    frame_counter = {"count": 0, "last_timestamp": 0, "dropped_frames": 0}  # 프레임 손실 모니터링 추가
    
    def audio_frame_callback(frame: av.AudioFrame) -> av.AudioFrame:
        try:
            frame_counter["count"] += 1
            current_time = time.monotonic()
            
            # 🚨 노이즈 방지: 프레임 간격 모니터링
            if frame_counter["last_timestamp"] > 0:
                interval = current_time - frame_counter["last_timestamp"]
                if interval > 0.025:  # 25ms 이상 간격이면 프레임 손실 의심
                    frame_counter["dropped_frames"] += 1
                    print(f"[DEBUG-AUDIO] 🚨 프레임 간격 이상: {interval:.3f}s (손실 프레임: {frame_counter['dropped_frames']}개)")
            
            frame_counter["last_timestamp"] = current_time
            
            # 🔍 DEBUG: 프레임 수신 상태 (올바른 속성 사용)
            print(f"[DEBUG-AUDIO] 🎵 프레임 #{frame_counter['count']} 수신:")
            print(f"  - 샘플레이트: {frame.sample_rate}Hz")
            print(f"  - 레이아웃: {frame.layout.name if hasattr(frame, 'layout') else 'unknown'}")
            print(f"  - 프레임 크기: {frame.samples} samples")
            print(f"  - 데이터 타입: {frame.format}")
            
            # 🔍 DEBUG: VAD 상태 확인
            vad_queue_size = vad_assembler.out_q.qsize()
            print(f"  - VAD 큐 크기: {vad_queue_size}")
            
            # 🚨 노이즈 방지: 큐 오버플로우 경고
            if vad_queue_size > 15:  # 75% 이상 차면 경고
                print(f"[DEBUG-AUDIO] 🚨 VAD 큐가 가득 참: {vad_queue_size}/20")
            
            # 1. VAD에 프레임 전달 (음성 블록 생성용)
            print(f"[DEBUG-AUDIO] 📤 VAD에 프레임 전달 중...")
            vad_assembler.push_av_frame(frame)
            print(f"[DEBUG-AUDIO] ✅ VAD에 프레임 전달 완료")
            
            # 🔍 DEBUG: VAD 처리 후 상태
            vad_queue_size_after = vad_assembler.out_q.qsize()
            print(f"  - VAD 처리 후 큐 크기: {vad_queue_size_after}")
            
            # 2. VAD에서 완성된 음성 블록이 있으면 AudioAccumulator에 추가
            processed_blocks = 0
            total_bytes = 0
            
            print(f"[DEBUG-AUDIO] 🔄 VAD 블록 처리 시작...")
            while not vad_assembler.out_q.empty():
                try:
                    vad_block = vad_assembler.out_q.get_nowait()
                    if vad_block and len(vad_block) > 0:
                        print(f"[DEBUG-AUDIO] 📦 VAD 블록 발견: {len(vad_block)} bytes")
                        
                        # AudioAccumulator에 추가
                        acc.add_vad_block(vad_block)
                        processed_blocks += 1
                        total_bytes += len(vad_block)
                        
                        print(f"[DEBUG-AUDIO] ✅ VAD 블록 AudioAccumulator에 추가됨")
                    else:
                        print(f"[DEBUG-AUDIO] ⚠️ 빈 VAD 블록 무시")
                        
                except queue.Empty:
                    print(f"[DEBUG-AUDIO] 🔄 VAD 큐가 비어있음")
                    break
                except Exception as e:
                    print(f"[DEBUG-AUDIO] ❌ VAD 블록 처리 실패: {e}")
            
            # 🔍 DEBUG: 처리 결과 요약
            print(f"[DEBUG-AUDIO] 📊 프레임 #{frame_counter['count']} 처리 완료:")
            print(f"  - 처리된 VAD 블록: {processed_blocks}개")
            print(f"  - 총 처리 바이트: {total_bytes} bytes")
            print(f"  - AudioAccumulator 상태: {acc.get_audio_info()}")
            
            # 🔍 DEBUG: 주기적 상태 출력 (100프레임마다)
            if frame_counter["count"] % 100 == 0:
                print(f"[DEBUG-AUDIO] 🎯 === 100프레임 처리 완료 ===")
                print(f"  - 총 프레임: {frame_counter['count']}개")
                print(f"  - 손실 프레임: {frame_counter['dropped_frames']}개")
                print(f"  - 손실률: {(frame_counter['dropped_frames'] / frame_counter['count']) * 100:.2f}%")
                print(f"  - VAD 큐 크기: {vad_assembler.out_q.qsize()}")
                print(f"  - AudioAccumulator 블록: {acc.stats()['wav_files']}개")
                print(f"  - AudioAccumulator 바이트: {acc.stats()['total_bytes']} bytes")
                print(f"[DEBUG-AUDIO] ================================")
            
        except Exception as e:
            print(f"[DEBUG-AUDIO] ❌ 오디오 프레임 처리 실패: {e}")
            import traceback
            traceback.print_exc()
            
        return frame
    return audio_frame_callback


def bind_data_channels(ctx, session_id: str, http_client_getter) -> bool:
    """
    streamlit-webrtc 컨텍스트에서 데이터채널이 열리면 /ingest/data로 포워딩.
    app.py에서 연결 직후 한 번 호출해 바인딩하세요.
    """
    try:
        channels = getattr(ctx.state, "data_channels", None)
        if not (ctx.state.playing and channels):
            return False

        dc = channels[0]

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
                pass

        if getattr(ctx.state, "_dc_wired", False):
            return True

        @dc.on("message")
        def _on_msg(message):
            asyncio.create_task(post_data(dc.label or "default", message))

        ctx.state._dc_wired = True
        return True
        
    except Exception:
        return False