# data_pipeline.py
import queue
import asyncio
from typing import Optional, Any, List, Dict


class DataPipeline:
    """
    큐 기반 데이터 파이프라인을 관리하는 클래스
    - 백프레셔 처리
    - 큐 상태 모니터링
    - 안전한 데이터 전송
    """
    
    def __init__(self, max_frame_queue_size: int = 20, max_summary_queue_size: int = 20):
        self.max_frame_queue_size = max_frame_queue_size
        self.max_summary_queue_size = max_summary_queue_size
        
        # 비디오 프레임 파이프라인
        self.raw_frame_q = queue.Queue(maxsize=max_frame_queue_size)
        self.frame_q = queue.Queue(maxsize=max_frame_queue_size)
        
        # 감정 분석 결과 파이프라인
        self.image_result_q = queue.Queue(maxsize=max_summary_queue_size)
        self.emotion_summary_q = queue.Queue(maxsize=max_summary_queue_size)
        
        # 오디오 전사 파이프라인
        self.transcript_q = queue.Queue(maxsize=4)
        self.session_summary_q = queue.Queue(maxsize=2)
        
        print(f"[INFO] DataPipeline 초기화 완료 - 프레임큐:{max_frame_queue_size}, 요약큐:{max_summary_queue_size}")
    
    def safe_put(self, target_queue: queue.Queue, item: Any, drop_old: bool = True) -> bool:
        """
        백프레셔 처리를 포함한 안전한 큐 삽입
        
        Args:
            target_queue: 대상 큐
            item: 추가할 항목
            drop_old: True면 큐가 가득 찰 때 오래된 항목 제거, False면 실패
        
        Returns:
            bool: 성공 여부
        """
        try:
            # drop_old가 True면 큐가 가득 찰 경우 가장 오래된 항목을 제거
            if drop_old:
                dropped_count = 0
                while target_queue.full():
                    try:
                        dropped_item = target_queue.get_nowait()
                        dropped_count += 1
                    except queue.Empty:
                        break
                
                if dropped_count > 0:
                    pass        
            
            # 항목 추가
            target_queue.put_nowait(item)
            return True
            
        except queue.Full:
            # drop_old가 False이고 큐가 가득 찬 경우
            print(f"[WARNING] 큐가 가득 참 - 항목 추가 실패")
            return False
        except Exception as e:
            print(f"[ERROR] 큐 삽입 오류: {e}")
            return False
    
    def safe_get(self, source_queue: queue.Queue, timeout: float = 0.1) -> Optional[Any]:
        """
        안전한 큐에서 항목 가져오기
        
        Args:
            source_queue: 소스 큐
            timeout: 타임아웃 (초)
        
        Returns:
            Optional[Any]: 가져온 항목 또는 None
        """
        try:
            return source_queue.get(timeout=timeout)
        except queue.Empty:
            return None
        except Exception as e:
            print(f"[ERROR] 큐 가져오기 오류: {e}")
            return None
    
    def get_queue_status(self) -> dict:
        """모든 큐의 상태를 반환합니다."""
        return {
            "raw_frame_q": self.raw_frame_q.qsize(),
            "frame_q": self.frame_q.qsize(),
            "image_result_q": self.image_result_q.qsize(),
            "emotion_summary_q": self.emotion_summary_q.qsize(),
            "transcript_q": self.transcript_q.qsize(),
            "session_summary_q": self.session_summary_q.qsize()
        }
    
    def get_detailed_status(self) -> dict:
        """상세한 큐 상태를 반환합니다."""
        status = self.get_queue_status()
        detailed = {}
        
        for queue_name, size in status.items():
            queue_obj = getattr(self, queue_name)
            detailed[queue_name] = {
                "size": size,
                "maxsize": queue_obj.maxsize,
                "full": queue_obj.full(),
                "empty": queue_obj.empty(),
                "utilization": (size / queue_obj.maxsize * 100) if queue_obj.maxsize > 0 else 0
            }
        
        return detailed
    
    def clear_all_queues(self) -> dict:
        """모든 큐를 비우고 제거된 항목 수를 반환합니다."""
        cleared_counts = {}
        
        for queue_name in ["raw_frame_q", "frame_q", "image_result_q", 
                          "emotion_summary_q", "transcript_q", "session_summary_q"]:
            queue_obj = getattr(self, queue_name)
            count = 0
            while not queue_obj.empty():
                try:
                    queue_obj.get_nowait()
                    count += 1
                except queue.Empty:
                    break
            cleared_counts[queue_name] = count
        
        total_cleared = sum(cleared_counts.values())
        print(f"[INFO] 모든 큐 정리 완료 - 총 {total_cleared}개 항목 제거됨")
        for queue_name, count in cleared_counts.items():
            if count > 0:
                print(f"  - {queue_name}: {count}개")
        
        return cleared_counts
    
    def is_pipeline_active(self) -> bool:
        """파이프라인이 활성 상태인지 확인합니다."""
        return (self.raw_frame_q.qsize() > 0 or 
                self.frame_q.qsize() > 0 or 
                self.image_result_q.qsize() > 0)
    
    def drain(self, q: queue.Queue) -> List[Any]:
        """큐를 비우고 모든 항목을 리스트로 반환합니다."""
        items = []
        while not q.empty():
            try:
                items.append(q.get_nowait())
            except queue.Empty:
                break
        

        return items
    
    def peek_queue(self, queue_name: str, max_items: int = 5) -> List[Any]:
        """큐의 내용을 제거하지 않고 확인합니다 (디버그용)."""
        try:
            queue_obj = getattr(self, queue_name)
            if hasattr(queue_obj, 'queue'):
                items = list(queue_obj.queue)[:max_items]
                return items
        except AttributeError:
            print(f"[ERROR] 큐 '{queue_name}'을 찾을 수 없습니다")
        except Exception as e:
            print(f"[ERROR] 큐 peek 오류: {e}")
        
        return []
    
    def get_pipeline_health(self) -> dict:
        """파이프라인 건강 상태를 반환합니다."""
        status = self.get_detailed_status()
        health = {
            "overall": "healthy",
            "warnings": [],
            "errors": []
        }
        
        # 큐 사용률 체크
        for queue_name, info in status.items():
            utilization = info["utilization"]
            
            if utilization >= 90:
                health["errors"].append(f"{queue_name} 큐가 거의 가득참 ({utilization:.1f}%)")
                health["overall"] = "critical"
            elif utilization >= 70:
                health["warnings"].append(f"{queue_name} 큐 사용률이 높음 ({utilization:.1f}%)")
                if health["overall"] == "healthy":
                    health["overall"] = "warning"
        
        return health