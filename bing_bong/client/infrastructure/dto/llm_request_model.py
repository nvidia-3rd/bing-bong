from typing import List

class LlmRequestModel:

    def __init__(self, audio_text: str, emotion_summary: List[dict], session_id: str):
        self.audio_text = audio_text
        self.emotion_summary = emotion_summary
        self.session_id = session_id


    def to_dict(self):
        return {
            "audio_text": self.audio_text,
            "emotion_summary": self.emotion_summary,
            "session_id": self.session_id
        }