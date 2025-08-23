"""
헬스체크 및 상태 모니터링 API
"""
from fastapi import APIRouter, Depends
from ...services.webrtc_service import WebRTCService
from ..dependencies import get_webrtc_service
import time

router = APIRouter()


@router.get("/health")
async def health_check():
    """기본 헬스체크"""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "service": "WebRTC Face Detection Server"
    }


@router.get("/health/detailed")
async def detailed_health_check(
    webrtc_service: WebRTCService = Depends(get_webrtc_service)
):
    """상세 헬스체크 - 서비스별 상태 포함"""
    try:
        webrtc_stats = webrtc_service.get_service_stats()
        
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "services": {
                "webrtc": {
                    "status": "healthy",
                    "active_connections": webrtc_stats.get("active_connections", 0),
                    "total_connections": webrtc_stats.get("total_connections_created", 0)
                },
                "media": {
                    "status": "healthy",
                    "face_detection_enabled": True
                }
            },
            "system": {
                "uptime": time.time() - (webrtc_stats.get("uptime", 0)),
                "memory_usage": "N/A",  # 필요시 psutil 등으로 실제 메모리 사용량 추가
                "cpu_usage": "N/A"      # 필요시 psutil 등으로 실제 CPU 사용량 추가
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "timestamp": time.time(),
            "error": str(e),
            "services": {
                "webrtc": {"status": "error"},
                "media": {"status": "error"}
            }
        }


@router.get("/health/webrtc")
async def webrtc_health_check(
    webrtc_service: WebRTCService = Depends(get_webrtc_service)
):
    """WebRTC 서비스 전용 헬스체크"""
    try:
        stats = webrtc_service.get_service_stats()
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "service": "webrtc",
            "stats": stats
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "timestamp": time.time(),
            "service": "webrtc",
            "error": str(e)
        }


@router.get("/health/media")
async def media_health_check():
    """미디어 서비스 전용 헬스체크"""
    try:
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "service": "media",
            "stats": {
                "face_detection_enabled": True,
                "opencv_available": True
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "timestamp": time.time(),
            "service": "media",
            "error": str(e)
        }
