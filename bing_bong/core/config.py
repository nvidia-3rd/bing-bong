"""
애플리케이션 설정 관리
"""
import os
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """애플리케이션 설정"""
    
    # 서버 설정
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False  # Docker 환경에서는 기본적으로 False
    log_level: str = "info"
    
    # WebRTC 설정
    ice_servers: List[str] = [
        "stun:stun.l.google.com:19302",
        "stun:stun1.l.google.com:19302"
    ]
    
    # 미디어 설정
    max_frame_size: int = 10 * 1024 * 1024  # 10MB
    supported_image_types: List[str] = ["image/jpeg", "image/png", "image/gif"]
    
    # 얼굴 인식 설정
    face_detection_enabled: bool = True
    face_detection_scale_factor: float = 1.1
    face_detection_min_neighbors: int = 5
    face_detection_min_size: tuple = (30, 30)
    
    class Config:
        env_file = ".env"
        # Docker 환경에서 환경변수 우선 사용
        env_file_encoding = 'utf-8'


# 전역 설정 인스턴스
settings = Settings()
