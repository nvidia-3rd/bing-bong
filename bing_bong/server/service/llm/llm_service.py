import os
from typing import List, Dict, Optional
from openai import OpenAI
from service.llm.ai_doll_client import AIDoll 
import pandas as pd  

class LlmService:
    
    client = None

    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.doll = AIDoll()

    def call_to_llm(self, audio_text: str, emotion_summary: List[dict]) -> bytes:
        try:
            print(f"🎭 LLM 서비스 시작:")
            print(f"  - 입력 텍스트: {audio_text}")
            print(f"  - 감정 데이터: {len(emotion_summary)}개")
            
            # 감정 분석 결과에서 최고 점수 감정 선택
            sentiment_label = self._pick_top_sentiment(emotion_summary)
            print(f"  - 선택된 감정: {sentiment_label}")
            
            # AI Doll 클라이언트로 LLM 응답 생성
            ai_response: str = self.doll.handle_user_input(
                username="jinsu",
                starttime=pd.Timestamp.now().isoformat(),
                text=audio_text or "",
                sentiment=sentiment_label,
            )
            
            print(f"🤖 AI 응답 생성 완료:")
            print(f"  - 응답 텍스트: {ai_response}")
            print(f"  - 응답 길이: {len(ai_response)}자")
            
            # TTS로 음성 생성
            print(f"🎵 TTS 음성 생성 시작...")
            tts_response = self.client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice="alloy",
                input=ai_response
            )
            
            # TTS 응답을 bytes로 변환
            audio_bytes = tts_response.read()
            print(f"🎵 TTS 완료: {len(audio_bytes)} bytes")
            
            return audio_bytes
            
        except Exception as e:
            print(f"❌ LLM 서비스 오류: {e}")
            # 오류 시 빈 bytes 반환
            return b""

    @staticmethod
    def _pick_top_sentiment(items: List[Dict]) -> str:
        """감정 리스트에서 score가 가장 큰 label을 반환. 비어있으면 'neutral'."""
        if not items:
            return "neutral"
        best = max(
            (x for x in items if "label" in x and "score" in x),
            key=lambda x: x["score"],
            default=None
        )
        return best["label"] if best else "neutral"