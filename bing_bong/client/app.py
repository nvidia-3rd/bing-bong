# app.py (기존 코드 방식)
import streamlit as st
from services.async_exec import ensure_bg_loop, submit_coro
from streamlit_webrtc import WebRtcMode, webrtc_streamer
from infrastructure.config import RTC_CONFIG, QUEUE_MAXSIZE, SAMPLE_EVERY
from infrastructure.http_client import get_http
from infrastructure.metrics import get_metrics, get_errbuf
from infrastructure.VADBlockAssembler import VADBlockAssembler
from infrastructure.webrtc_handlers import (
    make_video_frame_callback,
    AudioAccumulator,
    make_vad_audio_callback_with_accumulator,
)
from core.session_manager import SessionManager
from core.data_pipeline import DataPipeline
from core.stream_orchestrator import StreamOrchestrator
from services.dual_audio_service import DualAudioService


import uuid
import asyncio
import os

# 백그라운드 asyncio 실행 루프 준비
ensure_bg_loop()

# 브라우저 종료 시 자동 정리를 위한 클린업 함수
def cleanup_on_exit():
    """브라우저 종료 시 자동 정리"""
    print("[App] 🚨 브라우저 종료 감지 - 자동 정리 시작")
    
    try:
        # 세션이 활성 상태인 경우에만 정리
        if hasattr(st.session_state, 'wired_lifecycle') and st.session_state.wired_lifecycle:
            print("[App] 🔄 활성 세션 감지 - 정리 수행")
            
            # 1. 모든 처리 중단
            if hasattr(st.session_state, 'stop_evt'):
                st.session_state.stop_evt.set()
            
            # 2. 오케스트레이터 종료 (동기 호출)
            if hasattr(st.session_state, 'orchestrator') and st.session_state.orchestrator:
                print("[App] 🔄 오케스트레이터 종료 중...")
                try:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # 새로운 태스크로 종료 실행
                        loop.create_task(st.session_state.orchestrator.stop())
                    else:
                        # 루프가 실행 중이 아니면 직접 실행
                        loop.run_until_complete(st.session_state.orchestrator.stop())
                    print("[App] ✅ 오케스트레이터 종료 완료")
                except Exception as e:
                    print(f"[App] ⚠️ 오케스트레이터 종료 오류: {e}")
            
            # 3. 세션 종료 API 호출 (비동기)
            if hasattr(st.session_state, 'session_manager'):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(st.session_state.session_manager.stop_session())
                    else:
                        loop.run_until_complete(st.session_state.session_manager.stop_session())
                    print("[App] ✅ 세션 종료 API 호출 완료")
                except Exception as e:
                    print(f"[App] ⚠️ 세션 종료 API 호출 오류: {e}")
            
            print("[App] ✅ 브라우저 종료 시 자동 정리 완료")
        else:
            print("[App] ℹ️ 활성 세션이 없어 정리 작업 건너뜀")
            
    except Exception as e:
        print(f"[App] ❌ 브라우저 종료 시 정리 오류: {e}")

# 브라우저 종료 감지 설정
import atexit
atexit.register(cleanup_on_exit)

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
    st.session_state.dual_audio_service = DualAudioService()

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
    pass

ctx.on_connection_state_change = on_state_change

# VAD 소비자 루프 함수
async def _vad_consumer_loop(stop_evt: asyncio.Event):
    """VAD에서 처리된 오디오 블록을 소비하고 전사 처리"""
    try:
        # orchestrator가 초기화될 때까지 대기
        max_wait_time = 15  # 최대 15초 대기 (여유 시간 증가)
        wait_count = 0
        
        while (not hasattr(st.session_state, 'orchestrator') or 
               st.session_state.orchestrator is None) and wait_count < max_wait_time:
            print(f"[VAD] orchestrator 초기화 대기 중... ({wait_count + 1}/{max_wait_time})")
            await asyncio.sleep(0.5)  # 대기 간격 단축
            wait_count += 1
            
        if wait_count >= max_wait_time:
            print("[VAD] orchestrator 초기화 시간 초과 - 소비자 루프 종료")
            return
            
        print(f"[VAD] ✅ orchestrator 연결 성공: {st.session_state.orchestrator}")
            
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


# WebRTC 스트리머 렌더링
st.write("## 🎥 WebRTC 스트리머")

# WebRTC 컴포넌트 렌더링 (객체 정보 출력 방지)
# ctx는 WebRtcStreamerContext 객체로, 자동으로 UI 컴포넌트로 변환됩니다

playing_now = ctx.state.playing
was_playing = st.session_state.was_playing

# WebRTC 상태 디버깅
st.info(f"🎥 WebRTC 상태: playing={playing_now}, was_playing={was_playing}")
st.info(f"🔧 세션 상태: wired_lifecycle={st.session_state.wired_lifecycle}, orchestrator={st.session_state.orchestrator is not None}")

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
        
        # 스트리밍 오케스트레이터 시작 (dual_audio_service 전달)
        orchestrator = StreamOrchestrator(
            http_client=http,
            session_id=session_manager.session_id,
            data_pipeline=data_pipeline,
            audio_accumulator=st.session_state.audio_acc,
            metrics=metrics,
            errbuf=errbuf,
            dual_audio_service=st.session_state.dual_audio_service  # PyAudio 서비스 전달
        )
        
        submit_coro(orchestrator.start())
        st.session_state.orchestrator = orchestrator
        
        # VAD 소비자 루프 시작 (orchestrator 설정 후)
        st.session_state.vad_consumer_task = submit_coro(
            _vad_consumer_loop(st.session_state.stop_evt)
        )
        print(f"[App] 🔄 VAD 소비자 태스크 생성됨: {st.session_state.vad_consumer_task}")
        
    st.success("Streaming… 프레임과 오디오를 FastAPI로 처리 중")
else:
    st.write("START 버튼으로 연결을 시작하세요.")

# PAUSE 버튼 클릭 시 (playing_now = False) - 세션 일시정지
if (not playing_now) and was_playing and st.session_state.wired_lifecycle:
    st.session_state.wired_lifecycle = False
    
    # stop_evt 설정하여 VAD 소비자 루프 중단
    st.session_state.stop_evt.set()
    
    # VAD 소비자 태스크 정리
    if st.session_state.vad_consumer_task:
        st.session_state.vad_consumer_task = None
    
    # 오케스트레이터 종료 및 최종 요약 처리
    if st.session_state.orchestrator:
        print("[App] 🔄 오케스트레이터 종료 및 최종 요약 처리 시작")
        submit_coro(st.session_state.orchestrator.stop())
        print("[App] ✅ PAUSE 버튼 처리 완료 - 세션 일시정지됨 (재시작 가능)")
    else:
        print("[App] ✅ STOP 버튼 처리 완료 - 세션은 유지됨 (재시작 가능)")
    
    # 🚀 모델 추론은 orchestrator.stop()에서 이미 처리됨
    st.write("🚀 **모델 추론 완료됨** (orchestrator에서 자동 처리)")
    
    # 오케스트레이터가 있으면 상태 확인
    if st.session_state.orchestrator:
        orch_status = "🟢 정상 종료" if st.session_state.orchestrator else "⚠️ 종료 중"
        st.info(f"오케스트레이터 상태: {orch_status}")
    else:
        st.warning("⚠️ 오케스트레이터가 없어서 모델 추론을 건너뜁니다.")
    
    # 🎵 오디오 재생은 orchestrator에서 처리된 결과 사용
    try:
        # transcript_q에서 전사 결과 확인
        if hasattr(st.session_state, 'data_pipeline') and st.session_state.data_pipeline:
            transcript_q = st.session_state.data_pipeline.transcript_q
            if not transcript_q.empty():
                transcript = transcript_q.get()
                st.write("🎤 **음성 전사 결과:**")
                st.write(f"📝 {transcript}")
            else:
                st.write("🎤 **음성 전사**: 아직 처리되지 않았습니다.")
        
        # 감정 분석 결과 표시
        if hasattr(st.session_state, 'emotion_service') and st.session_state.emotion_service:
            emotion_summary = st.session_state.emotion_service.get_final_summary()
            if emotion_summary:
                st.write("🎭 **감정 분석 결과:**")
                if isinstance(emotion_summary, dict) and 'video' in emotion_summary:
                    video_data = emotion_summary['video']
                    if isinstance(video_data, dict):
                        st.write(f"**주요 감정**: {video_data.get('label', 'N/A')} (점수: {video_data.get('score', 0):.2f})")
                        st.write(f"**분석 프레임 수**: {video_data.get('count', 0)}개")
                
                if emotion_summary.get('audio'):
                    audio_data = emotion_summary['audio']
                    if isinstance(audio_data, dict):
                        transcript = audio_data.get('transcript', '')
                        st.write(f"**음성 전사**: {transcript}")
                        st.write(f"**전사 횟수**: {audio_data.get('count', 0)}회")
        
        st.write("✅ 분석 완료 - START 버튼으로 새로운 분석을 시작하세요.")
        
    except Exception as e:
        st.error(f"❌ 결과 표시 실패: {e}")
        print(f"[App] 결과 표시 오류: {e}")
    
    # 오케스트레이터 종료 후 초기화 (재시작을 위해 None으로 설정하지 않음)
    if hasattr(st.session_state, 'orchestrator') and st.session_state.orchestrator:
        submit_coro(st.session_state.orchestrator.stop())
        # st.session_state.orchestrator = None  # 재시작을 위해 주석 처리

# WebRTC 재연결 및 세션 상태 표시
if not playing_now and was_playing:
    st.info("⏸️ WebRTC 연결이 일시정지되었습니다. START 버튼으로 재시작하거나 End Session 버튼으로 완전 종료하세요.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ 세션 재시작"):
            st.rerun()
    with col2:
        if st.button("🔄 페이지 새로고침"):
            st.rerun()
elif playing_now:
    st.success("🟢 WebRTC 연결 활성화됨")
elif st.session_state.wired_lifecycle:
    st.info("⚪ 세션 활성 + WebRTC 대기 중 - START 버튼을 클릭하세요")
else:
    st.info("⚪ 세션 비활성 - START 버튼을 클릭하세요")

# End Session 버튼
st.write("---")
if st.button("🛑 End Session", type="primary"):
    print("[App] 🔄 End Session 시작")
    
    # 1. 모든 처리 중단
    st.session_state.stop_evt.set()
    st.session_state.wired_lifecycle = False
    
    # 2. WebRTC 연결 중단
    if hasattr(st.session_state, 'ctx') and st.session_state.ctx:
        st.session_state.ctx.playing = False
    
    # 3. 세션 종료
    submit_coro(session_manager.stop_session())
    
    # 4. 오케스트레이터 종료 및 초기화
    if hasattr(st.session_state, 'orchestrator') and st.session_state.orchestrator:
        submit_coro(st.session_state.orchestrator.stop())
        st.session_state.orchestrator = None  # 완전 종료 시에는 None으로 설정
    
    # 5. 오디오 누적기 초기화
    if hasattr(st.session_state, 'audio_acc'):
        st.session_state.audio_acc.reset()
    
    # 6. VAD 어셈블러 초기화
    if hasattr(st.session_state, 'vad_assembler'):
        st.session_state.vad_assembler.reset()
    
    # 7. VAD 소비자 태스크 정리
    if hasattr(st.session_state, 'vad_consumer_task'):
        st.session_state.vad_consumer_task = None
    
    print("[App] ✅ End Session 완료")
    st.success("✅ 세션이 완전히 종료되었습니다.")
    

