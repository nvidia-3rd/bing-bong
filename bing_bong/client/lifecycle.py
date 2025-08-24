# lifecycle.py
import time
import asyncio
import queue
from typing import List

from http_client import post_json, post_multipart
from config import HEARTBEAT_SEC
from metrics import Metrics, ErrorBuf
from util import calculate

async def heartbeat_loop(
    http,
    session_id: str,
    stop_evt: asyncio.Event,
    errbuf: ErrorBuf,
    interval: float = HEARTBEAT_SEC,
) -> None:
    while not stop_evt.is_set():
        try:
            await post_json(http, "/sessions/heartbeat", {"session_id": session_id, "ts": time.time()})
        except Exception as e:
            errbuf.push(f"heartbeat 실패: {e}")
        try:
            await asyncio.wait_for(stop_evt.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def post_frame(
    http,
    session_id: str,
    jpeg_bytes: bytes,
    metrics: Metrics,
    errbuf: ErrorBuf,
) -> None:
    files = {"file": ("frame.jpg", jpeg_bytes, "image/jpeg")}
    data = {"session_id": session_id, "ts": str(time.time())}
    try:
        await post_multipart(http, "/ingest/frame", files=files, data=data, timeout=30)
        metrics.inc_deq()
    except Exception as e:
        errbuf.push(f"/ingest/frame 실패: {e}")


async def uploader_loop(
    http,
    session_id: str,
    q: "queue.Queue[bytes]",
    stop_evt: asyncio.Event,
    metrics: Metrics,
    errbuf: ErrorBuf,
) -> None:

    while not stop_evt.is_set():
        try:
            chunk = await asyncio.to_thread(q.get, True, 0.3)  # block=True, timeout=0.3
        except queue.Empty:
            continue
        
        await post_frame(http, session_id, chunk, metrics, errbuf)


async def start_session(
    http,
    session_id: str,
    stop_evt: asyncio.Event,
    bg_tasks: List[asyncio.Task],
    q: "queue.Queue[bytes]",
    metrics: Metrics,
    errbuf: ErrorBuf,
) -> None:
    # 중복 시작 방지(선택)
    if any(t for t in bg_tasks if not t.done()):
        return

    meta = {"note": "streamlit-proxy"}  # 필요 시 UA 등 추가
    try:
        await post_json(http, "/sessions/start", {"session_id": session_id, "meta": meta, "ts": time.time()})
    except Exception as e:
        errbuf.push(f"/sessions/start 실패: {e}")

    stop_evt.clear()
    bg_tasks.append(asyncio.create_task(heartbeat_loop(http, session_id, stop_evt, errbuf)))
    bg_tasks.append(asyncio.create_task(uploader_loop(http, session_id, q, stop_evt, metrics, errbuf)))


async def stop_session(
    http,
    session_id: str,
    stop_evt: asyncio.Event,
    bg_tasks: List[asyncio.Task],
    errbuf: ErrorBuf,
) -> None:
    stop_evt.set()

    # 태스크 정리: cancel 후 안전 수거
    for t in list(bg_tasks):
        t.cancel()
    if bg_tasks:
        await asyncio.gather(*bg_tasks, return_exceptions=True)
    bg_tasks.clear()

    try:
        await post_json(http, "/sessions/stop", {"session_id": session_id, "ts": time.time()})
    except Exception as e:
        errbuf.push(f"/sessions/stop 실패: {e}")


async def post_audio(
    http,
    session_id: str,
    wav_bytes: bytes,
    errbuf: ErrorBuf,
) -> None:
    files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
    data = {"session_id": session_id, "ts": str(time.time())}
    try:
        await post_multipart(http, "/ingest/audio", files=files, data=data, timeout=120)
    except Exception as e:
        errbuf.push(f"/ingest/audio 실패: {e}")




