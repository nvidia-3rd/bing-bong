from fastapi import FastAPI, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
import time
from model.dto.WebRTCImageResponse import WebRTCImageResponse
from util.convert_things import ConvertThings
from service.video.video_service import VideoService

app = FastAPI()
SESSIONS = {}  # 데모용 인메모리. 실제론 Redis/DB 권장.
video_service = VideoService()

class StartReq(BaseModel):
    session_id: str
    meta: Optional[dict] = None
    ts: float

class HeartbeatReq(BaseModel):
    session_id: str
    ts: float

class StopReq(BaseModel):
    session_id: str
    ts: float

class DataReq(BaseModel):
    session_id: str
    label: str
    data: str
    ts: float

@app.post("/sessions/start")
async def start(req: StartReq):
    SESSIONS[req.session_id] = {"started_at": req.ts, "meta": req.meta, "last_hb": req.ts, "frames": 0, "data_msgs": 0}
    return {"ok": True}

@app.post("/sessions/heartbeat")
async def heartbeat(req: HeartbeatReq):
    s = SESSIONS.get(req.session_id)
    if s:
        s["last_hb"] = req.ts
    return {"ok": True}

@app.post("/sessions/stop")
async def stop(req: StopReq):
    s = SESSIONS.pop(req.session_id, None)
    return {"ok": True, "summary": s or {}}

# 사진 처리 api
@app.post("/ingest/frame")
async def ingest_frame(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    ts: str = Form(...)
):
    
    content = await file.read()

    try:
        img_bgr = ConvertThings.bytes_to_bgr(content)
    except Exception:
        raise Exception(400, "invalid image")

    s = SESSIONS.get(session_id)
      
    if not s:
        s = SESSIONS.setdefault(session_id, {"started_at": float(ts), "frames": 0, "data_msgs": 0})

    s["frames"] += 1
    s["last_frame_ts"] = time.time()

    # 동기 추론을 비동기 컨텍스트에서 실행(서비스 래퍼 사용)
    result = await video_service.to_inference_by_frame(img_bgr, session_id)
    print(f"가장 강한 감정은: {max(result, key=result.get)}")
 
    return WebRTCImageResponse(
        success=True,
        message="success",
        data=result,
        timestamp=time.time(),
        session_id=session_id
    )


@app.post("/ingest/audio")
async def ingest_audio(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    ts: str = Form(...),
):
    content = await file.read()
    print(f"{session_id} fastapi audio bytes={len(content)}")
    print(f"{session_id} fastapi audio type = {type(file)}")
    s = SESSIONS.get(session_id)
    if s:
        s["audio_bytes"] = s.get("audio_bytes", 0) + len(content)
    # TODO: 여기서 파일 저장/ASR 큐 전송 등 처리
    return {"ok": True, "bytes": len(content)}