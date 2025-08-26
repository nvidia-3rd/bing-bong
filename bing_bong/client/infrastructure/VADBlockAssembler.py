# infrastructure/VADBlockAssembler.py
import asyncio
import time
from collections import deque
from math import gcd
from typing import Optional, Deque


import av
import numpy as np
import webrtcvad
from scipy.signal import resample_poly

def _to_mono_f32(arr: np.ndarray) -> np.ndarray:
    """스테레오/멀티채널을 모노로 변환"""
    if arr.ndim == 1:
        return arr.astype(np.float32, copy=False)
    elif arr.ndim == 2:
        if arr.shape[0] == 1:  # 이미 모노
            return arr[0].astype(np.float32, copy=False)
        else:  # 다운믹스
            return np.mean(arr, axis=0, dtype=np.float32)
    else:
        return arr.flatten().astype(np.float32, copy=False)

def _resample_f32(arr: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """리샘플링"""
    if src_rate == dst_rate:
        return arr
    if len(arr) == 0:
        return arr
    
    g = gcd(src_rate, dst_rate)
    up = dst_rate // g
    down = src_rate // g
    return resample_poly(arr, up, down).astype(np.float32, copy=False)

def _f32_to_i16(arr: np.ndarray) -> np.ndarray:
    """float32를 int16으로 변환"""
    return np.clip(arr * 32767.0, -32768, 32767).astype(np.int16)



class VADBlockAssembler:
    def __init__(self, out_queue: Optional[asyncio.Queue] = None):
        # 🚨 노이즈 방지: 큐 크기 증가
        self.out_q: asyncio.Queue = out_queue or asyncio.Queue(maxsize=20)  # 8 → 20으로 증가

        self._pcm16_buf = bytearray()
        self._block = bytearray()
        self._voiced_frames = 0
        self._total_frames = 0
        self._have_any_voice = False
        self._last_voice_ts: Optional[float] = None

        # VAD 민감도를 높게 설정 (0: 낮음, 3: 높음)
        self._vad = webrtcvad.Vad(1)  # 3 → 1로 변경 (더 정확한 음성 감지)
        self._smooth: Deque[int] = deque(maxlen=5)  # 10 → 5로 변경 (더 빠른 반응)

        self._block_start_ts = time.monotonic()
        
        # 🎯 음성 품질 향상: 15초 블록 + 조기 플러시 설정
        self._RATE = 16000
        self._FRAME_MS = 20
        self._SAMPLE_WIDTH = 2
        self._CHANNELS = 1
        
        # 15초 블록 설정
        self._BLOCK_SECONDS = 15
        self._BLOCK_BYTES = int(self._RATE * self._BLOCK_SECONDS) * self._SAMPLE_WIDTH * self._CHANNELS
        
        # VAD 품질 기준 (참고 코드 기반) - 더 완화된 기준
        self._VOICE_RATIO_MIN = 0.05      # 10% → 5%로 완화
        self._MIN_VOICE_MS = 500          # 1000ms → 500ms로 완화
        self._SILENCE_TIMEOUT_SEC = 1     # 2초 → 1초로 완화
        self._EARLY_MIN_VOICE_MS = 150    # 300ms → 150ms로 완화
        self._EARLY_VOICE_RATIO_MIN = 0.02  # 5% → 2%로 완화
        
        # 모니터링용 통계
        self._stats = {
            'frames_processed': 0,
            'blocks_created': 0,
            'blocks_dropped': 0,
            'last_block_ts': None,
            'current_block_duration': 0
        }

    def reset(self):
        self._pcm16_buf.clear()
        self._block.clear()
        self._voiced_frames = 0
        self._total_frames = 0
        self._have_any_voice = False
        self._last_voice_ts = None
        self._smooth.clear()
        self._block_start_ts = time.monotonic()
        
        # 통계 리셋 (누적값은 유지)
        self._stats['current_block_duration'] = 0

    def get_stats(self) -> dict:
        """현재 상태 통계 반환"""
        now = time.monotonic()
        current_block_duration = now - self._block_start_ts if self._block_start_ts else 0
        
        # 🎯 음성 품질 향상: 더 상세한 통계
        voice_ratio = (self._voiced_frames / self._total_frames) if self._total_frames else 0.0
        voiced_ms = self._voiced_frames * self._FRAME_MS
        block_size_bytes = len(self._block)
        block_size_seconds = block_size_bytes / (self._RATE * self._SAMPLE_WIDTH * self._CHANNELS)
        
        return {
            'total_frames': self._total_frames,
            'voiced_frames': self._voiced_frames,
            'voice_ratio': voice_ratio,
            'voiced_ms': voiced_ms,
            'block_size_bytes': block_size_bytes,
            'block_size_seconds': block_size_seconds,
            'current_block_duration': current_block_duration,
            'have_any_voice': self._have_any_voice,
            'last_voice_ts': self._last_voice_ts,
            'blocks_created': self._stats['blocks_created'],
            'blocks_dropped': self._stats['blocks_dropped'],
            'frames_processed': self._stats['frames_processed'],
            'queue_size': self.out_q.qsize(),
            'queue_maxsize': self.out_q.maxsize
        }

    def push_av_frame(self, frame: av.AudioFrame):
        """PyAV AudioFrame을 처리"""
        try:
            arr = frame.to_ndarray()
            arr = _to_mono_f32(arr)
            
            # 🎵 노이즈 필터링 및 음질 개선 적용
            arr_enhanced = self._enhance_audio_quality(arr, frame.sample_rate)
            
            # 🚨 노이즈 방지: 데이터 품질 검증
            if not self._validate_audio_quality(arr_enhanced):
                return
            
            # 🚨 노이즈 방지: 고품질 리샘플링 (48kHz → 16kHz)
            arr_resampled = self._high_quality_resample(arr_enhanced, frame.sample_rate, 16000)
            
            # 🚨 노이즈 방지: 정규화 최적화
            arr_normalized = self._optimized_normalize(arr_resampled)
            
            # 🚨 노이즈 방지: 비트 변환 품질 향상
            i16 = self._high_quality_convert_to_int16(arr_normalized)
            raw = i16.tobytes()

            self._pcm16_buf.extend(raw)
            self._stats['frames_processed'] += 1
            
            FRAME_MS = 20
            RATE = 16000
            SAMPLE_WIDTH = 2
            FRAME_BYTES = (RATE * FRAME_MS // 1000) * SAMPLE_WIDTH * 1  # mono

            while len(self._pcm16_buf) >= FRAME_BYTES:
                fb = bytes(self._pcm16_buf[:FRAME_BYTES])
                del self._pcm16_buf[:FRAME_BYTES]
                self._consume_frame_bytes(fb)
            
        except Exception as e:
            print(f"[VAD] push_av_frame 오류: {e}")

    def _enhance_audio_quality(self, audio_data: np.ndarray, sample_rate: int) -> np.ndarray:
        """🎵 고품질 오디오 품질 향상"""
        try:
            # 🚀 1단계: 노이즈 제거 및 신호 정제
            audio_cleaned = self._remove_noise(audio_data, sample_rate)
            
            # 🚀 2단계: 주파수 대역 최적화
            audio_optimized = self._optimize_frequency_bands(audio_cleaned, sample_rate)
            
            # 🚀 3단계: 다이나믹 레인지 압축
            audio_compressed = self._dynamic_range_compression(audio_optimized)
            
            # 🚀 4단계: 스펙트럼 밸런싱
            audio_balanced = self._spectral_balancing(audio_compressed, sample_rate)
            
            # print(f"[VAD] 🎵 오디오 품질 향상 완료: {len(audio_data)} → {len(audio_balanced)} samples")
            return audio_balanced
            
        except Exception as e:
            print(f"[VAD] 오디오 품질 향상 실패: {e}, 원본 반환")
            return audio_data

    def _remove_noise(self, audio_data: np.ndarray, sample_rate: int) -> np.ndarray:
        """🎵 고급 노이즈 제거"""
        try:
            from scipy import signal
            
            # 1. 고주파 노이즈 제거 (8kHz 이상)
            if sample_rate > 16000:
                nyquist = sample_rate / 2
                cutoff = 8000 / nyquist
                b, a = signal.butter(4, cutoff, btype='low')
                audio_data = signal.filtfilt(b, a, audio_data)
            
            # 2. 저주파 노이즈 제거 (60Hz 이하)
            cutoff = 60 / (sample_rate / 2)
            b, a = signal.butter(4, cutoff, btype='high')
            audio_data = signal.filtfilt(b, a, audio_data)
            
            # 3. 적응형 노이즈 게이트
            rms = np.sqrt(np.mean(audio_data**2))
            noise_threshold = max(0.01, rms * 0.1)
            audio_data = np.where(np.abs(audio_data) < noise_threshold, 0, audio_data)
            
            return audio_data
            
        except Exception as e:
            print(f"[VAD] 노이즈 제거 실패: {e}")
            return audio_data

    def _optimize_frequency_bands(self, audio_data: np.ndarray, sample_rate: int) -> np.ndarray:
        """🎵 주파수 대역 최적화"""
        try:
            from scipy import signal
            
            # 1. 음성 주파수 대역 강화 (300Hz ~ 3kHz)
            nyquist = sample_rate / 2
            
            # 저음 강화 (300Hz ~ 800Hz)
            low_cutoff = 300 / nyquist
            low_high_cutoff = 800 / nyquist
            b, a = signal.butter(4, [low_cutoff, low_high_cutoff], btype='band')
            low_band = signal.filtfilt(b, a, audio_data)
            
            # 중음 강화 (800Hz ~ 3kHz)
            mid_low_cutoff = 800 / nyquist
            mid_high_cutoff = 3000 / nyquist
            b, a = signal.butter(4, [mid_low_cutoff, mid_high_cutoff], btype='band')
            mid_band = signal.filtfilt(b, a, audio_data)
            
            # 2. 주파수 대역별 가중치 적용
            enhanced = (low_band * 1.2) + (mid_band * 1.5) + (audio_data * 0.8)
            
            # 3. 클리핑 방지
            enhanced = np.clip(enhanced, -0.95, 0.95)
            
            return enhanced
            
        except Exception as e:
            print(f"[VAD] 주파수 대역 최적화 실패: {e}")
            return audio_data

    def _dynamic_range_compression(self, audio_data: np.ndarray) -> np.ndarray:
        """🎵 다이나믹 레인지 압축"""
        try:
            # 1. RMS 기반 압축
            rms = np.sqrt(np.mean(audio_data**2))
            
            # 2. 적응형 압축 비율
            if rms > 0.5:
                # 큰 신호 압축
                compression_ratio = 0.7
                threshold = 0.5
                audio_data = np.where(
                    np.abs(audio_data) > threshold,
                    np.sign(audio_data) * (threshold + (np.abs(audio_data) - threshold) * compression_ratio),
                    audio_data
                )
            
            # 3. 작은 신호 부스트
            boost_threshold = 0.1
            boost_factor = 1.3
            audio_data = np.where(
                (np.abs(audio_data) > 0.01) & (np.abs(audio_data) < boost_threshold),
                audio_data * boost_factor,
                audio_data
            )
            
            return audio_data
            
        except Exception as e:
            print(f"[VAD] 다이나믹 레인지 압축 실패: {e}")
            return audio_data

    def _spectral_balancing(self, audio_data: np.ndarray, sample_rate: int) -> np.ndarray:
        """🎵 스펙트럼 밸런싱"""
        try:
            from scipy import signal
            
            # 1. 스펙트럼 서브트랙션으로 노이즈 제거
            if len(audio_data) > 1024:
                # FFT 기반 스펙트럼 분석
                fft = np.fft.fft(audio_data)
                freqs = np.fft.fftfreq(len(audio_data), 1/sample_rate)
                
                # 노이즈 스펙트럼 추정 (고주파 대역)
                noise_mask = np.abs(freqs) > 4000
                noise_spectrum = np.mean(np.abs(fft[noise_mask])) if np.any(noise_mask) else 0
                
                # 노이즈 스펙트럼 서브트랙션
                if noise_spectrum > 0:
                    fft[noise_mask] *= 0.3  # 고주파 노이즈 억제
                    audio_data = np.real(np.fft.ifft(fft))
            
            # 2. 스무딩 필터로 급격한 변화 완화
            window_size = min(31, len(audio_data) // 10)
            if window_size > 3:
                audio_data = signal.savgol_filter(audio_data, window_size, 3)
            
            return audio_data
            
        except Exception as e:
            print(f"[VAD] 스펙트럼 밸런싱 실패: {e}")
            return audio_data

    def _validate_audio_quality(self, audio_data: np.ndarray) -> bool:
        """오디오 품질 검증으로 노이즈 방지"""
        try:
            # 1. SNR (Signal-to-Noise Ratio) 계산
            signal_power = np.mean(audio_data**2)
            noise_power = np.var(audio_data)
            snr = 10 * np.log10(signal_power / (noise_power + 1e-10))
            
            # 2. 신호 강도 검증
            rms = np.sqrt(signal_power)
            
            # 3. 추가 품질 지표
            peak = np.max(np.abs(audio_data))
            dynamic_range = 20 * np.log10(peak / (rms + 1e-10))
            
            # 품질 검증 (조용히 처리)
            if snr < -2.0 or rms < 0.0001 or rms > 0.95 or dynamic_range < 2:
                return False
            
            return True
            
        except Exception as e:
            print(f"[VAD] 품질 검증 실패: {e}")
            return False

    def _high_quality_resample(self, audio_data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        """🎵 고품질 리샘플링으로 노이즈 방지"""
        try:
            if src_rate == dst_rate:
                return audio_data
            
            # 🚀 노이즈 방지: 고품질 리샘플링
            if len(audio_data) > 0:
                from scipy import signal
                
                # 1. 안티앨리어싱 필터 적용
                if src_rate > dst_rate:
                    # 다운샘플링 시 안티앨리어싱
                    nyquist = dst_rate / 2
                    cutoff = min(nyquist * 0.8, src_rate / 2 - 1000) / (src_rate / 2)
                    b, a = signal.butter(8, cutoff, btype='low')
                    audio_data = signal.filtfilt(b, a, audio_data)
                
                # 2. 고품질 리샘플링 (scipy.signal.resample)
                target_length = int(len(audio_data) * dst_rate / src_rate)
                resampled = signal.resample(audio_data, target_length, window='hann')
                
                # 3. 품질 검증
                if np.any(np.isnan(resampled)) or np.any(np.isinf(resampled)):
                    print(f"[VAD] 🚨 리샘플링 결과에 NaN/Inf 발견, 기본 방법 사용")
                    return _resample_f32(audio_data, src_rate, dst_rate)
                
                return resampled.astype(np.float32)
            else:
                return audio_data
                
        except Exception as e:
            print(f"[VAD] 고품질 리샘플링 실패: {e}, 기본 리샘플링 사용")
            return _resample_f32(audio_data, src_rate, dst_rate)

    def _optimized_normalize(self, audio_data: np.ndarray) -> np.ndarray:
        """🎵 최적화된 정규화로 노이즈 방지"""
        try:
            # 🚀 노이즈 방지: 다이나믹 레인지 보존
            peak = float(np.max(np.abs(audio_data))) if audio_data.size else 0.0
            
            if peak > 1e-6:
                # 1. 적응형 타겟 피크 설정
                target_peak = 0.85  # 95% → 85%로 조정 (더 안전하게)
                
                # 2. 스마트 정규화
                if peak > 0.8:
                    # 이미 큰 신호는 약간만 조정
                    normalized = audio_data * (target_peak / peak)
                else:
                    # 작은 신호는 더 적극적으로 정규화
                    normalized = audio_data * (target_peak / peak) * 1.1
                
                # 3. 고급 노이즈 게이트
                rms = np.sqrt(np.mean(normalized**2))
                adaptive_threshold = max(0.005, rms * 0.05)  # 더 민감하게
                
                # 노이즈 게이트 + 스무딩
                noise_mask = np.abs(normalized) < adaptive_threshold
                normalized[noise_mask] = 0
                
                # 4. 클리핑 방지
                normalized = np.clip(normalized, -0.95, 0.95)
                
                return normalized
            else:
                return audio_data
                
        except Exception as e:
            print(f"[VAD] 최적화된 정규화 실패: {e}")
            return audio_data

    def _high_quality_convert_to_int16(self, audio_data: np.ndarray) -> np.ndarray:
        """🎵 고품질 int16 변환으로 노이즈 방지"""
        try:
            # 🚀 노이즈 방지: 클리핑 방지
            # 1. 다이나믹 레인지 최적화
            rms = np.sqrt(np.mean(audio_data**2))
            peak = np.max(np.abs(audio_data))
            
            # 2. 적응형 스케일링
            if peak > 0.8:
                # 큰 신호는 보수적으로 스케일링
                scale_factor = 0.8 / peak
            else:
                # 작은 신호는 더 적극적으로 스케일링
                scale_factor = 0.9 / peak
            
            # 3. 고품질 스케일링
            scaled = audio_data * scale_factor * 32767.0
            
            # 4. 클리핑 방지
            int16_data = np.clip(scaled, -32768, 32767).astype(np.int16)
            
            # 5. 품질 검증
            if np.any(np.isnan(int16_data)) or np.any(np.isinf(int16_data)):
                print(f"[VAD] 🚨 변환된 데이터에 NaN/Inf 발견")
                return np.zeros_like(audio_data, dtype=np.int16)
            
            # 6. 품질 메트릭 계산
            converted_rms = np.sqrt(np.mean(int16_data.astype(np.float32)**2))
            quality_score = (converted_rms / 16384.0) * 100  # 0-100 스케일
            
            # print(f"[VAD] 🎵 int16 변환 품질: 원본 RMS={rms:.6f}, 변환 RMS={converted_rms:.1f}, 품질점수={quality_score:.1f}%")
            
            return int16_data
            
        except Exception as e:
            print(f"[VAD] 고품질 int16 변환 실패: {e}")
            return _f32_to_i16(audio_data)

    def _consume_frame_bytes(self, fb: bytes):
        """20ms 프레임 처리"""
        RATE = 16000
        is_voiced = False
        try:
            # 🎵 VAD 음성 감지 개선: 더 민감하게 설정
            is_voiced = self._vad.is_speech(fb, RATE)
            
            # 🚨 노이즈 방지: 신호 품질 기반 음성 판단
            if not is_voiced:
                # 16-bit PCM 데이터로 변환하여 신호 강도 확인
                audio_data = np.frombuffer(fb, dtype=np.int16)
                rms = np.sqrt(np.mean(audio_data.astype(np.float32)**2))
                
                # 🚨 노이즈 방지: 더 현실적인 임계값
                if rms > 500:  # 2000 → 500으로 완화 (실제 음성에 맞춤)
                    # 추가 검증: 주파수 특성 확인
                    if self._check_speech_frequency_characteristics(audio_data):
                        is_voiced = True
                        print(f"[VAD] 🎤 신호 품질로 음성 판단: RMS={rms:.1f}")
                    else:
                        print(f"[VAD] 🚨 높은 RMS지만 음성 특성 아님: RMS={rms:.1f}")
                else:
                    print(f"[VAD] 🔇 낮은 RMS: {rms:.1f} (임계값: 500)")
                        
        except Exception as e:
            print(f"[VAD] VAD 처리 오류: {e}")
            is_voiced = False

        # 🎵 VAD 음성 감지 개선
        self._smooth.append(1 if is_voiced else 0)
        
        # 🚨 노이즈 방지: 더 정확한 스무딩
        smooth = (sum(self._smooth) >= 3)  # 5개 중 3개 이상이면 음성으로 판단 (2 → 3으로 증가)
        
        self._block.extend(fb)
        self._total_frames += 1
        now = time.monotonic()

        if smooth:
            self._voiced_frames += 1
            self._last_voice_ts = now
            self._have_any_voice = True

        # else:
        #     print(f"[VAD] 🔇 무음 프레임 (프레임 {self._total_frames}, 음성 프레임: {self._voiced_frames})")

        # 🎯 음성 품질 향상: 15초 블록 + 조기 플러시 로직
        self._check_block_completion()
        self._check_early_flush()
        
        # 🔧 추가: 더 자주 블록 생성 체크 (프레임 100개마다)
        if self._total_frames % 100 == 0:
            self._force_block_generation_check()

    def _finalize_block(self, relaxed: bool):
        """블록 완료 처리"""
        if len(self._block) == 0:
            print(f"[VAD] ⚠️ 빈 블록: 처리하지 않음")
            return

        try:
            # 🎯 음성 품질 향상: 블록 품질 검증
            voice_ratio = (self._voiced_frames / self._total_frames) if self._total_frames else 0.0
            voiced_ms = self._voiced_frames * self._FRAME_MS
            block_size_seconds = len(self._block) / (self._RATE * self._SAMPLE_WIDTH * self._CHANNELS)
            
            # 블록 완료 처리 (조용히)
            
            # 품질 기준 통과 확인
            min_voice_ms = self._EARLY_MIN_VOICE_MS if relaxed else self._MIN_VOICE_MS
            min_voice_ratio = self._EARLY_VOICE_RATIO_MIN if relaxed else self._VOICE_RATIO_MIN
            
            if voiced_ms >= min_voice_ms and voice_ratio >= min_voice_ratio:
                # 블록을 큐에 추가
                block_data = bytes(self._block)
                try:
                    self.out_q.put_nowait(block_data)
                    self._stats['blocks_created'] += 1
                    self._stats['last_block_ts'] = time.monotonic()
                except asyncio.QueueFull:
                    self._stats['blocks_dropped'] += 1
            else:
                self._stats['blocks_dropped'] += 1
            
            # 블록 상태 초기화
            self._reset_block()
            
        except Exception as e:
            print(f"[VAD] 블록 완료 처리 실패: {e}")
            self._reset_block()

    async def get_block(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """블록 비동기 대기"""
        try:
            if timeout is None:
                return await self.out_q.get()
            return await asyncio.wait_for(self.out_q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def drain_block_nowait(self) -> Optional[bytes]:
        """블록 논블로킹 가져오기"""
        try:
            return self.out_q.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def _check_speech_frequency_characteristics(self, audio_data: np.ndarray) -> bool:
        """음성 주파수 특성 확인으로 노이즈 방지"""
        try:
            # FFT로 주파수 분석
            fft = np.fft.fft(audio_data)
            freqs = np.fft.fftfreq(len(audio_data), 1/16000)
            
            # 사람 음성 주파수 범위 (85Hz ~ 8kHz)
            voice_mask = (np.abs(freqs) >= 85) & (np.abs(freqs) <= 8000)
            voice_power = np.sum(np.abs(fft[voice_mask])**2)
            total_power = np.sum(np.abs(fft)**2)
            
            # 🚨 노이즈 방지: 더 현실적인 임계값
            voice_ratio = voice_power / total_power if total_power > 0 else 0
            
            if voice_ratio < 0.15:  # 40% → 15%로 완화 (실제 음성에 맞춤)
                print(f"[VAD] 🚨 음성 주파수 비율 부족: {voice_ratio:.1%} (임계값: 15%)")
                return False
            
            # 추가 검증: 제로 크로싱 비율
            zero_crossings = np.sum(np.diff(np.sign(audio_data)) != 0)
            zero_crossing_rate = zero_crossings / len(audio_data)
            
            # 🚨 노이즈 방지: 더 현실적인 범위
            if zero_crossing_rate < 0.005 or zero_crossing_rate > 0.6:  # 1% ~ 40% → 0.5% ~ 60%
                print(f"[VAD] 🚨 제로 크로싱 비율 이상: {zero_crossing_rate:.1%} (범위: 0.5% ~ 60%)")
                return False
            
            print(f"[VAD] ✅ 주파수 특성 검증 통과: 음성비율={voice_ratio:.1%}, 제로크로싱={zero_crossing_rate:.1%}")
            return True
            
        except Exception as e:
            print(f"[VAD] 주파수 특성 검증 실패: {e}")
            return False

    def _check_block_completion(self):
        """15초 블록 완성 여부 확인"""
        if len(self._block) >= self._BLOCK_BYTES:
            voice_ratio = (self._voiced_frames / self._total_frames) if self._total_frames else 0.0
            voiced_ms = self._voiced_frames * self._FRAME_MS
            
            print(f"[VAD] 🎯 15초 블록 완성: 음성비율={voice_ratio:.1%}, 음성시간={voiced_ms}ms")
            print(f"[VAD] 🔍 블록 크기: {len(self._block)} bytes / {self._BLOCK_BYTES} bytes ({(len(self._block)/self._BLOCK_BYTES)*100:.1f}%)")
            
            # 품질 기준 통과 시 블록 생성
            if voice_ratio >= self._VOICE_RATIO_MIN and voiced_ms >= self._MIN_VOICE_MS:
                print(f"[VAD] ✅ 15초 블록 품질 기준 통과: 블록 생성")
                self._finalize_block(relaxed=False)  # 15초 블록은 엄격한 기준
            else:
                print(f"[VAD] ❌ 15초 블록 품질 기준 미달: 음성비율={voice_ratio:.1%} (기준: {self._VOICE_RATIO_MIN:.1%}), 음성시간={voiced_ms}ms (기준: {self._MIN_VOICE_MS}ms)")
                self._stats['blocks_dropped'] += 1
                self._reset_block()
        else:
            # 🔍 디버그: 블록 진행 상황 모니터링
            if self._total_frames % 50 == 0:  # 50프레임마다 상태 출력
                voice_ratio = (self._voiced_frames / self._total_frames) if self._total_frames else 0.0
                voiced_ms = self._voiced_frames * self._FRAME_MS
                block_progress = (len(self._block) / self._BLOCK_BYTES) * 100
    

    def _check_early_flush(self):
        """조기 플러시 조건 확인"""
        if (len(self._block) > 0 and 
            self._have_any_voice and 
            self._last_voice_ts is not None and
            len(self._block) < self._BLOCK_BYTES):  # 15초 미만일 때만
            
            silence_duration = time.monotonic() - self._last_voice_ts
            
            if silence_duration >= self._SILENCE_TIMEOUT_SEC:
                voice_ratio = (self._voiced_frames / self._total_frames) if self._total_frames else 0.0
                voiced_ms = self._voiced_frames * self._FRAME_MS
                
                print(f"[VAD] 🎯 조기 플러시 조건 확인: 무음지속={silence_duration:.1f}s, 음성비율={voice_ratio:.1%}, 음성시간={voiced_ms}ms")
                print(f"[VAD] 🔍 조기 플러시 기준: 무음지속={silence_duration:.1f}s (기준: {self._SILENCE_TIMEOUT_SEC}s), 음성시간={voiced_ms}ms (기준: {self._EARLY_MIN_VOICE_MS}ms), 음성비율={voice_ratio:.1%} (기준: {self._EARLY_VOICE_RATIO_MIN:.1%})")
                
                # 조기 플러시 기준 통과 시 블록 생성
                if (voiced_ms >= self._EARLY_MIN_VOICE_MS and 
                    voice_ratio >= self._EARLY_VOICE_RATIO_MIN):
                    print(f"[VAD] ✅ 조기 플러시 기준 통과: 블록 생성")
                    self._finalize_block(relaxed=True)  # 조기 플러시는 완화된 기준
                else:
                    print(f"[VAD] ❌ 조기 플러시 기준 미달: 음성시간={voiced_ms}ms (기준: {self._EARLY_MIN_VOICE_MS}ms), 음성비율={voice_ratio:.1%} (기준: {self._EARLY_VOICE_RATIO_MIN:.1%})")
                    self._stats['blocks_dropped'] += 1
                    self._reset_block()
            else:
                # 🔍 디버그: 조기 플러시 대기 상황
                if self._total_frames % 100 == 0:  # 100프레임마다 상태 출력
                    voice_ratio = (self._voiced_frames / self._total_frames) if self._total_frames else 0.0
                    voiced_ms = self._voiced_frames * self._FRAME_MS
                    remaining_silence = self._SILENCE_TIMEOUT_SEC - silence_duration
        

    def _force_block_generation_check(self):
        """강제 블록 생성 체크 (더 자주 블록 생성)"""
        if len(self._block) > 0 and self._have_any_voice:
            voice_ratio = (self._voiced_frames / self._total_frames) if self._total_frames else 0.0
            voiced_ms = self._voiced_frames * self._FRAME_MS
            block_size_seconds = len(self._block) / (self._RATE * self._SAMPLE_WIDTH * self._CHANNELS)
            

            
            # 더 완화된 기준으로 블록 생성 시도
            if (voiced_ms >= 200 and  # 200ms 이상 음성
                voice_ratio >= 0.03 and  # 3% 이상 음성
                block_size_seconds >= 1.0):  # 1초 이상 블록
                
                self._finalize_block(relaxed=True)

    def _reset_block(self):
        """블록 상태 초기화"""
        self._block.clear()
        self._voiced_frames = 0
        self._total_frames = 0
        self._have_any_voice = False
        self._last_voice_ts = None
        self._block_start_ts = time.monotonic()
        