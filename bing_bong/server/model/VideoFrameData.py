from dataclasses import dataclass
import numpy as np
from av import VideoFrame
from typing import Dict, Any

@dataclass
class VideoFrameData:
    """비디오 프레임 데이터 구조"""
    frame_id: str
    connection_id: str
    frame_number: int
    timestamp: float
    original_frame: VideoFrame
    numpy_array: np.ndarray
    width: int
    height: int
    format: str
    metadata: Dict[str, Any]