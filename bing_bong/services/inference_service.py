
"""
감정 분석 추론 서비스 - DeepFace를 사용한 실시간 감정 인식
"""

import asyncio
import time
import cv2
import numpy as np
from typing import List, Dict, Any, Optional
import logging
import os

# TensorFlow 호환성 설정 (mutex 충돌 방지)
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'  # 더 강력한 로그 억제
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'  # protobuf 호환성

# TensorFlow import 전에 설정
import tensorflow as tf
tf.get_logger().setLevel('ERROR')
tf.config.threading.set_inter_op_parallelism_threads(1)  # 스레드 충돌 방지
tf.config.threading.set_intra_op_parallelism_threads(1)

from deepface import DeepFace

logger = logging.getLogger(__name__)


class EmotionInferenceService:
    """감정 분석 추론 서비스"""
    
    def __init__(self, lazy_loading: bool = True):
        self.model_loaded = False
        self.lazy_loading = lazy_loading
        self.inference_count = 0
        self.successful_inferences = 0
        self.failed_inferences = 0
        print("🎭 EmotionInferenceService 초기화 완료")
        if lazy_loading:
            print("⚡ 지연 로딩 모드 활성화 - 첫 요청 시 모델을 로드합니다")
    
    async def initialize_models(self):
        """DeepFace 모델 초기화 (첫 호출 시 자동으로 다운로드됨)"""
        try:
            print("🚀 DeepFace 모델 초기화 시작...")
            print("📥 모델 다운로드 중... (첫 실행 시 시간이 걸릴 수 있습니다)")
            
            # 작은 더미 이미지로 빠른 초기화
            dummy_img = np.zeros((48, 48, 3), dtype=np.uint8)  # 더 작은 이미지 사용
            
            try:
                print("⏳ TensorFlow 모델 로딩 중...")
                await asyncio.to_thread(
                    DeepFace.analyze,
                    img_path=dummy_img,
                    actions=["emotion"],
                    detector_backend="opencv",  # 가장 빠른 백엔드
                    enforce_detection=False,
                    silent=True
                )
                self.model_loaded = True
                print("✅ DeepFace 모델 초기화 완료!")
                return True
                
            except Exception as e:
                print(f"⚠️ DeepFace 모델 초기화 중 경고: {e}")
                # 경고가 있어도 일단 진행 (보통 정상 작동함)
                self.model_loaded = True
                return True
                
        except Exception as e:
            print(f"❌ DeepFace 모델 초기화 실패: {e}")
            logger.error(f"DeepFace 초기화 실패: {e}")
            return False
    
    async def run_emotion_analysis(self, frame_bgr: np.ndarray, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        비동기 감정 분석 실행
        
        Args:
            frame_bgr: BGR 형식의 입력 프레임
            metadata: 추가 메타데이터
            
        Returns:
            감정 분석 결과
        """
        start_time = time.time()
        self.inference_count += 1
        
        print(f"🎭 [EmotionInferenceService] 감정 분석 시작 - 이미지: {frame_bgr.shape}")
        
        try:
            # 모델 초기화 확인
            if not self.model_loaded:
                await self.initialize_models()
            
            # 감정 분석 실행 (thread pool에서 실행)
            results = await asyncio.to_thread(
                self._emotions_from_frame_sync,
                frame_bgr,
                detector_backend="opencv",  # 빠른 처리를 위해 opencv 사용
                enforce_detection=False,
                normalize=True,
                return_bbox=False
            )
            
            processing_time = time.time() - start_time
            self.successful_inferences += 1
            
            # 결과 로그
            face_count = len(results)
            if face_count > 0:
                dominant_emotion = max(results[0], key=results[0].get)
                confidence = results[0][dominant_emotion]
                print(f"✅ [EmotionInferenceService] 감정 분석 완료! {face_count}개 얼굴, 주요 감정: {dominant_emotion} ({confidence:.2f})")
            else:
                print(f"✅ [EmotionInferenceService] 감정 분석 완료! 얼굴 없음")
            
            return {
                "success": True,
                "face_count": face_count,
                "emotions": results,
                "processing_time": processing_time,
                "metadata": metadata
            }
            
        except Exception as e:
            processing_time = time.time() - start_time
            self.failed_inferences += 1
            
            error_msg = f"감정 분석 실패: {str(e)}"
            print(f"❌ [EmotionInferenceService] {error_msg}")
            logger.error(error_msg)
            
            return {
                "success": False,
                "face_count": 0,
                "emotions": [],
                "processing_time": processing_time,
                "error": str(e),
                "metadata": metadata
            }

    def _emotions_from_frame_sync(self, frame_bgr, detector_backend: str = "opencv", 
        align: bool = True, enforce_detection: bool = False, normalize: bool = True, return_bbox = False
        ) -> List[Dict[str, Any]]:

        """ 프레임을 입력으로 받아 얼굴 감정을 분석하고 결과를 반환하는 함수

        Args:
            frame_bgr (_type_): 입력 프레임 (HxWx3, BGR; OpenCV 프레임)
            detector_backend (str, optional): 얼굴 검출 모델, "opencv"(빠름) / "retinaface"(정확) . Defaults to "retinaface".
            enforce_detection (bool, optional): _description_. Defaults to False.
            normalize (bool, optional): 감정 확률 합이 1이 되도록 정규화. Defaults to True.
            return_bbox (bool, optional): 얼굴 위치도 반환할지 여부. Defaults to False.

        Returns:
            List[Dict[str, Any]]:   case1 : bbox = False
                                    {"angry": p, "disgust": p, …},  # 감정별 확률(정규화 옵션)
                                    
                                    case2 : bbox = True
                                    얼굴마다 한 항목씩 갖는 리스트. 각 항목은:
                                    {
                                        "scores": {"angry": p, "disgust": p, …},  # 감정별 확률(정규화 옵션)
                                        "box": {"x": int, "y": int, "w": int, "h": int}  # 얼굴 위치(있으면)
                                    }
                                    얼굴이 없으면 [] 반환
        """

        # BGR -> RGB (DeepFace 권장)
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

        results = DeepFace.analyze(
            img_path=frame_rgb,
            actions=["emotion"],
            detector_backend=detector_backend,
            enforce_detection=enforce_detection,
            align=align,
            silent=True
        )

        # 반환 형태 통일 (단일 얼굴이면 dict, 다중이면 list)
        faces = results if isinstance(results, list) else [results]

        out: List[Dict[str, Any]] = []
        
        if return_bbox:
            ## bbox도 반환시
            for f in faces:
                if not isinstance(f, dict) or "emotion" not in f:
                    continue
                scores = dict(f["emotion"])  # {'angry': …, 'disgust': …, …}
                if normalize:
                    s = sum(float(v) for v in scores.values()) or 1.0
                    scores = {k: float(v) / s for k, v in scores.items()}
                out.append({
                    "scores": scores,
                    "box": f.get("region")  # {'x','y','w','h'} 또는 None
                })
            return out
        else:
            for f in faces:
                if not isinstance(f, dict) or "emotion" not in f:
                    continue
                scores = dict(f["emotion"])  # {'angry': …, 'disgust': …, …}
                if normalize:
                    s = sum(float(v) for v in scores.values()) or 1.0
                    scores = {k: float(v) / s for k, v in scores.items()}
                out.append(scores)
            print(f"🎭 [outoutout] 감정 분석 결과: {out}")    
            return out

    def get_statistics(self) -> Dict[str, Any]:
        """감정 분석 서비스 통계 반환"""
        success_rate = 0
        if self.inference_count > 0:
            success_rate = self.successful_inferences / self.inference_count * 100
            
        return {
            "total_inferences": self.inference_count,
            "successful_inferences": self.successful_inferences,
            "failed_inferences": self.failed_inferences,
            "success_rate": round(success_rate, 2),
            "model_loaded": self.model_loaded,
            "service_type": "emotion_analysis"
        }
    
    async def cleanup(self):
        """리소스 정리"""
        print("🎭 EmotionInferenceService 정리 중...")
        self.model_loaded = False
        print("✅ EmotionInferenceService 정리 완료")


# 전역 인스턴스 (지연 로딩 활성화)
emotion_inference_service = EmotionInferenceService(lazy_loading=True)