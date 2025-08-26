# http_client.py
import httpx
import streamlit as st
import time
from .config import API_BASE
from .dto.llm_request_model import LlmRequestModel

## fastapi로 호출하는 client
@st.cache_resource
def get_http() -> httpx.AsyncClient:
    # 재사용 가능한 AsyncClient (타임아웃 단축)
    return httpx.AsyncClient(
        base_url=API_BASE, 
        timeout=5.0,  # 5초로 단축
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)
    )

# 편의 함수
async def post_json(http: httpx.AsyncClient, path: str, payload: dict, timeout: float = 5.0):
    try:
        # API_BASE와 path를 결합하여 전체 URL 생성
        full_url = f"{API_BASE}{path}"
        print(f"[HTTP] 📤 POST 요청 전송: {full_url}")
        print(f"[HTTP] 📦 페이로드 크기: {len(str(payload))} chars")
        print(f"[HTTP] ⏱️ 타임아웃: {timeout}s")
        
        r = await http.post(full_url, json=payload, timeout=timeout)
        print(f"[HTTP] 📥 응답 수신: {r.status_code}")
        
        r.raise_for_status()
        print(f"[HTTP] ✅ HTTP 요청 성공: {r.status_code}")
        return r
        
    except Exception as e:
        print(f"[HTTP] ❌ POST 요청 실패: {full_url} - {e}")
        raise

async def post_json_with_file(http: httpx.AsyncClient, path: str, payload: dict, timeout: float = 5.0):
    try:
        # API_BASE와 path를 결합하여 전체 URL 생성
        full_url = f"{API_BASE}{path}"
        print(f"[HTTP] 📤 POST 요청 전송: {full_url}")
        print(f"[HTTP] 📦 페이로드 크기: {len(str(payload))} chars")
        print(f"[HTTP] ⏱️ 타임아웃: {timeout}s")
        
        r = await http.post(full_url, json=payload, timeout=timeout)
        print(f"[HTTP] 📥 응답 수신: {r.status_code}")
        
        r.raise_for_status()
        print(f"[HTTP] ✅ HTTP 요청 성공: {r.status_code}")
        return r
        
    except Exception as e:
        print(f"[HTTP] ❌ POST 요청 실패: {full_url} - {e}")
        raise

async def post_multipart(http: httpx.AsyncClient, path: str, files: dict, data: dict, timeout: float = 10.0):
    try:
        # API_BASE와 path를 결합하여 전체 URL 생성
        full_url = f"{API_BASE}{path}"
        print(f"[HTTP] 📤 Multipart 요청 전송: {full_url}")
        
        r = await http.post(full_url, files=files, data=data, timeout=timeout)
        r.raise_for_status()
        return r
        
    except Exception as e:
        print(f"[HTTP] ❌ Multipart 요청 실패: {full_url} - {e}")
        raise

async def post_frame(http: httpx.AsyncClient, session_id: str, frame_data: bytes, metrics, errbuf, timeout: float = 30.0):
    """비디오 프레임을 서버에 업로드합니다."""
    try:
        print(f"[HTTP] 📸 프레임 업로드 시작: {len(frame_data)} bytes, session_id={session_id}")
        
        files = {"file": ("frame.jpg", frame_data, "image/jpeg")}
        data = {"session_id": session_id, "ts": str(time.time())}
        
        # 서버 연결 상태 확인
        try:
            r = await post_multipart(http, "/ingest/frame", files=files, data=data, timeout=timeout)
            print(f"[HTTP] ✅ 프레임 업로드 성공: {r.status_code}")
            metrics.inc_deq()
            return r
        except httpx.ConnectError as e:
            print(f"[HTTP] ❌ 서버 연결 실패: {e}")
            errbuf.push(f"/ingest/frame 연결 실패: {e}")
            return None
        except httpx.TimeoutException as e:
            print(f"[HTTP] ⏰ 프레임 업로드 타임아웃: {e}")
            errbuf.push(f"/ingest/frame 타임아웃: {e}")
            return None
        except httpx.HTTPStatusError as e:
            print(f"[HTTP] ❌ HTTP 오류: {e.response.status_code} - {e}")
            errbuf.push(f"/ingest/frame HTTP 오류: {e.response.status_code}")
            return None
            
    except Exception as e:
        print(f"[HTTP] ❌ 프레임 업로드 예외: {e}")
        errbuf.push(f"/ingest/frame 실패: {e}")
        return None

async def post_audio(http: httpx.AsyncClient, session_id: str, wav_data: bytes, metrics, errbuf, timeout: float = 120.0):
    """오디오 데이터를 서버에 업로드합니다."""
    try:
        print(f"[HTTP] 🎵 오디오 업로드 시작: {len(wav_data)} bytes, session_id={session_id}")
        
        files = {"file": ("audio.wav", wav_data, "audio/wav")}
        data = {"session_id": session_id, "ts": str(time.time())}
        
        # 서버 연결 상태 확인
        try:
            r = await post_multipart(http, "/ingest/audio", files=files, data=data, timeout=timeout)
            print(f"[HTTP] ✅ 오디오 업로드 성공: {r.status_code}")
            return r
        except httpx.ConnectError as e:
            print(f"[HTTP] ❌ 서버 연결 실패: {e}")
            errbuf.push(f"/ingest/audio 연결 실패: {e}")
            return None
        except httpx.TimeoutException as e:
            print(f"[HTTP] ⏰ 오디오 업로드 타임아웃: {e}")
            errbuf.push(f"/ingest/audio 타임아웃: {e}")
            return None
        except httpx.HTTPStatusError as e:
            print(f"[HTTP] ❌ HTTP 오류: {e.response.status_code} - {e}")
            errbuf.push(f"/ingest/audio HTTP 오류: {e.response.status_code}")
            return None
            
    except Exception as e:
        print(f"[HTTP] ❌ 오디오 업로드 예외: {e}")
        errbuf.push(f"/ingest/audio 실패: {e}")
        return None

async def post_llm_request(http: httpx.AsyncClient, model: LlmRequestModel, timeout: float = 20.0) -> bytes:
    """LLM 요청을 전송합니다."""
    try:
        print(f"[HTTP] 🧠 LLM 요청 시작: {model.to_dict()}")
        
        r = await post_json_with_file(http, "/llm/request", model.to_dict(), timeout=timeout)
        r.raise_for_status()
        
        data = r.json()
        
        # base64로 인코딩된 오디오 데이터를 디코딩
        if data.get("ok") and data.get("result"):
            import base64
            try:
                audio_bytes = base64.b64decode(data["result"])
                print(f"[HTTP] 🎵 LLM 오디오 응답 수신: {len(audio_bytes)} bytes")
                return audio_bytes
            except Exception as e:
                print(f"[HTTP] ❌ base64 디코딩 오류: {e}")
                return b""
        else:
            print(f"[HTTP] ⚠️ LLM 응답 오류: {data.get('error', 'Unknown error')}")
            return b""
            
    except httpx.ConnectError as e:
        print(f"[HTTP] ❌ LLM 요청 서버 연결 실패: {e}")
        return b""
    except httpx.TimeoutException as e:
        print(f"[HTTP] ⏰ LLM 요청 타임아웃: {e}")
        return b""
    except httpx.HTTPStatusError as e:
        print(f"[HTTP] ❌ LLM 요청 HTTP 오류: {e.response.status_code} - {e}")
        return b""
    except Exception as e:
        print(f"[HTTP] ❌ LLM 요청 예외: {e}")
        return b""

