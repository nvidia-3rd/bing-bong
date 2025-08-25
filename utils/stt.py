import os
import sys
import time
import wave
import queue
import signal
import threading
import tempfile
import pyaudio
import webrtcvad
from openai import OpenAI

# ===================== Config =====================

# Audio : 기본 오디오 설정
RATE = 16000               # 샘플링 레이트(Hz). webrtcvad는 8/16/32/48k 지원
CHANNELS = 1               # 채널 수(모노)
SAMPLE_WIDTH = 2           # 샘플 폭(바이트) -> 16-bit PCM = 2바이트
FRAME_MS = 20              # 프레임 길이(ms). webrtcvad는 10/20/30ms만 허용
FRAME_BYTES = int(RATE * (FRAME_MS / 1000.0)) * SAMPLE_WIDTH * CHANNELS  # i.e. 20ms 프레임의 총 바이트 수(= 640 bytes @16kHz)

# STT block : 요청할 오디오 블록 단위
BLOCK_SECONDS = 15
BLOCK_BYTES = int(RATE * BLOCK_SECONDS) * SAMPLE_WIDTH * CHANNELS   # 15초 블록의 총 바이트 수

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

# STT model
MODEL = "gpt-4o-mini-transcribe"
PRINT_TIMING = True

# 입력장치 선택 (None = 시스템 기본장치)
INPUT_DEVICE_INDEX = None


# ===================== Globals =====================
'''Frame queue는 20ms 단위로 녹음된 오디오 프레임을 저장하고,
Block은 frame들을 모아 묶은 오디오 블록을 저장'''

stop_event = threading.Event()
frame_queue = queue.Queue(maxsize=FRAME_QUEUE_MAX)   # producer: 20ms frames
block_queue = queue.Queue(maxsize=BLOCK_QUEUE_MAX)   # consumer: 15s (or early) blocks

## OpenAI client init
client = OpenAI(api_key='')


# ===================== Helpers =====================

def open_stream(pa: pyaudio.PyAudio, device_index=None):
    """Open mic stream; frames_per_buffer matches FRAME_MS."""
    '''pyaudio 스트림을 열고 20ms 프레임 단위로 읽도록 설정'''
    frames_per_buffer = int(RATE * (FRAME_MS / 1000.0))  # samples per frame
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=frames_per_buffer,
        start=True,
    )
    return stream


def write_wav(path: str, pcm_bytes: bytes):
    """Wrap raw PCM16 mono bytes into a WAV file."""
    '''PCM16 mono 바이트를 WAV 파일로 만드는 함수,
    함수를 통해 생성된 WAV 파일은 OpenAI STT API로 전달'''
    
    # print(type(pcm_bytes))
    pa_tmp = pyaudio.PyAudio()
    try:
        with wave.open(path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(pa_tmp.get_sample_size(pyaudio.paInt16))
            wf.setframerate(RATE)
            wf.writeframes(pcm_bytes)
    finally:
        pa_tmp.terminate()


# ===================== Threads =====================

def recorder_thread():
    """마이크에서 20ms 프레임을 지속적으로 읽어 frame_queue에 적재"""
    pa = pyaudio.PyAudio()
    stream = None
    try:
        stream = open_stream(pa, device_index=INPUT_DEVICE_INDEX)
        print("🎙️ Recording... (Ctrl+C to stop)")
        
        # 워밍업: 첫 프레임을 한번 읽어 내부 버퍼 안정화(실제 구현 시 사전 안정화 권장)
        try:
            _ = stream.read(int(RATE * (FRAME_MS / 1000.0)), exception_on_overflow=False)
        except Exception:
            pass

        samples_per_frame = int(RATE * (FRAME_MS / 1000.0))
        while not stop_event.is_set():
            data = stream.read(samples_per_frame, exception_on_overflow=False)
            if not data:
                continue
            # 예상 프레임 크기와 다르면 버림
            if len(data) != FRAME_BYTES:
                continue
            # stream된 프레임을 frame_queue에 넣기(최대 대기시간 0.2)
            try:
                frame_queue.put(data, timeout=0.2)
                
            # frame_queue가 가득 찼을 때 처리 정책
            except queue.Full:
                print("queue full, dropping oldest frame")
                try:
                    _ = frame_queue.get_nowait()
                    frame_queue.task_done()
                except queue.Empty:
                    pass
                try:
                    frame_queue.put(data, timeout=0.05)
                except queue.Full:
                    pass

    except Exception as e:
        if not stop_event.is_set():
            print(f"[recorder] error: {e}")
    finally:
        try:
            if stream and stream.is_active():
                stream.stop_stream()
            if stream:
                stream.close()
        except Exception:
            pass
        pa.terminate()
        print("🎤 Recorder stopped")


def vad_blocker_thread():
    """
    STT에 보낼 블록을 생성하는 함수.
    20ms 프레임들을 모아 정확히 15초 블록을 만들거나,
    무음이 오래 지속되면(e.g. 2초 이상은 발화 종료로 간주) 15초 이전에 조기 플러시 블록 생성.
    VAD 비율/누적 음성시간 기준으로 유효한 블록만 block_queue로 전달.
    """
    vad = webrtcvad.Vad(VAD_AGGR)

    block = bytearray()
    voiced_frames = 0
    total_frames = 0
    last_voice_ts = None
    have_any_voice = False
    block_start_ts = time.time()

    while not stop_event.is_set():
        frame = None
        try:
            frame = frame_queue.get(timeout=0.2)  # 20ms frame
        except queue.Empty:
            pass

        now = time.time()

        if frame is not None:
            
            # VAD로 현재 프레임이 음성인지 판정(20ms 단위)
            try:
                is_voiced = vad.is_speech(frame, RATE)
            except Exception:
                is_voiced = False

            # 블록 바이트에 프레임 추가하고 카운터 갱신
            block.extend(frame)
            total_frames += 1
            if is_voiced:
                voiced_frames += 1
                last_voice_ts = now
                have_any_voice = True

            # 15초 분량이 채워진 경우 -> 유효성 판단 후 전송/드롭
            if len(block) >= BLOCK_BYTES:
                voice_ratio = (voiced_frames / total_frames) if total_frames else 0.0
                voiced_ms = voiced_frames * FRAME_MS

                # 비율/누적시간 기준을 만족하면 block_queue에 전송
                if voice_ratio >= VOICE_RATIO_MIN and voiced_ms >= MIN_VOICE_MS:
                    try:
                        block_queue.put(bytes(block), timeout=0.5)
                    except queue.Full:
                        print("[vad] block queue full; drop full block")
                # else: drop

                # reset for next block
                block.clear()
                voiced_frames = 0
                total_frames = 0
                have_any_voice = False
                last_voice_ts = None
                block_start_ts = now

            frame_queue.task_done()

        # 조기 플러시 상황 : 블록에 음성이 있었고, 마지막 음성 이후 무음이 SILENCE_TIMEOUT_SEC 이상이면 (아직 15초는 안 채웠더라도) 완화된 기준으로 유효하면 전송
        if (
            len(block) > 0
            and have_any_voice
            and last_voice_ts is not None
            and (now - last_voice_ts) >= SILENCE_TIMEOUT_SEC
            and len(block) < BLOCK_BYTES  # not already flushed by full block logic
        ):
            voice_ratio = (voiced_frames / total_frames) if total_frames else 0.0
            voiced_ms = voiced_frames * FRAME_MS

            # 조기 플러시 기준(완화된 임계치) 통과 시 전송
            if voiced_ms >= EARLY_MIN_VOICE_MS and voice_ratio >= EARLY_VOICE_RATIO_MIN:
                try:
                    block_queue.put(bytes(block), timeout=0.5)
                except queue.Full:
                    print("[vad] block queue full; drop early block")

            # reset
            block.clear()
            voiced_frames = 0
            total_frames = 0
            have_any_voice = False
            last_voice_ts = None
            block_start_ts = now


def transcriber_thread():
    """block_queue에서 15초 이하의 블록을 꺼내 WAV로 저장 후 OpenAI STT 호출"""
    while not stop_event.is_set():
        try:
            block = block_queue.get(timeout=0.2)
        except queue.Empty:
            continue

        tmp_path = None
        t0 = time.time()
        try:
            # 임시 WAV 파일로 저장(PCM 바이트 -> WAV)
            with tempfile.NamedTemporaryFile(prefix="stt_", suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
            write_wav(tmp_path, block)

            # STT API 호출
            with open(tmp_path, "rb") as f:
                tr = client.audio.transcriptions.create(
                    model=MODEL,
                    file=f,
                    language="ko",
                    response_format="json",
                )
            t1 = time.time()
            if PRINT_TIMING:
                print(f"\n[{time.strftime('%H:%M:%S')}] STT done in {t1 - t0:.2f}s")
            print("→", tr.text)
        except Exception as e:
            print(f"[stt] error: {e}")
        finally:
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            block_queue.task_done()


# ===================== Launcher =====================

def graceful_exit(*_):
    if not stop_event.is_set():
        print("\n🛑 Stopping... please wait")
    stop_event.set()

def main():
    try:
        signal.signal(signal.SIGINT, graceful_exit)
        signal.signal(signal.SIGTERM, graceful_exit)
        print("[info] signal handlers installed")
    except Exception as e:
        print(f"[warn] cannot install signal handlers: {e}")

    # 3개 스레드 작동(음성 녹음, VAD 블록 생성, STT 전송)
    t_rec = threading.Thread(target=recorder_thread, name="recorder", daemon=False) # 녹음 -> frame_queue
    t_vad = threading.Thread(target=vad_blocker_thread, name="vad", daemon=False)   # 프레임 -> 블록화 -> block_queue
    t_stt = threading.Thread(target=transcriber_thread, name="stt", daemon=False)   # 블록 -> STT 호출

    print("[info] starting threads...")
    t_rec.start()
    t_vad.start()
    t_stt.start()

    try:
        # Keep main thread alive until stop_event set
        while not stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        graceful_exit()
    finally:
        print("[info] joining threads...")
        # Give threads a moment to drain queues
        t_rec.join(timeout=5)
        t_vad.join(timeout=5)
        t_stt.join(timeout=5)
        print("✅ Clean exit")

if __name__ == "__main__":
    main()