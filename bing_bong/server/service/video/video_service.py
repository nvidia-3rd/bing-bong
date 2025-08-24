import asyncio
import time
import cv2
import numpy as np
from typing import List, Dict, Any, Optional
from .image_inference import ImageInference

class VideoService:
    
    
    def __init__(self, lazy_loading: bool = True):
        self.lazy_loading = lazy_loading
        # 인스턴스 생성
        self.image_inference = ImageInference()

        
    # 동기 추론 코어를 비동기 컨텍스트에서 실행
    async def to_inference_by_frame(self, img_bgr: np.ndarray, session_id: str | None = None) -> Dict[str, Any]:
        result_list = await asyncio.to_thread(
            self.image_inference._emotions_from_frame_sync,
            img_bgr,
            return_bbox=False,
            normalize=True,
        )
        return result_list[0] if isinstance(result_list, list) and result_list else {}
    