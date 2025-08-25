from .open_api_client import OpenApiClient
import os
import numpy as np
import time


class AudioService:
    
    def __init__(self):
        self.open_api_client = OpenApiClient()

    def get_audio_analysis(self, audio_data: bytes) -> dict:
        
        save_dir = os.path.join(os.getcwd(), "recordings")
        os.makedirs(save_dir, exist_ok=True)

        # 타임스탬프 기반 파일명
        filename = f"{123}_{int(time.time())}.wav"
        filepath = os.path.join(save_dir, filename)

        # 파일 저장
        with open(filepath, "wb") as f:
            f.write(audio_data)

        return self.open_api_client.get_audio_analysis(audio_data)