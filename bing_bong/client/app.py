# app.py
import uuid
import asyncio
import streamlit as st
from streamlit_webrtc import WebRtcMode, webrtc_streamer

from infrastructure.config import RTC_CONFIG, QUEUE_MAXSIZE, SAMPLE_EVERY
from infrastructure.http_client import get_http
from infrastructure.metrics import get_metrics, get_errbuf
from infrastructure.VADBlockAssembler import VADBlockAssembler
from infrastructure.webrtc_handlers import (
    make_video_frame_callback,
    AudioAccumulator,
    make_audio_processor_factory,
    make_vad_audio_callback_with_accumulator,
)
from core.session_manager import SessionManager
from core.data_pipeline import DataPipeline
from core.stream_orchestrator import StreamOrchestrator
from services.async_exec import ensure_bg_loop, submit_coro
from services.dual_audio_service import DualAudioService  # 🎤 이중 오디오 서비스 추가
from ui.main import render_main

# 백그라운드 asyncio 실행 루프 준비
ensure_bg_loop()

st.set_page_config(page_title="WebRTC Proxy → FastAPI", layout="centered")

# 인프라 초기화
http = get_http()
metrics = get_metrics()
errbuf = get_errbuf()

# 상태 초기화
if "session_manager" not in st.session_state:
    st.session_state.session_manager = SessionManager(http)
if "data_pipeline" not in st.session_state:
    st.session_state.data_pipeline = DataPipeline(QUEUE_MAXSIZE)
if "stop_evt" not in st.session_state:
    st.session_state.stop_evt = asyncio.Event()
if "wired_lifecycle" not in st.session_state:
    st.session_state.wired_lifecycle = False
if "was_playing" not in st.session_state:
    st.session_state.was_playing = False
if "audio_acc" not in st.session_state:
    st.session_state.audio_acc = AudioAccumulator()
if "vad_assembler" not in st.session_state:
    st.session_state.vad_assembler = VADBlockAssembler()
if "vad_consumer_task" not in st.session_state:
    st.session_state.vad_consumer_task = None
if "orchestrator" not in st.session_state:
    st.session_state.orchestrator = None
if "dual_audio_service" not in st.session_state:
    st.session_state.dual_audio_service = DualAudioService()  # 🎤 이중 오디오 서비스 추가

session_manager = st.session_state.session_manager
data_pipeline = st.session_state.data_pipeline

# WebRTC 컨텍스트
video_cb = make_video_frame_callback(data_pipeline.raw_frame_q, metrics, SAMPLE_EVERY)

ctx = webrtc_streamer(
    key="proxy",
    mode=WebRtcMode.SENDRECV,
    rtc_configuration=RTC_CONFIG,
    media_stream_constraints={"video": True, 
                              "audio": {
                                    "echoCancellation": False,      # 에코 캔슬 비활성화 (음질 향상)
                                    "noiseSuppression": False,      # 노이즈 서프레션 비활성화 (음질 향상)
                                    "autoGainControl": False,       # 자동 게인 제어 비활성화 (음질 향상)
                                    "channelCount": 2,              # 스테레오 지원 (가능한 경우)
                                    "sampleRate": 48000,            # 48kHz 샘플레이트 요청
                                }},
    video_frame_callback=video_cb,
    audio_frame_callback=make_vad_audio_callback_with_accumulator(st.session_state.vad_assembler, st.session_state.audio_acc),
    async_processing=True,
)

# 수명주기 콜백 (WebRTC 연결 상태)
def on_state_change(state: str):
    print(f"[DEBUG] WebRTC 상태 변경: {state}")

ctx.on_connection_state_change = on_state_change

# VAD 소비자 루프 함수
async def _vad_consumer_loop(stop_evt: asyncio.Event):
    """VAD에서 처리된 오디오 블록을 소비하고 전사 처리"""
    try:
        # orchestrator가 초기화될 때까지 대기
        max_wait_time = 10  # 최대 10초 대기
        wait_count = 0
        
        while (not hasattr(st.session_state, 'orchestrator') or 
               st.session_state.orchestrator is None) and wait_count < max_wait_time:
            print(f"[VAD] orchestrator 초기화 대기 중... ({wait_count + 1}/{max_wait_time})")
            await asyncio.sleep(1)
            wait_count += 1
            
        if wait_count >= max_wait_time:
            print("[VAD] orchestrator 초기화 시간 초과 - 소비자 루프 종료")
            return
            
        orch = st.session_state.orchestrator
        vad = st.session_state.vad_assembler
        session_id = st.session_state.session_manager.session_id
        dp = st.session_state.data_pipeline

        print("[VAD] 소비자 루프 시작")
        
        while not stop_evt.is_set():
            # 15초 블록 또는 조기 플러시 블록 대기
            wav_bytes = await vad.get_block(timeout=0.5)
            if not wav_bytes:
                continue
                
            print(f"[VAD] 오디오 블록 수신: {len(wav_bytes)} bytes")
            
            # AudioAccumulator 상태 확인
            audio_info = st.session_state.audio_acc.get_audio_info()
            print(f"[VAD] AudioAccumulator 상태: {audio_info['wav_files_count']}개 WAV 파일, {audio_info['total_bytes']} bytes")
            
            try:
                # 업로드 → 전사
                resp = await orch.audio_service.upload_audio(session_id, wav_bytes, errbuf)
                tr = orch.audio_service.extract_transcript(resp)
                if tr:
                    orch.emotion_service.add_audio_emotion(tr)
                    dp.safe_put(dp.transcript_q, tr)
                    print(f"[VAD] transcript: {tr[:80]}...")
            except Exception as e:
                errbuf.push(f"VAD 소비 루프 오류: {e}")
                print(f"[VAD] 처리 오류: {e}")
                
    except Exception as e:
        print(f"[VAD] 소비자 루프 전체 오류: {e}")
        errbuf.push(f"VAD 소비자 루프 전체 오류: {e}")

async def _send_model_inference(session_id: str, wav_bytes: bytes, orch, dp, errbuf, audio_info: dict):
    """모델 추론 전송 (비동기)"""
    try:
        # 1. 오디오 업로드
        upload_response = await orch.audio_service.upload_audio(session_id, wav_bytes, errbuf)
        if upload_response:
            print("[AUDIO] ✅ 오디오 업로드 완료")
            
            # 2. 전사 결과 추출
            transcript = orch.audio_service.extract_transcript(upload_response)
            if transcript:
                print(f"[AUDIO] 📝 전사 결과: {transcript[:100]}...")
                
                # 3. 감정 분석 추가
                orch.emotion_service.add_audio_emotion(transcript)
                dp.safe_put(dp.transcript_q, transcript)
                
                # 4. 추가 분석 결과 표시
                print(f"[AUDIO] 🔍 분석 완료: 세션 {session_id}, 길이 {audio_info['total_duration']:.1f}초")
                
            else:
                print("[AUDIO] ⚠️ 전사 결과를 가져올 수 없습니다")
                
        else:
            print("[AUDIO] ❌ 오디오 업로드 실패")
            
    except Exception as e:
        print(f"[AUDIO] ❌ 모델 추론 전송 실패: {e}")
        errbuf.push(f"모델 추론 전송 실패: {e}")

# UI 렌더링 위임
render_main(
    ctx=ctx, 
    session_id=session_manager.session_id, 
    metrics=metrics, 
    errbuf=errbuf, 
    frame_q=data_pipeline.frame_q,
    data_pipeline=data_pipeline
)

playing_now = ctx.state.playing
was_playing = st.session_state.was_playing
st.session_state.was_playing = playing_now

# START 버튼 클릭 시 (playing_now = True)
if playing_now:
    if not st.session_state.wired_lifecycle:
        st.session_state.wired_lifecycle = True
        
        # 오디오 누적기 초기화
        st.session_state.audio_acc.reset()
        st.session_state.vad_assembler.reset()
        
        # stop_evt 리셋
        st.session_state.stop_evt.clear()
        
        # 세션 시작
        submit_coro(session_manager.start_session())
        
        # 스트리밍 오케스트레이터 시작
        orchestrator = StreamOrchestrator(
            http_client=http,
            session_id=session_manager.session_id,
            data_pipeline=data_pipeline,
            audio_accumulator=st.session_state.audio_acc,
            metrics=metrics,
            errbuf=errbuf
        )
        
        submit_coro(orchestrator.start())
        st.session_state.orchestrator = orchestrator
        
        # VAD 소비자 루프 시작
        st.session_state.vad_consumer_task = submit_coro(
            _vad_consumer_loop(st.session_state.stop_evt)
        )
        
    st.success("Streaming… 프레임과 오디오를 FastAPI로 처리 중")
else:
    st.write("START 버튼으로 연결을 시작하세요.")

# STOP 버튼 클릭 시 (playing_now = False)
if (not playing_now) and was_playing and st.session_state.wired_lifecycle:
    st.session_state.wired_lifecycle = False
    
    # stop_evt 설정하여 VAD 소비자 루프 중단
    st.session_state.stop_evt.set()
    
    # VAD 소비자 태스크 정리
    if st.session_state.vad_consumer_task:
        st.session_state.vad_consumer_task = None
    
    # 오디오 WAV 파일 생성 및 저장
    try:
        import os
        from datetime import datetime
        
        # orchestrator 가져오기
        orch = st.session_state.orchestrator if hasattr(st.session_state, 'orchestrator') else None
        
        # data_pipeline 가져오기
        dp = st.session_state.data_pipeline if hasattr(st.session_state, 'data_pipeline') else None
        
        # WAV 파일 생성
        wav_bytes = st.session_state.audio_acc.build_wav_bytes()
        
        if wav_bytes:
            # 파일명 생성 (세션 ID + 타임스탬프)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = session_manager.session_id
            filename = f"session_{session_id}_{timestamp}.wav"
            
            # recordings 폴더에 저장
            recordings_dir = "recordings"
            os.makedirs(recordings_dir, exist_ok=True)
            filepath = os.path.join(recordings_dir, filename)
            
            with open(filepath, "wb") as f:
                f.write(wav_bytes)
            
            print(f"[AUDIO] WAV 파일 생성 완료: {filepath} ({len(wav_bytes)} bytes)")
            
            # WAV 파일 정보 표시
            audio_info = st.session_state.audio_acc.get_audio_info()
            st.success(f"🎵 오디오 녹음 완료!\n파일: {filename}\n경로: {recordings_dir}\n크기: {len(wav_bytes)} bytes\nWAV 파일: {audio_info['wav_files_count']}개\n총 길이: {audio_info['total_duration']:.1f}초")
            
            # 🚀 모델 추론 전송 시작
            st.write("🚀 **모델 추론 전송 중...**")
            
            # 비동기 작업을 백그라운드에서 실행 (orchestrator가 있을 때만)
            if orch and dp:
                submit_coro(_send_model_inference(session_id, wav_bytes, orch, dp, errbuf, audio_info))
            else:
                missing_services = []
                if not orch:
                    missing_services.append("Orchestrator")
                if not dp:
                    missing_services.append("Data Pipeline")
                st.warning(f"⚠️ {', '.join(missing_services)}가 초기화되지 않아 모델 추론을 건너뜁니다.")
            
            # 🎤 사람 말을 위한 최적화된 오디오 플레이어
            st.write("🎵 **녹음된 음성 재생:**")
            
            # 샘플레이트 정보 추출 (WAV 헤더에서)
            try:
                import wave
                import io
                with io.BytesIO(wav_bytes) as audio_buffer:
                    with wave.open(audio_buffer, 'rb') as wav_file:
                        sample_rate = wav_file.getframerate()
                        channels = wav_file.getnchannels()
                        st.write(f"📊 **오디오 정보:** 샘플레이트 {sample_rate}Hz, 채널 {channels}개")
            except Exception as e:
                sample_rate = 16000  # 기본값
                st.write(f"📊 **오디오 정보:** 샘플레이트 {sample_rate}Hz (기본값)")
            
            # 🎯 사람 말을 위한 최적화된 st.audio 설정
            st.audio(
                data=wav_bytes,
                format="audio/wav"
            )
            
            # 🎵 추가 음성 정보 표시
            if audio_info['wav_files_count'] > 0:
                st.write(f"🎤 **WAV 파일 분석:**")
                st.write(f"- 총 WAV 파일: {audio_info['wav_files_count']}개")
                st.write(f"- 총 오디오 길이: {audio_info['total_duration']:.1f}초")
                st.write(f"- 총 바이트: {audio_info['total_bytes']:,} bytes")
                st.write(f"- 예상 재생 시간: {audio_info['total_duration']:.1f}초")
            
        else:
            st.warning("⚠️ 오디오 데이터가 없어 WAV 파일을 생성할 수 없습니다.")
            
    except Exception as e:
        st.error(f"❌ WAV 파일 생성 실패: {e}")
        print(f"[AUDIO] WAV 파일 생성 오류: {e}")
    
    # 오케스트레이터 종료 후 초기화
    if hasattr(st.session_state, 'orchestrator'):
        submit_coro(st.session_state.orchestrator.stop())
        st.session_state.orchestrator.reset()

# 세션 종료 버튼
end_col1, end_col2 = st.columns([1, 5])
with end_col1:
    if st.button("End Session", disabled=not session_manager.session_started):
        # 모든 처리 중단
        st.session_state.stop_evt.set()
        submit_coro(session_manager.stop_session())

# 파이프라인 상태 표시
if st.checkbox("파이프라인 상태 보기"):
    st.json(data_pipeline.get_queue_status())
    
    # WebRTC 상태 추가
    st.write("WebRTC 상태:", ctx.state.playing)
    st.write("오케스트레이터 상태:", st.session_state.wired_lifecycle)
    
    # VAD 상태 추가
    if hasattr(st.session_state, 'vad_assembler'):
        vad_stats = {
            "queue_size": st.session_state.vad_assembler.out_q.qsize(),
            "consumer_running": st.session_state.vad_consumer_task is not None,
            "stop_event_set": st.session_state.stop_evt.is_set()
        }
        st.write("VAD 상태:", vad_stats)
    
    # 오디오 누적 상태 추가
    if hasattr(st.session_state, 'audio_acc'):
        audio_stats = st.session_state.audio_acc.get_audio_info()
        st.write("🎵 VAD 기반 원본 오디오 누적 상태:", audio_stats)
        
        # 실시간 VAD 기반 오디오 시각화
        if audio_stats['vad_blocks'] > 0:
            col1, col2 = st.columns(2)
            with col1:
                st.metric("VAD 블록 수", audio_stats['vad_blocks'])
            with col2:
                st.metric("총 바이트", f"{audio_stats['total_bytes']:,}")
        
        # 🔍 VAD 상세 상태 추가
        if st.checkbox("VAD 상세 상태 보기"):
            if hasattr(st.session_state, 'vad_assembler'):
                vad = st.session_state.vad_assembler
                st.write("🔍 VAD 상세 상태:")
                
                # VAD 내부 상태
                st.write(f"**VAD 내부 상태:**")
                st.write(f"- 총 프레임: {vad._total_frames}")
                st.write(f"- 음성 프레임: {vad._voiced_frames}")
                st.write(f"- 음성 비율: {(vad._voiced_frames / max(vad._total_frames, 1)) * 100:.1f}%")
                st.write(f"- 현재 블록 크기: {len(vad._block)} bytes")
                st.write(f"- 블록 시작 시간: {vad._block_start_ts:.2f}s")
                
                if vad._last_voice_ts:
                    time_since_voice = time.monotonic() - vad._last_voice_ts
                    st.write(f"- 마지막 음성 감지: {time_since_voice:.2f}s 전")
                else:
                    st.write(f"- 마지막 음성 감지: 없음")
                
                # VAD 통계
                vad_stats = vad.get_stats()
                st.write(f"**VAD 통계:**")
                st.write(f"- 현재 블록 지속 시간: {vad_stats.get('current_block_duration', 0):.2f}s")
                st.write(f"- 음성 감지 여부: {vad_stats.get('have_any_voice', False)}")
                
                # 실시간 모니터링
                if st.button("🔄 VAD 상태 새로고침"):
                    st.rerun()