"""
WebRTC Face Detection Server - Main Application
"""
from __future__ import annotations
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api.routes import webrtc, health, audio, connection
from .services.webrtc_service import WebRTCService
from .services.video_queue_service import video_queue_service
from .services.inference_service import emotion_inference_service
from .core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 수명주기 관리"""
    # Startup
    print("🚀 WebRTC Face Detection Server starting...")
    
    # 정적/템플릿 디렉토리 준비
    Path("templates").mkdir(exist_ok=True, parents=True)
    Path("static").mkdir(exist_ok=True, parents=True)
    
    # 감정 분석 서비스 초기화 (지연 로딩으로 빠른 시작)
    print("🎭 Emotion inference service ready (lazy loading enabled)")
    if not emotion_inference_service.lazy_loading:
        print("🎭 Initializing emotion inference service...")
        await emotion_inference_service.initialize_models()
        print("✅ Emotion inference service initialized")
    
    # 비디오 큐 서비스 시작
    print("🎬 Starting video queue service...")
    await video_queue_service.start()
    print("✅ Video queue service started")
    
    yield
    
    # Shutdown
    print("🛑 Shutting down servers...")
    
    # 감정 분석 서비스 정리
    print("🎭 Cleaning up emotion inference service...")
    await emotion_inference_service.cleanup()
    print("✅ Emotion inference service cleaned up")
    
    # 비디오 큐 서비스 정리
    print("🎬 Stopping video queue service...")
    await video_queue_service.stop()
    print("✅ Video queue service stopped")
    
    # WebRTC 서비스 정리
    webrtc_service = WebRTCService()
    await webrtc_service.cleanup_all_connections()
    
    print("✅ Server shutdown complete")


# FastAPI 애플리케이션 생성
app = FastAPI(
    title="WebRTC Face Detection Server",
    description="Real-time face detection using WebRTC and OpenCV",
    version="1.0.0",
    lifespan=lifespan
)

# 정적 파일 마운트
app.mount("/static", StaticFiles(directory="static"), name="static")

# 오디오 파일 디렉터리 생성
Path("static/audio").mkdir(exist_ok=True, parents=True)

# 템플릿 설정
templates = Jinja2Templates(directory="templates")

# API 라우터 등록
app.include_router(webrtc.router)  # webrtc.router 자체에 prefix가 이미 설정됨
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(audio.router)  # audio.router는 이미 /fetch prefix를 가지고 있음
app.include_router(connection.router)  # connection.router는 이미 /api/connection prefix를 가지고 있음


# 루트 경로 - HTML 템플릿 제공
@app.get("/", response_class=HTMLResponse, tags=["ui"])
async def root(request: Request):
    """메인 페이지"""
    return templates.TemplateResponse("index.html", {"request": request})


# 기본 헬스체크 (기존과의 호환성)
@app.get("/health", tags=["health"])
async def health_check():
    """기본 헬스체크 (기존 엔드포인트와의 호환성)"""
    return {
        "status": "healthy",
        "service": "WebRTC Face Detection Server"
    }


def main():
    """메인 함수 - FastAPI 서버 시작"""
    import uvicorn
    
    print("🚀 WebRTC Face Detection 서버를 시작합니다...")
    print("📍 FastAPI 서버: http://localhost:8000")
    print("📍 WebRTC 클라이언트: http://localhost:8000 (HTML)")
    print("⏹️  종료하려면 Ctrl+C를 누르세요")
    
    # FastAPI 서버 시작
    uvicorn.run(
        "bing_bong.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()



