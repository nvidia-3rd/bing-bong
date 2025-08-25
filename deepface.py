import cv2
from typing import List, Dict, Any
from deepface import DeepFace

def emotions_from_frame(frame_bgr, detector_backend: str = "retinaface", 
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
        return out
        