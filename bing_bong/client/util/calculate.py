
from typing import List, Dict, Tuple

EmotionScores = Dict[str, float]

@staticmethod
def summarize_emotions(queue: List[EmotionScores]) -> Tuple[str, float, EmotionScores]:
    """
    큐에 쌓인 감정 결과들을 평균 내고,
    가장 높은 감정을 반환한다.

    Args:
        queue (List[Dict[str,float]]): 각 프레임의 감정 분석 결과 리스트

    Returns:
        (label, score, mean_scores):
            label : 가장 높은 감정명
            score : 그 감정의 평균값
            mean_scores : 전체 평균 dict
    """
    if not queue:
        return ("none", 0.0, {})

    # key set 확보
    keys = queue[0].keys()
    sums = {k: 0.0 for k in keys}

    # 합산
    for emo in queue:
        for k in keys:
            sums[k] += float(emo.get(k, 0.0))

    # 평균
    n = len(queue)
    mean_scores = {k: v / n for k, v in sums.items()}

    # 지배 감정 추출
    label = max(mean_scores, key=mean_scores.get)
    return label, mean_scores[label], mean_scores