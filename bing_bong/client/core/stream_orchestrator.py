# stream_orchestrator.py
import asyncio
import time
import queue
from typing import Optional, List
from infrastructure.metrics import Metrics, ErrorBuf
from infrastructure.http_client import post_frame
from services.emotion_service import EmotionService
from services.video_service import VideoService
from services.audio_service import AudioService
from core.data_pipeline import DataPipeline


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

        self.stop_evt = asyncio.Event()
        self.tasks: List[asyncio.Task] = []

        # 재진입/중복 실행 방지 플래그
        self._started: bool = False
        self._stopping: bool = False

    # -------------------------
    # 라이프사이클
    # -------------------------
    async def start(self) -> None:
        if self._started and not self._stopping:
            return  # 이미 실행 중

        self._stopping = False
        self._started = True
        self.stop_evt.clear()

        self.tasks = [
            asyncio.create_task(self._heartbeat_loop(), name="hb"),
            asyncio.create_task(self._frame_uploader_loop(), name="uploader"),
            asyncio.create_task(self._emotion_aggregator_loop(), name="agg"),
            asyncio.create_task(self._frame_processor_loop(), name="encoder"),
        ]

    async def stop(self) -> None:
        if self._stopping:
            return  # 중복 stop 방지

        self._stopping = True
        self.stop_evt.set()

        # 태스크 취소 후 종료 대기
        for t in self.tasks:
            if not t.done():
                t.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
            self.tasks.clear()

        # 남은 감정 데이터 최종 처리(영상)
        await self._process_remaining_emotions()

        # 오디오 최종 처리
        await self._process_audio_finalization()

        # 최종 결과 요약/상태 노출
        self._finalize_check_segment_log()

        self._started = False
        self._stopping = False

    def reset(self) -> None:
        # 실행 중 reset 호출을 막거나 경고하는 게 안전
        if self._started and not self._stopping:
            return
        self.stop_evt.clear()
        self.tasks.clear()
    
    async def call_llm(self) -> None:
        try:
            if self._stopping:
                await self.stop()

            # todo 

        except Exception as e:
            self.errbuf.push(f"LLM 호출 오류: {e}")
            return None
  
    # -------------------------
    # 파이프라인 루프
    # -------------------------
    async def _frame_processor_loop(self) -> None:
        """
        raw_frame_q → (OpenCV JPEG 인코딩) → frame_q
        내부에서 get 호출은 to_thread로 돌려 GIL 점유 최소화
        """
        loop = asyncio.get_running_loop()
        try:
            while not self.stop_evt.is_set():
                try:
                    if loop.is_closed() or self.stop_evt.is_set():
                        break
                    raw = await asyncio.to_thread(self.data_pipeline.raw_frame_q.get, True, 0.1)
                    enc = await self.video_service.encode_frame(raw)
                    if enc:
                        # EncodedFrame이든 bytes든 현재 설계에 맞추어 넣기
                        self.data_pipeline.safe_put(self.data_pipeline.frame_q, enc if isinstance(enc, bytes) else enc.jpeg)
                except queue.Empty:
                    continue
                except (asyncio.CancelledError, RuntimeError):
                    break
                except Exception as e:
                    self.errbuf.push(f"프레임 파이프라인 처리 오류: {e}")
                    await asyncio.sleep(0.05)
        finally:
            # 루프 종료 훅
            pass

    async def _frame_uploader_loop(self) -> None:
        """
        frame_q → post_frame → image_result_q (감정 결과 단건 적재)
        """
        processed_frames = 0
        loop = asyncio.get_running_loop()
        try:
            while not self.stop_evt.is_set():
                try:
                    if self.data_pipeline.frame_q.empty():
                        await asyncio.sleep(0.1)
                        continue

                    if loop.is_closed() or self.stop_evt.is_set():
                        break
                    chunk = await asyncio.to_thread(self.data_pipeline.frame_q.get, True, 0.3)
                    response = await post_frame(self.http, self.session_id, chunk, self.metrics, self.errbuf)
                    processed_frames += 1

                    if response:
                        emotion_data = self._extract_emotion_data(response)
                        if emotion_data:
                            self.data_pipeline.safe_put(self.data_pipeline.image_result_q, emotion_data)
                except queue.Empty:
                    continue
                except (asyncio.CancelledError, RuntimeError):
                    break
                except Exception as e:
                    self.errbuf.push(f"프레임 업로드 오류: {e}")
                    await asyncio.sleep(0.1)
        finally:
            # 필요 시 processed_frames 지표만 기록
            pass

    async def _emotion_aggregator_loop(self) -> None:
        """
        image_result_q → 배치 요약 → emotion_summary_q
        - 하이브리드 트리거: BATCH_MIN개 모이거나 FLUSH_SEC 경과 시 요약
        - BATCH_MAX 상한으로 메모리/지연 제어
        """
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
                        # 기존 서비스 시그니처 유지: 큐를 넘기면 내부에서 비우지만,
                        # 여기서는 리스트 기반 summarize가 이상적이므로 가능시 변경 권장.
                        # 현재는 호환 위해 임시 큐로 포장 후 호출.
                        tmp_q = self._to_queue(batch)
                        summary = self.emotion_service.summarize_img_infer(tmp_q)
                        if summary:
                            self.data_pipeline.safe_put(self.data_pipeline.emotion_summary_q, summary)
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
        except Exception as e:
            self.errbuf.push(f"최종 감정 요약 처리 오류: {e}")

    async def _process_audio_finalization(self) -> None:
        """
        오디오 WAV 생성/업로드 → 전사 텍스트 추출 → transcript_q 적재
        """
        try:
            wav_bytes = await asyncio.to_thread(self.audio_accumulator.build_wav_bytes)
            if not wav_bytes:
                return
            response = await self.audio_service.upload_audio(self.session_id, wav_bytes, self.errbuf)
            transcript = self.audio_service.extract_transcript(response)
            if transcript:
                self.emotion_service.add_audio_emotion(transcript)
                self.data_pipeline.safe_put(self.data_pipeline.transcript_q, transcript)
        except Exception as e:
            self.errbuf.push(f"오디오 최종 처리 오류: {e}")

    def _finalize_check_segment_log(self) -> None:
        """
        세그먼트 종료 상태(요약/전사/큐 크기)에 대한 최소 요약.
        상세 출력/로깅은 외부 로거/메트릭으로 위임 권장.
        """
        # 의존 코드와의 호환을 위해 최소한의 상태만 노출
        _ = self.data_pipeline.get_queue_status()
        _ = self.emotion_service.get_final_summary()
        # 필요 시 metrics에 기록만 남기고 콘솔 출력은 생략

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