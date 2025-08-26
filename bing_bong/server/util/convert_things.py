import numpy as np
import cv2

class ConvertThings:

    @staticmethod
    def bytes_to_bgr(img_bytes: bytes) -> np.ndarray:
        arr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("decode failed")
        return img  # BGR