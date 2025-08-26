from fastapi import FastAPI, UploadFile, File, Form, Request
from pydantic import BaseModel
from typing import Optional, List
import time
from model.dto.WebRTCImageResponse import WebRTCImageResponse
from util.convert_things import ConvertThings
from service.video.video_service import VideoService
from service.audio.audio_service import AudioService
from service.llm.llm_service import LlmService

app = FastAPI()
video_service = VideoService()
audio_service = AudioService()
llm_service = LlmService()


class LlmRequestModel(BaseModel):
    audio_text: str
    emotion_summary: List[dict]
    session_id: str  # 세션 ID는 여전히 필요 (로깅용)

# 사진 처리 api
@app.post("/ingest/frame")
async def ingest_frame(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    ts: str = Form(...)
):
    try:
        content = await file.read()
        print(f"[Frame] 📸 프레임 수신: {len(content)} bytes, session_id={session_id}")
        
        # 이미지 변환
        img_bgr = ConvertThings.bytes_to_bgr(content)
        print(f"[Frame] ✅ 이미지 변환 완료: {img_bgr.shape}")
        
        # 비동기 추론 실행
        result = await video_service.to_inference_by_frame(img_bgr, session_id)
        print(f"[Frame] 🎭 감정 분석 완료: {result}")
        print(f"[Frame] 🏆 가장 강한 감정: {max(result, key=result.get)}")
        
        return WebRTCImageResponse(
            success=True,
            message="success",
            data=result,
            timestamp=time.time(),
            session_id=session_id
        )
        
    except Exception as e:
        print(f"[Frame] ❌ 프레임 처리 오류: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"프레임 처리 실패: {str(e)}")


@app.post("/ingest/audio")
async def ingest_audio(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    ts: str = Form(...),
):
    try:
        content = await file.read()
        print(f"[Audio] 🎵 오디오 수신: {len(content)} bytes, session_id={session_id}")
        print(f"[Audio] 📁 파일 타입: {file.content_type}")
        
        # 오디오 분석 실행 (동기 함수를 비동기 컨텍스트에서 실행)
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, audio_service.get_audio_analysis, content)
        print(f"[Audio] 🎤 오디오 분석 완료: {result}")
        
        # OpenAI 응답 객체에서 전사 결과 추출
        transcript_text = None
        try:
            transcript_text = getattr(result, "text", None)
            if transcript_text:
                print(f"[Audio] 📝 전사 결과: {transcript_text[:100]}...")
            else:
                print("[Audio] ⚠️ 전사 결과가 없습니다")
        except Exception as e:
            print(f"[Audio] ❌ 전사 결과 추출 실패: {e}")
            transcript_text = None
        
        return {
            "ok": True, 
            "bytes": len(content), 
            "transcript": transcript_text,
            "session_id": session_id
        }
        
    except Exception as e:
        print(f"[Audio] ❌ 오디오 처리 오류: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"오디오 처리 실패: {str(e)}")


@app.post("/llm/request")
async def llm_request(req: LlmRequestModel):
    try:
        print(f"[LLM] 🚀 LLM 요청 시작:")
        print(f"[LLM] 📝 오디오 텍스트: {req.audio_text[:100]}...")
        print(f"[LLM] 🎭 감정 요약: {len(req.emotion_summary)}개 항목")
        print(f"[LLM] 🆔 세션 ID: {req.session_id}")
        
        # LLM 서비스 호출 (동기 함수를 비동기 컨텍스트에서 실행)
        import asyncio
        loop = asyncio.get_event_loop()
        call_to_llm_result = await loop.run_in_executor(
            None, 
            llm_service.call_to_llm, 
            req.audio_text, 
            req.emotion_summary
        )
        
        # 결과가 bytes인지 확인
        if isinstance(call_to_llm_result, bytes):
            print(f"[LLM] ✅ LLM 처리 완료: {len(call_to_llm_result)} bytes 반환")
            
            # bytes를 base64로 인코딩하여 JSON 응답 가능하게 함
            import base64
            audio_base64 = base64.b64encode(call_to_llm_result).decode('utf-8')
            
            return {
                "ok": True, 
                "result": audio_base64,
                "audio_size_bytes": len(call_to_llm_result),
                "encoding": "base64",
                "session_id": req.session_id
            }
        else:
            print(f"[LLM] ⚠️ LLM 결과가 bytes가 아님: {type(call_to_llm_result)}")
            return {"ok": False, "error": "Invalid result type", "result": ""}
            
    except Exception as e:
        print(f"[LLM] ❌ LLM 요청 처리 오류: {e}")
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": str(e), "result": ""}