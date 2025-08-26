# emotion_service.py
import time
import queue
from typing import Dict, Optional, List


class EmotionService:
    """
    감정 분석 서비스 - 큐 기반으로 단순화
    - Video 감정 데이터: 감정 점수 기반 요약
    - Audio 감정 데이터: 텍스트 전사 결과
    """
    
    def __init__(self):
        self.video_emotions = []  # video 감정 데이터 임시 저장
        self.audio_transcripts = []  # audio 전사 결과 임시 저장
        # 허용할 감정 키들
        self.emotion_keys = {'happy', 'sad', 'angry', 'fear', 'disgust', 'surprise', 'neutral', 'contempt'}
    
    def summarize_img_infer(self, target_queue: queue.Queue) -> Optional[dict]:
        """
        큐가 가득 찰 때 비우고, 감정 평균 요약 dict 반환
        """
        # 1) 모든 아이템 꺼내기
        video_emotions = []
        while not target_queue.empty():
            try:
                emotion_data = target_queue.get_nowait()
                video_emotions.append(emotion_data)

            except queue.Empty:
                break

        if not video_emotions:

            return None

        # 2) 감정 키 수집 (숫자 데이터만)
        keys = set()
        for emotion_data in video_emotions:
            if isinstance(emotion_data, dict):
                for key, value in emotion_data.items():
                    # 감정 키이면서 숫자 값인 경우만 추가
                    if key in self.emotion_keys:
                        try:
                            float(value)  # 숫자 변환 테스트
                            keys.add(key)
                        except (ValueError, TypeError):
                            continue  # 숫자가 아니면 건너뛰기



        if not keys:

            return {
                "type": "video",
                "label": "none",
                "score": 0.0,
                "means": {},
                "count": len(video_emotions),
                "timestamp": time.time()
            }

        # 3) 평균 계산
        n = len(video_emotions)
        sums = {k: 0.0 for k in keys}
        for emotion_data in video_emotions:
            if isinstance(emotion_data, dict):
                for k in keys:
                    try:
                        value = emotion_data.get(k, 0.0)
                        sums[k] += float(value)
                    except (ValueError, TypeError):
                        continue  # 변환 실패 시 건너뛰기

        means = {k: (v / n) for k, v in sums.items()}
        label = max(means, key=means.get) if means else "none"



        # 4) summary dict
        summary = {
            "type": "video",
            "label": label,
            "score": float(means.get(label, 0.0)) if means else 0.0,
            "means": means,
            "count": n,
            "timestamp": time.time()
        }

        # 임시 저장소에도 추가 (최종 요약용)
        self.video_emotions.extend(video_emotions)
        
        return summary
       
    def get_video_emotion_summary(self) -> Optional[dict]:
        """Video 감정 데이터 요약을 반환합니다."""
        if not self.video_emotions:
            return None
        
        # 모든 감정 키 수집 (유효한 키만)
        keys = set()
        for emotion_data in self.video_emotions:
            if isinstance(emotion_data, dict):
                for key, value in emotion_data.items():
                    if key in self.emotion_keys:
                        try:
                            float(value)
                            keys.add(key)
                        except (ValueError, TypeError):
                            continue
        
        # 각 감정별 평균 점수 계산
        sums = {k: 0.0 for k in keys}
        n = len(self.video_emotions)
        
        for emotion_data in self.video_emotions:
            if isinstance(emotion_data, dict):
                for k in keys:
                    try:
                        sums[k] += float(emotion_data.get(k, 0.0))
                    except (ValueError, TypeError):
                        continue
        
        means = {k: (v / n) for k, v in sums.items()}
        label = max(means, key=means.get) if means else "none"
        
        return {
            "type": "video",
            "label": label,
            "score": float(means.get(label, 0.0)),
            "means": means,
            "count": n,
            "timestamp": time.time()
        }
    
    def get_audio_emotion_summary(self) -> Optional[dict]:
        """Audio 전사 결과 요약을 반환합니다."""
        if not self.audio_transcripts:
            return None
        
        return {
            "type": "audio",
            "transcript": " ".join(self.audio_transcripts),  # 모든 전사 텍스트 결합
            "count": len(self.audio_transcripts),
            "timestamp": time.time()
        }
    
    def get_final_summary(self) -> dict:
        """최종 감정 요약을 반환합니다."""
        video_summary = self.get_video_emotion_summary()
        audio_summary = self.get_audio_emotion_summary()
        
        return {
            "video": video_summary,
            "audio": audio_summary,
            "timestamp": time.time()
        }
    
    def add_audio_emotion(self, audio_transcript: str) -> None:
        """Audio 전사 결과를 추가합니다."""
        if audio_transcript:
            self.audio_transcripts.append(audio_transcript)

            # 큐가 가득차면 오래된 데이터 제거
            if len(self.audio_transcripts) > 10:  # 최대 10개 유지
                self.audio_transcripts.pop(0)
    
    def reset(self) -> None:
        """서비스 상태를 초기화합니다."""
        self._clear_all_data()
        print("[INFO] EmotionService 초기화 완료")
    
    def _clear_all_data(self) -> None:
        """모든 임시 데이터를 정리합니다."""
        self.video_emotions.clear()
        self.audio_transcripts.clear()
    
    def get_stats(self) -> dict:
        """현재 상태 통계를 반환합니다."""
        return {
            "video_emotions_count": len(self.video_emotions),
            "audio_transcripts_count": len(self.audio_transcripts),
            "timestamp": time.time()
        }