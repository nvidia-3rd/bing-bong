import os
import io
from typing import Optional
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

class OpenApiClient:
    def __init__(self, api_key: Optional[str] = None):
        self.client = OpenAI(api_key= os.getenv("OPENAI_API_KEY"))

    def get_audio_analysis(self, audio_data: bytes) -> dict:
        buf = io.BytesIO(audio_data)
        buf.name = "audio.wav"

        transcript = self.client.audio.transcriptions.create(
            model= 'gpt-4o-mini-transcribe',  # Whisper 기반 STT 모델("whisper-1", "gpt-4o-transcribe","gpt-4o-mini-transcribe")
            file=buf,
            language="ko",  # 한국어 
            response_format="json"
        )
        print(transcript.text)
        return transcript

    
    
