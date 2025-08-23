"""
의존성 주입 설정
"""
from functools import lru_cache
from ..services.webrtc_service import WebRTCService
from ..services.connection_service import ConnectionService

# 싱글톤 인스턴스들
_connection_service_instance = None
_webrtc_service_instance = None

def get_connection_service() -> ConnectionService:
    """
    ConnectionService 싱글톤 인스턴스 반환
    """
    global _connection_service_instance
    if _connection_service_instance is None:
        _connection_service_instance = ConnectionService()
    return _connection_service_instance

def get_webrtc_service() -> WebRTCService:
    """
    WebRTCService 싱글톤 인스턴스 반환
    - ConnectionService와 동일한 인스턴스를 공유하도록 설정
    """
    global _webrtc_service_instance
    if _webrtc_service_instance is None:
        # ConnectionService 인스턴스를 먼저 생성
        connection_service = get_connection_service()
        _webrtc_service_instance = WebRTCService()
        # 동일한 ConnectionService 인스턴스 사용
        _webrtc_service_instance.connection_service = connection_service
    return _webrtc_service_instance
