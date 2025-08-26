# session_manager.py
import uuid
import time
from typing import Optional
from infrastructure.http_client import post_json


class SessionManager:
    """
    세션 생명주기를 관리하는 클래스
    - 장기 세션 상태 유지
    - 세션 시작/종료 API 호출
    """
    
    def __init__(self, http_client, session_id: Optional[str] = None):
        self.http = http_client
        self.session_id = session_id or str(uuid.uuid4())
        self.session_started = False
        self.created_at = time.time()
    
    async def start_session(self, meta: Optional[dict] = None) -> bool:
        """세션을 시작합니다."""
        if self.session_started:
            return True
            
        try:
            payload = {
                "session_id": self.session_id,
                "meta": meta or {"note": "streamlit-proxy"},
                "ts": time.time()
            }
            await post_json(self.http, "/sessions/start", payload)
            self.session_started = True
            return True
        except Exception as e:
            print(f"세션 시작 실패: {e}")
            return False
    
    async def stop_session(self) -> bool:
        """세션을 종료합니다."""
        # 세션 종료는 한 번만 실행되므로 로그 플래그 불필요
        
        if not self.session_started:
            print("[Session] ℹ️ 세션이 이미 종료된 상태입니다")
            return True
            
        try:
            payload = {
                "session_id": self.session_id,
                "ts": time.time()
            }
            print(f"[Session] 📤 POST /sessions/stop 요청 전송: {payload}")
            await post_json(self.http, "/sessions/stop", payload)
            self.session_started = False
            print("[Session] ✅ 세션 종료 완료")
            return True
        except Exception as e:
            print(f"[Session] ❌ 세션 종료 실패: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def get_session_info(self) -> dict:
        """세션 정보를 반환합니다."""
        return {
            "session_id": self.session_id,
            "session_started": self.session_started,
            "created_at": self.created_at,
            "duration": time.time() - self.created_at if self.session_started else 0
        }
