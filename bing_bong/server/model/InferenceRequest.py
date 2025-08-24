from dataclasses import dataclass
from .VideoFrameData import VideoFrameData
import time

@dataclass
class InferenceRequest:
    """추론 요청 데이터 구조"""
    request_id: str
    frame_data: VideoFrameData
    model_type: str = "face_detection"  # face_detection, object_detection, emotion_recognition 등
    priority: int = 1  # 1(높음) ~ 5(낮음)
    created_at: float = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = time.time()