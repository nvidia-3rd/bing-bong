# stream_orchestrator.py
import asyncio
import time
import streamlit as st
import queue
import httpx
from typing import Optional, List
from infrastructure.metrics import Metrics, ErrorBuf
from infrastructure.http_client import post_frame
from services.emotion_service import EmotionService
from services.video_service import VideoService
from services.audio_service import AudioService
from core.data_pipeline import DataPipeline
# TaskManager 제거 - Streamlit asyncio 직접 사용
from infrastructure.dto.llm_request_model import LlmRequestModel
from infrastructure.http_client import post_llm_request

# 전역 변수로 상태 관리
TRANSCRIPT_BUFFER = []
EMOTION_QUEUE_SIZE = 0
LLM_AUDIO_RESPONSE = None

class StreamOrchestrator:
    """
    스트리밍 세그먼트 오케스트레이션
    - 비디오/오디오 파이프라인 관리
    - 감정 분석 배치 집계
    - 종료 시 최종 드레인/정리
    """

    # 집계 트리거(하이브리드): N개 도달 또는 T초 경과 시 요약
    BATCH_MIN: int = 5
    BATCH_MAX: int = 60
    AGG_INTERVAL_SEC: float = 0.2
    FLUSH_SEC: float = 3.0

    def __init__(
        self,
        http_client,
        session_id: str,
        data_pipeline: DataPipeline,
        audio_accumulator,
        metrics: Metrics,
        errbuf: ErrorBuf,
        dual_audio_service=None,  # PyAudio 우선 처리를 위한 서비스
    ):
        self.http = http_client
        self.session_id = session_id
        self.data_pipeline = data_pipeline
        self.audio_accumulator = audio_accumulator
        self.metrics = metrics
        self.errbuf = errbuf

        self.emotion_service = EmotionService()
        self.video_service = VideoService()
        self.audio_service = AudioService(http_client)
        self.dual_audio_service = dual_audio_service  # PyAudio 서비스 저장

        self.stop_evt = asyncio.Event()
        self.tasks: List[asyncio.Task] = []
        self.audio_q = queue.Queue(maxsize=50)  # 오디오 큐 크기 증가
        
        # TaskManager 제거 - 직접 asyncio 태스크 관리
        
        # 재진입/중복 실행 방지 플래그
        self._started: bool = False
        self._stopping: bool = False
        self._llm_processing: bool = False  # LLM 처리 중 플래그
        
        # Audio 재생 콜백 추가
        self.on_llm_complete = None  # LLM 완료 시 audio 재생 콜백
        
        # 전역 변수 초기화
        global TRANSCRIPT_BUFFER, EMOTION_QUEUE_SIZE
        TRANSCRIPT_BUFFER.clear()
        EMOTION_QUEUE_SIZE = 0
        
        # 데이터 파이프라인 콜백 설정
        self._setup_data_pipeline_callbacks()

    # -------------------------
    # 라이프사이클
    # -------------------------
    async def start(self) -> None:
        if self._started and not self._stopping:
            return  # 이미 실행 중

        print("[Orchestrator] 🚀 StreamOrchestrator 시작")
        self._stopping = False
        self._started = True
        self.stop_evt.clear()

        # 직접 asyncio.create_task 사용 (순서 중요!)
        self.tasks = [
            asyncio.create_task(self._frame_processor_loop(), name="frame_processor"),      # 1순위: 프레임 인코딩
            asyncio.create_task(self._frame_uploader_loop(), name="frame_uploader"),        # 2순위: 프레임 업로드
            asyncio.create_task(self._emotion_aggregator_loop(), name="emotion_aggregator"), # 3순위: 감정 집계
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),                  # 4순위: 하트비트
            asyncio.create_task(self._memory_cleanup_loop(), name="memory_cleanup"),        # 5순위: 메모리 정리
        ]
        
        print(f"[Orchestrator] ✅ {len(self.tasks)}개 태스크 생성 완료:")
        for i, task in enumerate(self.tasks):
            print(f"  - 태스크 {i+1}: {self.tasks[i].get_name()}")
            print(f"    - 상태: {task.done()=}, {task.cancelled()=}")
        
        print("[Orchestrator] 🎉 StreamOrchestrator 시작 완료 (직접 asyncio 사용)")
        
        # 태스크가 실제로 실행되는지 확인
        await asyncio.sleep(0.2)  # 잠시 대기
        print(f"[Orchestrator] 🔍 태스크 실행 상태 확인:")
        for i, task in enumerate(self.tasks):
            print(f"  - {task.get_name()}: {task.done()=}, {task.cancelled()=}")
            
            # 태스크가 실행되지 않았다면 재시작
            if task.done() or task.cancelled():
                print(f"[Orchestrator] 🔄 태스크 {task.get_name()} 재시작")
                if task.get_name() == "frame_processor":
                    self.tasks[i] = asyncio.create_task(self._frame_processor_loop(), name="frame_processor")
                elif task.get_name() == "frame_uploader":
                    self.tasks[i] = asyncio.create_task(self._frame_uploader_loop(), name="frame_uploader")
                elif task.get_name() == "emotion_aggregator":
                    self.tasks[i] = asyncio.create_task(self._emotion_aggregator_loop(), name="emotion_aggregator")
                elif task.get_name() == "heartbeat":
                    self.tasks[i] = asyncio.create_task(self._heartbeat_loop(), name="heartbeat")
                elif task.get_name() == "memory_cleanup":
                    self.tasks[i] = asyncio.create_task(self._memory_cleanup_loop(), name="memory_cleanup")
        
        # 최종 상태 확인
        await asyncio.sleep(0.1)
        print(f"[Orchestrator] 🔍 최종 태스크 상태:")
        for i, task in enumerate(self.tasks):
            print(f"  - {task.get_name()}: {task.done()=}, {task.cancelled()=}")
            
        print("[Orchestrator] 🚀 모든 태스크 시작 완료!")

    def _setup_data_pipeline_callbacks(self) -> None:
        """데이터 파이프라인에 콜백 함수를 설정합니다."""
        try:
            # transcript 콜백: 전사 결과가 추가되면 플래그만 설정 (task 생성 안함)
            self.data_pipeline.on_transcript_added = self._on_transcript_added
            
            # emotion 콜백: 감정 분석 결과가 추가되면 플래그만 설정 (task 생성 안함)
            self.data_pipeline.on_emotion_added = self._on_emotion_added
            
            print("[Orchestrator] ✅ 데이터 파이프라인 콜백 설정 완료")
        except Exception as e:
            print(f"[Orchestrator] ❌ 콜백 설정 오류: {e}")

    def _on_transcript_added(self, transcript: str) -> None:
        """전사 결과가 추가되었을 때 호출되는 콜백"""
        global TRANSCRIPT_BUFFER
        print(f"[Orchestrator] 🎤 전사 결과 콜백: {transcript[:50]}...")
        
        # 전역 변수에 데이터 추가
        TRANSCRIPT_BUFFER.append(transcript)
        print(f"[Orchestrator] ✅ 전역 transcript 버퍼에 추가: {transcript} (총 {len(TRANSCRIPT_BUFFER)}개)")
        
        # 즉시 LLM 처리 시도 (이벤트 기반) - 안전한 방식으로 실행
        self._safe_create_task(self._try_llm_processing())

    def _on_emotion_added(self, emotion_data: dict) -> None:
        """감정 분석 결과가 추가되었을 때 호출되는 콜백"""
        global EMOTION_QUEUE_SIZE
        print(f"[Orchestrator] 🎭 감정 분석 결과 콜백: {type(emotion_data)}")
        
        # 전역 변수 업데이트
        EMOTION_QUEUE_SIZE = self.data_pipeline.emotion_summary_q.qsize()
        print(f"[Orchestrator] ✅ 전역 emotion 큐 크기 업데이트: {EMOTION_QUEUE_SIZE}")

    def _safe_create_task(self, coro):
        """안전하게 태스크를 생성하는 헬퍼 함수"""
        try:
            # 현재 실행 중인 이벤트 루프 확인
            loop = asyncio.get_running_loop()
            if loop and loop.is_running():
                # 직접 asyncio.create_task 사용
                task = asyncio.create_task(coro, name="llm_processing")
                print(f"[Orchestrator] ✅ 직접 태스크 생성 성공: {task}")
                return task
            else:
                print("[Orchestrator] ⚠️ 이벤트 루프가 실행 중이지 않아 태스크 생성 건너뜀")
                return None
        except RuntimeError:
            print("[Orchestrator] ⚠️ 이벤트 루프를 가져올 수 없어 태스크 생성 건너뜀")
            return None
        except Exception as e:
            print(f"[Orchestrator] ❌ 태스크 생성 오류: {e}")
            return None

    # async def _llm_processing_loop(self) -> None:
    #     """LLM 처리를 위한 지속적인 루프 - 이벤트 기반 처리로 대체됨"""
    #     print("[Orchestrator] 🔄 LLM 처리 루프 시작 (사용되지 않음)")
    #     # 이 메서드는 이벤트 기반 처리로 대체되었습니다
    #     # _try_llm_processing() 메서드를 사용하세요
    #     pass

    async def _try_llm_processing(self) -> None:
        """콜백에서 즉시 LLM 처리 시도"""
        global TRANSCRIPT_BUFFER, EMOTION_QUEUE_SIZE
        
        try:
            # 전역 변수로 상태 확인
            transcript_ready = len(TRANSCRIPT_BUFFER) > 0
            emotion_ready = EMOTION_QUEUE_SIZE > 0
            
            print(f"[Orchestrator] 🔍 즉시 LLM 처리 시도: transcript_ready={transcript_ready}, emotion_ready={emotion_ready}")
            
            if transcript_ready and emotion_ready and not self._llm_processing:
                print("[Orchestrator] 🚀 즉시 LLM 처리 시작!")
                self._llm_processing = True
                
                try:
                    await self._process_llm_request()
                    print("[Orchestrator] 🎵 즉시 LLM 처리 완료!")
                finally:
                    self._llm_processing = False
                    # 처리 완료 후 전역 버퍼 비우기
                    TRANSCRIPT_BUFFER.clear()
                    print("[Orchestrator] 🧹 즉시 처리 후 전역 버퍼 비우기 완료")
            else:
                print(f"[Orchestrator] ⏳ LLM 처리 조건 미충족: transcript_ready={transcript_ready}, emotion_ready={emotion_ready}, processing={self._llm_processing}")
                
        except Exception as e:
            print(f"[Orchestrator] ❌ 즉시 LLM 처리 오류: {e}")
            self._llm_processing = False

    async def _process_llm_request(self) -> None:
        """LLM 요청을 처리합니다."""
        try:
            # 전역 변수에서 데이터 가져오기
            global TRANSCRIPT_BUFFER
            if not TRANSCRIPT_BUFFER:
                print("[Orchestrator] ⚠️ 전역 transcript 버퍼가 비어있습니다")
                return
            
            # 누적된 전사 텍스트들을 하나로 합치기
            transcript = " ".join(TRANSCRIPT_BUFFER)
            print(f"[Orchestrator] 📝 전역 버퍼에서 전사 텍스트: {len(TRANSCRIPT_BUFFER)}개 → '{transcript}'")
            
            # 감정 데이터는 큐에서 추출
            emotion_items = []
            while not self.data_pipeline.emotion_summary_q.empty():
                emotion_items.append(self.data_pipeline.emotion_summary_q.get_nowait())
            
            print(f"[Orchestrator] 🎤 LLM 요청 시작: 전사={len(transcript)}자, 감정={len(emotion_items)}개")
            
            # LLM 호출
            print(f"[Orchestrator] 📞 call_llm 호출 시작...")
            llm_response = await self.call_llm(transcript, emotion_items)
            print(f"[Orchestrator] 📞 call_llm 호출 완료, 응답: {type(llm_response)}")
            
            if llm_response and isinstance(llm_response, bytes):
                print(f"[Orchestrator] 🎵 LLM 음성 응답 수신: {len(llm_response)} bytes")
                # LLM 응답 audio를 audio queue에 추가 (듣고 시펑을 위해)
                self.audio_q.put_nowait(llm_response)
                print(f"[Orchestrator] 🎵 LLM 음성 응답을 audio queue에 추가: {len(llm_response)} bytes")
                
                # 기존 저장 로직도 유지
                self.llm_audio_response = llm_response
                print(f"[Orchestrator] 💾 LLM 음성 응답 저장 완료: {len(llm_response)} bytes")
                
                # Audio 재생 콜백 호출
                if self.on_llm_complete:
                    try:
                        print("[Orchestrator] 🎵 Audio 재생 콜백 호출 시작")
                        self.on_llm_complete(llm_response)
                        print("[Orchestrator] ✅ Audio 재생 콜백 호출 완료")
                    except Exception as e:
                        print(f"[Orchestrator] ❌ Audio 재생 콜백 오류: {e}")
                        self.errbuf.push(f"Audio 재생 콜백 오류: {e}")
                else:
                    print("[Orchestrator] ℹ️ Audio 재생 콜백이 설정되지 않음")
            else:
                print(f"[Orchestrator] ⚠️ LLM 응답이 비어있음: {type(llm_response)}")
                if llm_response is None:
                    print("[Orchestrator] ❌ LLM 응답이 None입니다")
                elif isinstance(llm_response, bytes) and len(llm_response) == 0:
                    print("[Orchestrator] ❌ LLM 응답이 빈 bytes입니다")
                else:
                    print(f"[Orchestrator] ❌ 예상치 못한 LLM 응답 타입: {type(llm_response)}")
                
        except Exception as e:
            self.errbuf.push(f"LLM 요청 처리 오류: {e}")
            print(f"[Orchestrator] ❌ LLM 요청 처리 오류: {e}")
            print(f"[Orchestrator] 🔍 오류 상세: {type(e).__name__}: {str(e)}")
            import traceback
            print(f"[Orchestrator] 🔍 스택 트레이스: {traceback.format_exc()}")

    async def stop(self) -> None:
        if self._stopping:
            return  # 중복 stop 방지

        print("[Orchestrator] 🛑 종료 시작...")
        self._stopping = True
        self.stop_evt.set()

        try:
            # 태스크 종료 전에 현재 이벤트 루프 상태 확인
            try:
                loop = asyncio.get_running_loop()
                if loop and not loop.is_closed():
                    # 직접 태스크 취소 및 종료
                    print("[Orchestrator] 🔄 직접 태스크 종료 처리...")
                    for task in self.tasks:
                        if not task.done():
                            task.cancel()
                            print(f"[Orchestrator] 🔄 태스크 취소: {task.get_name()}")
                    
                    # 태스크 종료 대기
                    if self.tasks:
                        await asyncio.gather(*self.tasks, return_exceptions=True)
                        print("[Orchestrator] ✅ 모든 태스크 종료 완료")
                    self.tasks.clear()
                else:
                    print("[Orchestrator] ⚠️ 이벤트 루프가 닫혀있어 태스크 종료 건너뜀")
            except RuntimeError:
                print("[Orchestrator] ⚠️ 실행 중인 이벤트 루프가 없어 태스크 종료 건너뜀")

            # 남은 데이터 처리
            try:
                await self._process_remaining_emotions()
            except Exception as e:
                print(f"[Orchestrator] ⚠️ 감정 데이터 최종 처리 오류: {e}")

            try:
                await self._process_audio_finalization()
            except Exception as e:
                print(f"[Orchestrator] ⚠️ 오디오 최종 처리 오류: {e}")

        except Exception as e:
            print(f"[Orchestrator] ❌ 종료 처리 중 오류: {e}")
        finally:
            # 상태 초기화
            self._started = False
            self._stopping = False
            print("[Orchestrator] ✅ 종료 완료")

    def reset(self) -> None:
        # 실행 중 reset 호출을 막거나 경고하는 게 안전
        if self._started and not self._stopping:
            return
        self.stop_evt.clear()
        self.tasks.clear()
    
    async def call_llm(self, audio_text: str, emotion_items: list) -> bytes:
        """
        LLM 요청을 전송하고 음성 응답을 반환합니다. (재시도 로직 포함)
        
        Args:
            audio_text: 오디오 전사 텍스트
            emotion_items: 감정 분석 결과 리스트
            
        Returns:
            bytes: LLM 음성 응답 (MP3 형식)
        """
        max_retries = 3
        retry_delay = 2.0  # 재시도 간격 (초)
        
        for attempt in range(max_retries):
            try:
                print(f"[Orchestrator] 🚀 LLM 요청 시도 {attempt + 1}/{max_retries}: 오디오={len(audio_text)}자, 감정={len(emotion_items)}개")
                
                llm_request_model = LlmRequestModel(
                    audio_text=audio_text,
                    emotion_summary=emotion_items,
                    session_id=self.session_id
                )
                print(f"[Orchestrator] 📦 LlmRequestModel 생성 완료: {llm_request_model.to_dict()}")

                print(f"[Orchestrator] 📡 LLM 요청 전송 시작...")
                print(f"[Orchestrator] 🔍 HTTP 클라이언트 상태: {self.http}")
                print(f"[Orchestrator] 🔍 세션 ID: {self.session_id}")
                
                # post_llm_request 호출
                result = await post_llm_request(self.http, llm_request_model)
                print(f"[Orchestrator] 📡 LLM 요청 전송 완료, 결과: {type(result)}")
                
                if result and isinstance(result, bytes):
                    print(f"[Orchestrator] ✅ LLM 응답 성공: {len(result)} bytes")
                    return result
                else:
                    print(f"[Orchestrator] ⚠️ LLM 응답이 비어있음: {type(result)}")
                    if attempt < max_retries - 1:
                        print(f"[Orchestrator] 🔄 {retry_delay}초 후 재시도...")
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        print(f"[Orchestrator] ❌ 최대 재시도 횟수 도달")
                        return None

            except httpx.ReadTimeout as e:
                print(f"[Orchestrator] ⏰ LLM 요청 타임아웃 (시도 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    print(f"[Orchestrator] 🔄 {retry_delay}초 후 재시도...")
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    print(f"[Orchestrator] ❌ 최대 재시도 횟수 도달")
                    self.errbuf.push(f"LLM 요청 최종 타임아웃: {e}")
                    return None
                    
            except Exception as e:
                print(f"[Orchestrator] ❌ LLM 요청 오류 (시도 {attempt + 1}/{max_retries}): {e}")
                import traceback
                print(f"[Orchestrator] 🔍 상세 오류: {traceback.format_exc()}")
                
                if attempt < max_retries - 1:
                    print(f"[Orchestrator] 🔄 {retry_delay}초 후 재시도...")
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    print(f"[Orchestrator] ❌ 최대 재시도 횟수 도달")
                    self.errbuf.push(f"LLM 호출 최종 오류: {e}")
                    return None
        
        return None
  
    # -------------------------
    # 파이프라인 루프
    # -------------------------


    async def _frame_processor_loop(self) -> None:
        """
        raw_frame_q → 인코딩 → frame_q
        """
        print("[Orchestrator] 🎞️ 프레임 프로세서 루프 시작")
        print(f"[Orchestrator] 📊 raw_frame_q 초기 상태: {self.data_pipeline.raw_frame_q.qsize()}/{self.data_pipeline.raw_frame_q.maxsize}")
        processed_frames = 0
        loop_count = 0
        
        try:
            while not self.stop_evt.is_set():
                loop_count += 1
                
                # 주기적으로 상태 출력 (100번마다)
                if loop_count % 100 == 0:
                    print(f"[Orchestrator] 🎞️ 프레임 프로세서 루프 실행 중... (루프 {loop_count}번)")
                    print(f"[Orchestrator] 📊 현재 상태 - raw_frame_q: {self.data_pipeline.raw_frame_q.qsize()}/8, frame_q: {self.data_pipeline.frame_q.qsize()}/8")
                
                try:
                    raw_frame_q_size = self.data_pipeline.raw_frame_q.qsize()
                    
                    # 큐가 비어있을 때는 짧게 대기
                    if raw_frame_q_size == 0:
                        if loop_count % 50 == 0:  # 50번마다 로그 출력
                            print(f"[Orchestrator] ⏳ raw_frame_q가 비어있음 (루프 {loop_count}번)")
                        await asyncio.sleep(0.01)  # 더 짧게 대기
                        continue
                    
                    # 큐가 가득 찰 때는 즉시 처리 (우선순위 높음)
                    if raw_frame_q_size >= self.data_pipeline.raw_frame_q.maxsize * 0.7:  # 70% 이상일 때
                        print(f"[Orchestrator] 🚨 raw_frame_q가 가득참 ({raw_frame_q_size}/8) - 즉시 처리 시작!")
                        
                        # 큐에 있는 모든 프레임을 빠르게 처리
                        batch_processed = 0
                        while not self.stop_evt.is_set() and self.data_pipeline.raw_frame_q.qsize() > 0:
                            try:
                                chunk = self.data_pipeline.raw_frame_q.get_nowait()
                                if chunk:
                                    # 프레임 인코딩 (비동기 함수 직접 호출)
                                    enc = await self.video_service.encode_frame(chunk)
                                    if enc:
                                        frame_data = enc  # encode_frame이 이미 bytes를 반환
                                        if frame_data:
                                            # frame_q에 추가
                                            success = self.data_pipeline.safe_put(self.data_pipeline.frame_q, frame_data)
                                            if success:
                                                print(f"[Orchestrator] ✅ 긴급 프레임 처리 완료: {len(frame_data)} bytes")
                                                processed_frames += 1
                                                batch_processed += 1
                                            else:
                                                print("[Orchestrator] ❌ frame_q 추가 실패!")
                                    else:
                                        print("[Orchestrator] ⚠️ 긴급 프레임 인코딩 실패")
                            except queue.Empty:
                                break
                            except Exception as e:
                                print(f"[Orchestrator] ❌ 긴급 프레임 처리 오류: {e}")
                                break
                        
                        print(f"[Orchestrator] 🎯 긴급 처리 완료 - {batch_processed}개 처리됨")
                        print(f"[Orchestrator] 📊 현재 상태 - raw_frame_q: {self.data_pipeline.raw_frame_q.qsize()}/8, frame_q: {self.data_pipeline.frame_q.qsize()}/8")
                        continue
                    
                    # 일반적인 프레임 처리 (큐에 데이터가 있을 때)
                    if raw_frame_q_size > 0:
                        # 한 번에 여러 프레임 처리 (효율성 향상)
                        frames_to_process = min(raw_frame_q_size, 3)  # 최대 3개씩 처리
                        
                        for _ in range(frames_to_process):
                            if self.stop_evt.is_set() or self.data_pipeline.raw_frame_q.qsize() == 0:
                                break
                                
                            try:
                                chunk = self.data_pipeline.raw_frame_q.get_nowait()
                                if chunk:
                                    print(f"[Orchestrator] 🎞️ 일반 프레임 처리: {chunk.width}x{chunk.height}")
                                    
                                    # 프레임 인코딩 (비동기 함수 직접 호출)
                                    enc = await self.video_service.encode_frame(chunk)
                                    if enc:
                                        frame_data = enc  # encode_frame이 이미 bytes를 반환
                                        if frame_data:
                                            # frame_q에 추가
                                            success = self.data_pipeline.safe_put(self.data_pipeline.frame_q, frame_data)
                                            if success:
                                                print(f"[Orchestrator] ✅ 일반 프레임 처리 완료: {len(frame_data)} bytes")
                                                processed_frames += 1
                                            else:
                                                print("[Orchestrator] ❌ frame_q 추가 실패!")
                                        else:
                                            print("[Orchestrator] ⚠️ 프레임 바이트 변환 실패")
                                    else:
                                        print("[Orchestrator] ⚠️ 프레임 인코딩 실패")
                            except queue.Empty:
                                break
                            except Exception as e:
                                print(f"[Orchestrator] ❌ 일반 프레임 처리 오류: {e}")
                    
                    # 처리 속도 조절 (CPU 부하 방지)
                    await asyncio.sleep(0.005)  # 더 빠른 반응
                    
                except Exception as e:
                    print(f"[Orchestrator] ❌ 프레임 처리 루프 오류: {e}")
                    await asyncio.sleep(0.05)
                    
        except asyncio.CancelledError:
            print("[Orchestrator] 🎞️ 프레임 프로세서 루프 취소됨")
        finally:
            print(f"[Orchestrator] 🎞️ 프레임 프로세서 루프 종료 (처리된 프레임: {processed_frames}개, 총 루프: {loop_count}번)")

    async def _frame_uploader_loop(self) -> None:
        """
        frame_q → post_frame → image_result_q (감정 결과 단건 적재)
        """
        print("[Orchestrator] 📤 프레임 업로더 루프 시작")
        print(f"[Orchestrator] 📊 frame_q 초기 상태: {self.data_pipeline.frame_q.qsize()}/{self.data_pipeline.frame_q.maxsize}")
        processed_frames = 0
        
        try:
            while not self.stop_evt.is_set():
                try:
                    frame_q_size = self.data_pipeline.frame_q.qsize()
                    
                    # 큐가 비어있을 때는 짧게 대기
                    if frame_q_size == 0:
                        await asyncio.sleep(0.01)  # 더 짧게 대기
                        continue
                    
                    # 큐에 데이터가 있으면 즉시 처리
                    if frame_q_size > 0:
                        # 한 번에 여러 프레임 처리 (효율성 향상)
                        frames_to_upload = min(frame_q_size, 2)  # 최대 2개씩 업로드
                        
                        for _ in range(frames_to_upload):
                            if self.stop_evt.is_set() or self.data_pipeline.frame_q.qsize() == 0:
                                break
                                
                            try:
                                chunk = self.data_pipeline.frame_q.get_nowait()
                                if chunk:
                                    print(f"[Orchestrator] 📤 프레임 업로드 시작: {len(chunk)} bytes")
                                    
                                    # HTTP 요청 전송
                                    response = await post_frame(self.http, self.session_id, chunk, self.metrics, self.errbuf)
                                    processed_frames += 1

                                    if response:
                                        print(f"[Orchestrator] ✅ 프레임 업로드 성공")
                                        emotion_data = self._extract_emotion_data(response)
                                        if emotion_data:
                                            print(f"[Orchestrator] 🎭 감정 데이터 추출 성공: {emotion_data}")
                                            self.data_pipeline.safe_put(self.data_pipeline.image_result_q, emotion_data)
                                        else:
                                            print("[Orchestrator] ⚠️ 감정 데이터 추출 실패")
                                    else:
                                        print("[Orchestrator] ❌ 프레임 업로드 실패")
                                
                            except queue.Empty:
                                break
                            except Exception as e:
                                print(f"[Orchestrator] ❌ 프레임 업로드 오류: {e}")
                                await asyncio.sleep(0.1)
                        
                        # 처리 속도 조절 (서버 부하 방지)
                        await asyncio.sleep(0.01)
                    
                except Exception as e:
                    print(f"[Orchestrator] ❌ 프레임 업로더 루프 오류: {e}")
                    await asyncio.sleep(0.1)
                    
        except asyncio.CancelledError:
            print("[Orchestrator] 📤 프레임 업로더 루프 취소됨")
        finally:
            print(f"[Orchestrator] 📤 프레임 업로더 루프 종료 (처리된 프레임: {processed_frames}개)")

    async def _emotion_aggregator_loop(self) -> None:
        """
        image_result_q → 배치 요약 → emotion_summary_q
        - 하이브리드 트리거: BATCH_MIN개 모이거나 FLUSH_SEC 경과 시 요약
        - BATCH_MAX 상한으로 메모리/지연 제어
        """
        print("[Orchestrator] 📊 감정 집계 루프 시작")
        last_flush = time.monotonic()
        batch: list = []

        try:
            while not self.stop_evt.is_set():
                try:
                    # 스냅샷 드레인
                    drained = self._drain(self.data_pipeline.image_result_q)
                    if drained:
                        batch.extend(drained)
                        if len(batch) > self.BATCH_MAX:
                            batch = batch[-self.BATCH_MAX:]

                    
                    now = time.monotonic()
                    count_trigger = len(batch) >= self.BATCH_MIN
                    time_trigger = (now - last_flush) >= self.FLUSH_SEC and len(batch) > 0

                    if count_trigger or time_trigger:

                        print(f"[Orchestrator] 📤 감정 집계 요약 생성: {len(batch)}개 프레임")
                        tmp_q = self._to_queue(batch)
                        summary = self.emotion_service.summarize_img_infer(tmp_q)
                        if summary:
                            self.data_pipeline.safe_put(self.data_pipeline.emotion_summary_q, summary)
                            print(f"[Orchestrator] ✅ 감정 요약 완료: {summary.get('label', 'N/A')}")
    
                        batch.clear()
                        last_flush = now

                    await asyncio.sleep(self.AGG_INTERVAL_SEC)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.errbuf.push(f"감정 집계 오류: {e}")
                    await asyncio.sleep(0.2)
        finally:
            # 루프 종료 훅
            pass

    async def _heartbeat_loop(self) -> None:
        """
        간헐적 상태 체크(필요시 메트릭 전송). 과도한 출력/작업 지양.
        """
        try:
            while not self.stop_evt.is_set():
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    break
        finally:
            pass

    # -------------------------
    # 종료 시 처리
    # -------------------------
    async def _process_remaining_emotions(self) -> None:
        """
        종료 직전 남은 image_result_q를 마지막 1회 요약하여 emotion_summary_q에 적재
        """
        try:
            remaining = self.data_pipeline.image_result_q.qsize()
            if remaining > 0:
                
                summary = self.emotion_service.summarize_img_infer(self.data_pipeline.image_result_q)
                if summary:
                    self.data_pipeline.safe_put(self.data_pipeline.emotion_summary_q, summary)
                    print(f"[DEBUG] ✅ 최종 요약 완료: {summary['label']} (점수: {summary['score']:.3f})")
        except Exception as e:
            self.errbuf.push(f"최종 감정 요약 처리 오류: {e}")

    async def _process_audio_finalization(self) -> None:
        """
        PyAudio 우선 처리: WAV 생성/업로드 → 전사 텍스트 추출 → transcript_q 적재 → 콜백으로 LLM 요청 실행
        """
        try:
            print("[Orchestrator] 🎤 PyAudio 우선 오디오 처리 시작")
            
            # PyAudio 데이터 우선 처리
            has_dual_audio = hasattr(self, 'dual_audio_service') and self.dual_audio_service
            print(f"[Orchestrator] 🔍 dual_audio_service 존재: {has_dual_audio}")
            
            if has_dual_audio:
                print(f"[Orchestrator] 🔍 dual_audio_service 객체: {self.dual_audio_service}")
                print(f"[Orchestrator] 🔍 PyAudio 활성 상태: {getattr(self.dual_audio_service, 'is_pyaudio_active', False)}")
                
                pyaudio_wav_bytes = await asyncio.to_thread(
                    self.dual_audio_service.build_pyaudio_wav_bytes
                )
                if pyaudio_wav_bytes:
                    print(f"[Orchestrator] 🎤 PyAudio WAV 생성 완료: {len(pyaudio_wav_bytes)} bytes")
                    
                    # PyAudio 우선 업로드
                    response = await self.audio_service.upload_audio(
                        self.session_id, pyaudio_wav_bytes, self.errbuf
                    )

                    transcript = self.audio_service.extract_transcript(response)
                    if transcript:
                        self.emotion_service.add_audio_emotion(transcript)
                        self.data_pipeline.safe_put(self.data_pipeline.transcript_q, transcript)
                        print(f"[Orchestrator] 🎤 PyAudio 전사 완료: {transcript[:50]}...")
                        
                        # 🚀 콜백으로 LLM 요청 실행!
                        if hasattr(self, 'on_audio_finalization_complete') and self.on_audio_finalization_complete:
                            print("[Orchestrator] 🚀 PyAudio 처리 완료 후 콜백으로 LLM 요청 실행!")
                            await self.on_audio_finalization_complete()
                        else:
                            print("[Orchestrator] ⚠️ on_audio_finalization_complete 콜백이 설정되지 않음")
                        
                        return  # PyAudio 처리 완료
                else:
                    print("[Orchestrator] ⚠️ PyAudio WAV 데이터가 없어서 WebRTC로 fallback")
            else:
                print("[Orchestrator] ⚠️ dual_audio_service가 없어서 WebRTC로 fallback")
                
            # PyAudio가 없으면 WebRTC 오디오 처리
            wav_bytes = await asyncio.to_thread(self.audio_accumulator.build_wav_bytes)
            if not wav_bytes:
                print("[Orchestrator] ⚠️ 오디오 데이터가 없습니다")
                return
                
            print(f"[Orchestrator] 🎵 WebRTC 오디오 WAV 생성: {len(wav_bytes)} bytes")
            response = await self.audio_service.upload_audio(self.session_id, wav_bytes, self.errbuf)
            transcript = self.audio_service.extract_transcript(response)
            if transcript:
                self.emotion_service.add_audio_emotion(transcript)
                self.data_pipeline.safe_put(self.data_pipeline.transcript_q, transcript)
                print(f"[Orchestrator] 🎵 WebRTC 전사 완료: {transcript[:50]}...")
                
                # 🚀 콜백으로 LLM 요청 실행!
                if hasattr(self, 'on_audio_finalization_complete') and self.on_audio_finalization_complete:
                    print("[Orchestrator] 🚀 WebRTC 처리 완료 후 콜백으로 LLM 요청 실행!")
                    await self.on_audio_finalization_complete()
                else:
                    print("[Orchestrator] ⚠️ on_audio_finalization_complete 콜백이 설정되지 않음")
                
        except Exception as e:
            self.errbuf.push(f"오디오 최종 처리 오류: {e}")
            print(f"[Orchestrator] ❌ 오디오 최종 처리 오류: {e}")

    def _finalize_check_segment_log(self) -> None:
        """
        세그먼트 종료 시 상세한 결과 로그 출력 (세션 종료 메시지 없음)
        """
        print("\n" + "="*60)
        
        # 데이터 파이프라인 상태
        pipeline_status = self.data_pipeline.get_queue_status()
        print(f"📊 **데이터 파이프라인 상태:**")
        print(f"  - raw_frame_q: {pipeline_status.get('raw_frame_q', 0)}개")
        print(f"  - frame_q: {pipeline_status.get('frame_q', 0)}개")
        print(f"  - image_result_q: {pipeline_status.get('image_result_q', 0)}개")
        print(f"  - transcript_q: {pipeline_status.get('transcript_q', 0)}개")
        print(f"  - summary_q: {pipeline_status.get('summary_q', 0)}개")
        
        # 감정 분석 결과
        emotion_summary = self.emotion_service.get_final_summary()
        if emotion_summary:
            print(f"\n🎭 **감정 분석 결과:**")
            if isinstance(emotion_summary, dict):
                for emotion, value in emotion_summary.items():
                    if isinstance(value, (int, float)):
                        print(f"  - {emotion}: {value:.2f}")
                    else:
                        print(f"  - {emotion}: {value}")
            else:
                print(f"  - 요약: {emotion_summary}")
        
        # 메트릭스 정보
        if self.metrics:
            metrics_data = self.metrics.get_stats()
            if metrics_data:
                print(f"\n📈 **메트릭스:**")
                for key, value in metrics_data.items():
                    print(f"  - {key}: {value}")
        
        # 에러 정보
        if self.errbuf and self.errbuf.has_errors():
            print(f"\n❌ **에러 로그:**")
            errors = self.errbuf.get_errors()
            for error in errors[-5:]:  # 최근 5개 에러만 표시
                print(f"  - {error}")
        
            print("="*60)

        # Streamlit UI 출력은 app.py에서 처리하므로 여기서는 제거
        # (중복 방지 및 책임 분리)

    def _extract_emotion_data(self, response) -> Optional[dict]:
        try:
            if hasattr(response, "json"):
                data = response.json()
                if isinstance(data, dict) and "data" in data:
                    emo = data["data"]
                    if isinstance(emo, dict) and any(
                        k in emo for k in ["happy", "sad", "angry", "fear", "disgust", "surprise", "neutral"]
                    ):
                        return emo
                return data
        except Exception as e:
            self.errbuf.push(f"감정 데이터 추출 오류: {e}")
        return None

    def get_status(self) -> dict:
        return {
            "stop_event_set": self.stop_evt.is_set(),
            "active_tasks": len(self.tasks),
            "pipeline_status": self.data_pipeline.get_queue_status(),
            "emotion_service_stats": getattr(self.emotion_service, "get_stats", lambda: {})(),
        }

    # -------------------------
    # 유틸
    # -------------------------
    @staticmethod
    def _drain(qobj: queue.Queue):
        items = []
        get = qobj.get_nowait
        while True:
            try:
                items.append(get())
            except queue.Empty:
                break
        return items

    @staticmethod
    def _to_queue(items: list) -> queue.Queue:
        qobj = queue.Queue(maxsize=len(items) or 1)
        for it in items:
            try:
                qobj.put_nowait(it)
            except queue.Full:
                break
        return qobj

    async def _memory_cleanup_loop(self) -> None:
        """주기적으로 메모리 정리 수행"""
        print("[Orchestrator] 🧹 메모리 정리 루프 시작")
        
        try:
            while not self.stop_evt.is_set():
                try:
                    await asyncio.sleep(60) # 정리 간격 60초로 변경
                    
                    # 프레임 큐 크기 제한
                    if self.data_pipeline.frame_q.qsize() > 100: # 최대 프레임 큐 크기 100으로 변경
                        excess = self.data_pipeline.frame_q.qsize() - 100
                        print(f"[Orchestrator] 🧹 프레임 큐 정리: {excess}개 제거")
                        for _ in range(excess):
                            try:
                                self.data_pipeline.frame_q.get_nowait()
                            except queue.Empty:
                                break
                    
                    # 오디오 큐 크기 제한
                    if self.audio_q.qsize() > 50: # 최대 오디오 큐 크기 50으로 변경
                        excess = self.audio_q.qsize() - 50
                        print(f"[Orchestrator] 🧹 오디오 큐 정리: {excess}개 제거")
                        for _ in range(excess):
                            try:
                                self.audio_q.get_nowait()
                            except queue.Empty:
                                break
                    
                    # 활성 태스크 상태 출력
                    print(f"[Orchestrator] �� 활성 태스크: {len(self.tasks)}/{6}") # 최대 동시 태스크 수 6으로 변경
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[Orchestrator] ⚠️ 메모리 정리 오류: {e}")
                    await asyncio.sleep(10)  # 오류 시 10초 대기
                    
        finally:
            print("[Orchestrator] 🧹 메모리 정리 루프 종료")