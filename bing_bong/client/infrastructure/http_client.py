# http_client.py
import httpx
import streamlit as st
import time
from .config import API_BASE
from .dto.llm_request_model import LlmRequestModel

## fastapi로 호출하는 client
@st.cache_resource
def get_http() -> httpx.AsyncClient:
    # 재사용 가능한 AsyncClient
    return httpx.AsyncClient(base_url=API_BASE, timeout=10)

# 편의 함수
async def post_json(http: httpx.AsyncClient, path: str, payload: dict, timeout: float = 10.0):
    r = await http.post(path, json=payload, timeout=timeout)
    r.raise_for_status()
    return r

async def post_multipart(http: httpx.AsyncClient, path: str, files: dict, data: dict, timeout: float = 30.0):
    r = await http.post(path, files=files, data=data, timeout=timeout)
    r.raise_for_status()
    return r

async def post_frame(http: httpx.AsyncClient, session_id: str, frame_data: bytes, metrics, errbuf, timeout: float = 30.0):
    """비디오 프레임을 서버에 업로드합니다."""
    try:
        files = {"file": ("frame.jpg", frame_data, "image/jpeg")}
        data = {"session_id": session_id, "ts": str(time.time())}
        r = await post_multipart(http, "/ingest/frame", files=files, data=data, timeout=timeout)
        metrics.inc_deq()
        return r
    except Exception as e:
        errbuf.push(f"/ingest/frame 실패: {e}")
        return None

async def post_audio(http: httpx.AsyncClient, session_id: str, wav_data: bytes, metrics, errbuf, timeout: float = 120.0):
    """오디오 데이터를 서버에 업로드합니다."""
    try:
        files = {"file": ("audio.wav", wav_data, "audio/wav")}
        data = {"session_id": session_id, "ts": str(time.time())}
        r = await post_multipart(http, "/ingest/audio", files=files, data=data, timeout=timeout)
        return r
    except Exception as e:
        errbuf.push(f"/ingest/audio 실패: {e}")
        return None

async def post_llm_request(http: httpx.AsyncClient, model: LlmRequestModel, timeout: float = 10.0):
    """LLM 요청을 전송합니다."""
    
    r = await post_json(http, "/llm/request", model.to_dict(), timeout=timeout)
    r.raise_for_status()

    return r

