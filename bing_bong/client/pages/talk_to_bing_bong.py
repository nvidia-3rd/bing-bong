
# app.py
import streamlit as st
# from services.async_exec import ensure_bg_loop
from streamlit_webrtc import WebRtcMode, webrtc_streamer
from infrastructure.config import RTC_CONFIG, QUEUE_MAXSIZE, SAMPLE_EVERY
from infrastructure.http_client import get_http  # ✅ StreamOrchestrator에서 필요
from infrastructure.metrics import get_metrics, get_errbuf
from infrastructure.VADBlockAssembler import VADBlockAssembler
from infrastructure.webrtc_handlers import (
    make_video_frame_callback,
    AudioAccumulator,
    make_vad_audio_callback_with_accumulator,
)
from core.data_pipeline import DataPipeline
from core.stream_orchestrator import StreamOrchestrator
from services.dual_audio_service import DualAudioService
from core.session_manager import get_session_manager, get_session_id, get_session_info, get_session_status, get_session_duration, start_session, stop_session, reset_session

import uuid
import asyncio
import os
import time

# 백그라운드 asyncio 실행 루프 준비
# ensure_bg_loop()

# 세션 매니저 초기화
session_manager = get_session_manager()

def cleanup_session():
    """세션 정리 (동기 방식)"""
    try:
        print(f"[Session] 🧹 세션 정리 시작: {session_manager.get_session_id()}")
        
        # 1. 모든 처리 중단
        if hasattr(st.session_state, 'stop_evt'):
            st.session_state.stop_evt.set()
        
        # 2. 오케스트레이터 종료 (안전한 방식)
        if hasattr(st.session_state, 'orchestrator') and st.session_state.orchestrator:
            print("[Session] 🔄 오케스트레이터 종료 중...")
            try:
                # 직접 오케스트레이터 종료 (submit_coro 제거)
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(st.session_state.orchestrator.stop())
                loop.close()
                print("[Session] ✅ 오케스트레이터 종료 완료")
                    
            except Exception as e:
                print(f"[Session] ⚠️ 오케스트레이터 종료 오류: {e}")
        
        # 3. 세션 상태 초기화
        session_manager.stop_session()
        print(f"[Session] ✅ 세션 정리 완료: {session_manager.get_session_id()}")
        
    except Exception as e:
        print(f"[Session] ❌ 세션 정리 오류: {e}")

# 브라우저 종료 시 자동 정리를 위한 JavaScript 코드
def inject_cleanup_script():
    """브라우저 종료 시 정리를 위한 JavaScript 주입"""
    st.markdown("""
    <script>
    // 페이지 언로드 시 정리 작업
    window.addEventListener('beforeunload', function(e) {
        // 정리 작업 수행
        console.log('페이지 언로드 - 세션 정리 시작');
        
        // 동기적 정리 (페이지가 닫히기 전에 완료)
        try {
            // localStorage에 정리 플래그 설정
            localStorage.setItem('session_cleanup_needed', 'true');
            localStorage.setItem('session_id', '""" + session_manager.get_session_id() + """');
            
            // WebRTC 연결 정리 시도
            if (window.webrtcContext) {
                try {
                    console.log('WebRTC 연결 정리 시도...');
                    // WebRTC 컨텍스트 정리
                    if (window.webrtcContext.pc) {
                        window.webrtcContext.pc.close();
                        console.log('WebRTC PeerConnection 정리 완료');
                    }
                } catch (webrtcError) {
                    console.warn('WebRTC 정리 중 오류:', webrtcError);
                }
            }
            
        } catch(err) {
            console.error('정리 플래그 설정 실패:', err);
        }
    });
    
    // 페이지 로드 시 정리 플래그 확인
    window.addEventListener('load', function() {
        try {
            const cleanupNeeded = localStorage.getItem('session_cleanup_needed');
            const sessionId = localStorage.getItem('session_id');
            
            if (cleanupNeeded === 'true' && sessionId) {
                console.log('이전 세션 정리 필요:', sessionId);
                // 정리 플래그 제거
                localStorage.removeItem('session_cleanup_needed');
                localStorage.removeItem('session_id');
            }
        } catch(err) {
            console.error('정리 플래그 확인 실패:', err);
        }
    });
    
    // 페이지 숨김 시에도 정리 시도
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'hidden') {
            console.log('페이지 숨김 - 정리 준비');
            try {
                localStorage.setItem('session_cleanup_needed', 'true');
                localStorage.setItem('session_id', '""" + session_manager.get_session_id() + """');
            } catch(err) {
                console.error('정리 플래그 설정 실패:', err);
            }
        }
    });
    </script>
    """, unsafe_allow_html=True)

st.set_page_config(page_title="WebRTC Proxy → FastAPI", layout="centered")

# JavaScript 정리 스크립트 주입
inject_cleanup_script()

# 인프라 초기화
http = get_http()
metrics = get_metrics()
errbuf = get_errbuf()

# 상태 초기화
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

# VAD 스레드 상태 초기화
if "vad_thread" not in st.session_state:
    st.session_state.vad_thread = None

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
    print(f"[WebRTC] 🔗 연결 상태 변경: {state}")
    if state == "connected":
        print("[WebRTC] ✅ WebRTC 연결 성공 - 프레임/오디오 전송 시작")
    elif state == "disconnected":
        print("[WebRTC] ❌ WebRTC 연결 해제")
    elif state == "failed":
        print("[WebRTC] ❌ WebRTC 연결 실패")

ctx.on_connection_state_change = on_state_change

# 데이터 채널 바인딩 코드 삭제됨 - /ingest/data 엔드포인트가 서버에 없음

# VAD 소비자 루프 함수
async def _vad_consumer_loop(stop_evt: asyncio.Event, orchestrator, vad_assembler, audio_accumulator, data_pipeline, session_id, error_buffer):
    """VAD에서 처리된 오디오 블록을 소비하고 전사 처리"""
    try:
        print(f"[VAD] ✅ VAD 소비자 루프 시작 - orchestrator={orchestrator}, session_id={session_id}")
        
        # orchestrator가 준비될 때까지 짧게 대기
        await asyncio.sleep(1.0)
        
        while not stop_evt.is_set():
            try:
                # 15초 블록 또는 조기 플러시 블록 대기
                wav_bytes = await vad_assembler.get_block(timeout=0.5)
                if not wav_bytes:
                    continue
                    
                # stop_event 재확인 (블록 처리 전)
                if stop_evt.is_set():
                    print("[VAD] 🛑 stop_event 감지 - 오디오 블록 처리 중단")
                    break
                    
                print(f"[VAD] 🎵 오디오 블록 수신: {len(wav_bytes)} bytes")
                
                # AudioAccumulator 상태 확인
                audio_info = audio_accumulator.get_audio_info()
                print(f"[VAD] 📊 AudioAccumulator 상태: {audio_info['wav_files_count']}개 WAV 파일, {audio_info['total_bytes']} bytes")
                
                # stop_event 재확인 (업로드 전)
                if stop_evt.is_set():
                    print("[VAD] 🛑 stop_event 감지 - 오디오 업로드 중단")
                    break
                    
                # 업로드 → 전사
                resp = await orchestrator.audio_service.upload_audio(session_id, wav_bytes, error_buffer)
                tr = orchestrator.audio_service.extract_transcript(resp)
                if tr:
                    orchestrator.emotion_service.add_audio_emotion(tr)
                    data_pipeline.safe_put(data_pipeline.transcript_q, tr)
                    print(f"[VAD] 📝 전사 결과: {tr[:80]}...")
                else:
                    print("[VAD] ⚠️ 전사 결과가 없습니다")
                    
            except Exception as e:
                error_buffer.push(f"VAD 소비 루프 오류: {e}")
                print(f"[VAD] ❌ 처리 오류: {e}")
                await asyncio.sleep(0.1)  # 오류 시 잠시 대기
                
    except Exception as e:
        print(f"[VAD] ❌ 소비자 루프 전체 오류: {e}")
        error_buffer.push(f"VAD 소비자 루프 전체 오류: {e}")


# 🎵 LLM Audio 재생 관련 함수들
# 현재 사용되지 않는 오디오 메서드들 - 제거됨
# 필요시 DualAudioService를 통해 오디오 재생 처리

def check_and_play_llm_audio():
    """LLM 오디오 큐를 확인하고 자동 재생"""
    try:
        if hasattr(st.session_state, 'orchestrator') and st.session_state.orchestrator:
            # 오디오 큐 상태 확인
            audio_queue_size = st.session_state.orchestrator.audio_q.qsize()
            
            if audio_queue_size > 0:
                # 오디오 큐에서 데이터 가져오기
                audio_data = st.session_state.orchestrator.audio_q.get_nowait()
                if audio_data and isinstance(audio_data, bytes):
                    print(f"[Audio] 🎵 LLM 오디오 데이터 발견: {len(audio_data)} bytes")
                    
                    # DualAudioService로 오디오 재생
                    if hasattr(st.session_state, 'dual_audio_service') and st.session_state.dual_audio_service:
                        success = st.session_state.dual_audio_service.play_audio(audio_data)
                        if success:
                            print("[Audio] ✅ LLM 오디오 자동 재생 성공!")
                            return True
                        else:
                            print("[Audio] ❌ LLM 오디오 자동 재생 실패")
                            return False
                    else:
                        print("[Audio] ⚠️ DualAudioService가 없어 오디오 재생 불가")
                        return False
                else:
                    print("[Audio] ⚠️ 오디오 큐에 유효하지 않은 데이터")
                    return False
            else:
                return False  # 큐가 비어있음
        else:
            return False  # 오케스트레이터가 없음
            
    except Exception as e:
        print(f"[Audio] ❌ LLM 오디오 확인/재생 오류: {e}")
        return False

# WebRTC 스트리머 렌더링
st.write("## 🎥 WebRTC 스트리머")

# WebRTC 컴포넌트 렌더링 (객체 정보 출력 방지)
# ctx는 WebRtcStreamerContext 객체로, 자동으로 UI 컴포넌트로 변환됩니다

playing_now = ctx.state.playing
was_playing = st.session_state.was_playing

# WebRTC 상태 디버깅
st.info(f"🎥 WebRTC 상태: playing={playing_now}, was_playing={was_playing}")
st.info(f"🔧 세션 상태: wired_lifecycle={st.session_state.wired_lifecycle}, orchestrator={st.session_state.orchestrator is not None}")

# WebRTC 연결 상태 상세 로깅
if hasattr(ctx, 'state'):
    st.info(f"🔗 WebRTC 연결 상태: {ctx.state}")
if hasattr(ctx, 'connection_state'):
    st.info(f"🔗 ICE 연결 상태: {ctx.connection_state}")
    
# WebRTC 연결 상태를 더 정확하게 확인
def get_webrtc_status():
    """WebRTC 연결 상태를 정확하게 반환"""
    try:
        if hasattr(ctx, 'connection_state'):
            return ctx.connection_state
        elif hasattr(ctx, 'state'):
            return ctx.state.playing
        else:
            return "unknown"
    except:
        return "unknown"

st.session_state.was_playing = playing_now

# START 버튼 클릭 시 (playing_now = True)
if playing_now:
    if not st.session_state.wired_lifecycle:
        st.session_state.wired_lifecycle = True
        
        # WebRTC 연결 상태 확인
        print(f"[App] 🔗 WebRTC 연결 상태: playing={playing_now}")
        if hasattr(ctx, 'connection_state'):
            print(f"[App] 🔗 ICE 연결 상태: {ctx.connection_state}")
        
        # 오디오 누적기 초기화
        st.session_state.audio_acc.reset()
        st.session_state.vad_assembler.reset()
        
        # stop_evt 리셋
        st.session_state.stop_evt.clear()
        
        # WebRTC 연결 안정화를 위한 대기
        import time
        time.sleep(2.0)  # 2초 대기하여 WebRTC 연결 안정화
        print("[App] ⏳ WebRTC 연결 안정화 대기 완료")
        
        # ✅ 세션 매니저를 통한 세션 시작
        if session_manager.start_session():
            print(f"[App] ✅ 세션 시작됨: {session_manager.get_session_id()}")
        else:
            print(f"[App] ❌ 세션 시작 실패")
            st.session_state.wired_lifecycle = False
            st.error("❌ 세션 시작에 실패했습니다. 다시 시도해주세요.")
            st.stop()
        
        # 오케스트레이터 생성 및 시작
        st.session_state.orchestrator = StreamOrchestrator(
            http_client=http,
            session_id=session_manager.get_session_id(),
            data_pipeline=data_pipeline,
            audio_accumulator=st.session_state.audio_acc,
            metrics=metrics,
            errbuf=errbuf,
            dual_audio_service=st.session_state.dual_audio_service
        )
        
        # LLM 완료 시 오디오 자동 재생 콜백 설정
        def on_llm_audio_complete(audio_bytes: bytes):
            """LLM 응답 오디오가 준비되면 자동으로 재생 (동기 처리)"""
            try:
                print(f"[App] 🎵 LLM 오디오 자동 재생 시작: {len(audio_bytes)} bytes")
                
                if st.session_state.dual_audio_service:
                    # DualAudioService를 통해 오디오 재생 (동기적)
                    success = st.session_state.dual_audio_service.play_audio(audio_bytes)
                    if success:
                        print("[App] ✅ LLM 오디오 자동 재생 성공!")
                        # UI에 성공 메시지 표시
                        st.success("🎵 LLM 오디오 자동 재생 완료!")
                    else:
                        print("[App] ❌ LLM 오디오 자동 재생 실패")
                        st.error("❌ LLM 오디오 재생 실패")
                else:
                    print("[App] ⚠️ DualAudioService가 없어 오디오 재생 불가")
                    st.warning("⚠️ DualAudioService가 없어 오디오 재생 불가")
            except Exception as e:
                print(f"[App] ❌ LLM 오디오 자동 재생 오류: {e}")
                st.error(f"❌ LLM 오디오 재생 오류: {e}")
        
        # 오케스트레이터에 콜백 설정
        st.session_state.orchestrator.on_llm_complete = on_llm_audio_complete
        
        # 🚀 오디오 처리 완료 후 LLM 요청 실행 콜백 설정
        async def on_audio_finalization_complete():
            """오디오 처리 완료 후 LLM 요청을 실행하는 콜백"""
            try:
                print("[App] 🚀 오디오 처리 완료 콜백 실행 - LLM 요청 시작!")
                if st.session_state.orchestrator:
                    # LLM 요청 실행
                    await st.session_state.orchestrator._try_llm_processing()
                    print("[App] ✅ LLM 요청 실행 완료!")
                else:
                    print("[App] ⚠️ 오케스트레이터가 없어 LLM 요청 실행 불가")
            except Exception as e:
                print(f"[App] ❌ LLM 요청 실행 오류: {e}")
        
        st.session_state.orchestrator.on_audio_finalization_complete = on_audio_finalization_complete
        print("[App] ✅ 오디오 처리 완료 후 LLM 요청 콜백 설정 완료")
        print("[App] ✅ LLM 오디오 자동 재생 콜백 설정 완료")
        
        # 오케스트레이터 시작
        try:
            # 오케스트레이터를 직접 시작 (백그라운드 스레드 제거)
            import asyncio
            print(f"[App] 🚀 오케스트레이터 직접 시작: {st.session_state.orchestrator}")
            
            # 새로운 이벤트 루프에서 실행
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                # start()는 이제 즉시 반환되므로 블로킹되지 않음
                loop.run_until_complete(st.session_state.orchestrator.start())
                print("[App] ✅ 오케스트레이터 시작 완료")
                
                # 오케스트레이터 상태 확인
                if st.session_state.orchestrator:
                    print(f"[App] 🔍 오케스트레이터 상태 확인: {st.session_state.orchestrator}")
                    print(f"[App] 🔍 오케스트레이터 타입: {type(st.session_state.orchestrator)}")
                    
                    # 데이터 파이프라인 상태 확인
                    if hasattr(st.session_state, 'data_pipeline') and st.session_state.data_pipeline:
                        dp = st.session_state.data_pipeline
                        print(f"[App] 📊 데이터 파이프라인 상태:")
                        print(f"  - raw_frame_q: {dp.raw_frame_q.qsize()}/{dp.raw_frame_q.maxsize}")
                        print(f"  - frame_q: {dp.frame_q.qsize()}/{dp.frame_q.maxsize}")
                        print(f"  - image_result_q: {dp.image_result_q.qsize()}/{dp.image_result_q.maxsize}")
                        
                        # WebRTC 상태도 확인
                        if hasattr(ctx, 'state'):
                            print(f"[App] 🎥 WebRTC 상태: {ctx.state}")
                        if hasattr(ctx, 'connection_state'):
                            print(f"[App] 🔗 ICE 연결 상태: {ctx.connection_state}")
                else:
                    print("[App] ❌ 오케스트레이터가 None입니다!")
                    
            except Exception as e:
                print(f"[App] ❌ 오케스트레이터 시작 중 오류: {e}")
                st.error(f"오케스트레이터 시작 실패: {e}")
            finally:
                loop.close()
                
        except Exception as e:
            print(f"[App] ❌ 오케스트레이터 시작 실패: {e}")
            st.error(f"오케스트레이터 시작 실패: {e}")
        
        # VAD 소비자 루프 시작 (orchestrator 설정 후)
        try:
            # VAD 소비자 루프를 백그라운드에서 실행 (무한 루프이므로)
            import asyncio
            import threading
            
            # 메인 스레드에서 필요한 객체들을 미리 가져오기
            stop_evt = st.session_state.stop_evt
            print(f"[App] 🔧 stop_evt 준비: {stop_evt}")
            
            def run_vad_loop():
                """백그라운드에서 VAD 루프 실행 (클로저로 객체들에 접근)"""
                try:
                    print(f"[VAD] 🚀 백그라운드 VAD 루프 시작")
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    print(f"[VAD] 🔧 이벤트 루프 설정 완료: {loop}")
                    
                    # VAD 루프 실행 (무한 루프이므로 run_until_complete 사용)
                    print(f"[VAD] 🎯 _vad_consumer_loop 실행 시작")
                    loop.run_until_complete(_vad_consumer_loop(stop_evt, orch, vad, audio_acc, dp, session_id, errbuf))
                    
                except Exception as e:
                    print(f"[VAD] ❌ 백그라운드 VAD 루프 오류: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    try:
                        loop.close()
                        print(f"[VAD] 🔧 이벤트 루프 정리 완료")
                    except:
                        pass
                    print("[VAD] 🔄 백그라운드 VAD 루프 종료")
            
            # 메인 스레드에서 필요한 객체들을 미리 가져오기
            orch = st.session_state.orchestrator
            vad = st.session_state.vad_assembler
            audio_acc = st.session_state.audio_acc
            dp = st.session_state.data_pipeline
            session_id = session_manager.get_session_id()
            errbuf = errbuf  # 전역 변수 사용
            
            print(f"[App] 🔧 VAD 루프에 전달할 객체들 준비: orch={orch}, vad={vad}, session_id={session_id}")
            
            # 백그라운드 스레드에서 VAD 루프 실행
            vad_thread = threading.Thread(target=run_vad_loop, daemon=True)
            vad_thread.start()
            st.session_state.vad_thread = vad_thread
            
            print("[App] 🔄 VAD 소비자 루프 백그라운드 시작 완료")
            
            # VAD 루프가 안정화될 때까지 잠시 대기
            import time
            time.sleep(1.0)  # 1초 대기
            print("[App] ⏳ VAD 루프 안정화 대기 완료")
            
            # 데이터 채널 바인딩 코드 삭제됨 - /ingest/data 엔드포인트가 서버에 없음
                
        except Exception as e:
            print(f"[App] ❌ VAD 소비자 루프 시작 실패: {e}")
            st.error(f"VAD 소비자 루프 시작 실패: {e}")
        
    st.success("Streaming… 프레임과 오디오를 FastAPI로 처리 중")
else:
    st.write("START 버튼으로 연결을 시작하세요.")

# stop 버튼 클릭 시 (playing_now = False) - 세션 일시정지
# WebRTC 연결이 실제로 종료되었는지 확인
if (not playing_now) and was_playing and st.session_state.wired_lifecycle:
    # WebRTC 연결 상태 재확인
    webrtc_status = get_webrtc_status()
    print(f"[App] 🔍 STOP 조건 확인: WebRTC 상태 = {webrtc_status}")
    
    # WebRTC 연결이 실제로 종료되었는지 확인
    if webrtc_status in ['closed', 'failed', 'disconnected'] or not playing_now:
        print("[App] 🛑 WebRTC 연결이 실제로 종료됨 - STOP 로직 실행")
    else:
        print(f"[App] ⚠️ WebRTC 연결이 아직 활성 상태 ({webrtc_status}) - STOP 로직 건너뜀")
        st.warning(f"⚠️ WebRTC 연결이 아직 활성 상태입니다 ({webrtc_status}). 잠시 후 다시 시도해주세요.")
        st.stop()
    st.session_state.wired_lifecycle = False
    
    # stop_evt 설정하여 VAD 소비자 루프 중단
    st.session_state.stop_evt.set()
    print("[App] 🛑 stop_event 설정 - VAD 소비자 루프 중단 신호")
    
    # VAD 소비자 루프가 완전히 중단될 때까지 잠시 대기
    import time
    time.sleep(0.5)  # 0.5초 대기하여 진행 중인 오디오 처리 완료
    
    print("[App] 🔄 VAD 소비자 루프 중단 완료")
    
    # VAD 소비자 스레드 정리
    if hasattr(st.session_state, 'vad_thread') and st.session_state.vad_thread:
        vad_thread = st.session_state.vad_thread
        if vad_thread and vad_thread.is_alive():
            print(f"[App] 🧹 VAD 스레드 정리 중...")
            # 스레드가 자연스럽게 종료되도록 대기
            vad_thread.join(timeout=1.0)
            if vad_thread.is_alive():
                print(f"[App] ⚠️ VAD 스레드 강제 종료")
            else:
                print(f"[App] ✅ VAD 스레드 정리 완료")
        st.session_state.vad_thread = None
    
    # 오케스트레이터 종료는 한 번만 실행 (중복 제거)
    if st.session_state.orchestrator:
        print("[App] 🔄 오케스트레이터 종료 및 최종 요약 처리 시작")
        
        # 안전한 종료 처리
        try:
            # 오케스트레이터를 직접 종료 (백그라운드 스레드 제거)
            import asyncio
            print(f"[App] 🔄 오케스트레이터 직접 종료: {st.session_state.orchestrator}")
            
            # 새로운 이벤트 루프에서 실행
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                # stop() 실행
                loop.run_until_complete(st.session_state.orchestrator.stop())
                print("[App] ✅ 오케스트레이터 종료 완료")
            except Exception as e:
                print(f"[App] ❌ 오케스트레이터 종료 중 오류: {e}")
            finally:
                loop.close()
                
        except Exception as e:
            print(f"[App] ❌ 오케스트레이터 종료 오류: {e}")
            st.error(f"오케스트레이터 종료 오류: {e}")
                    
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
        print("[App] ℹ️ 오케스트레이터는 재시작을 위해 유지됨")
        # st.session_state.orchestrator = None  # 재시작을 위해 주석 처리

# WebRTC 재연결 및 세션 상태 표시
if not playing_now and was_playing:
    st.info("⏸️ WebRTC 연결이 일시정지되었습니다. START 버튼으로 재시작하거나 End Session 버튼으로 완전 종료하세요.")

# End Session 버튼
if st.button("End Session", type="secondary"):
    cleanup_session()
    st.rerun()

