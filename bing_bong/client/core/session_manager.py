# session_manager.py
"""
Streamlit 세션을 관리하는 매니저
- 세션 ID 생성 및 관리
- 세션 상태 추적
- 세션 정보 제공
"""

import uuid
import time
import streamlit as st
from typing import Dict, Any, Optional


class StreamlitSessionManager:
    """Streamlit 세션을 관리하는 매니저 클래스"""
    
    def __init__(self):
        """세션 매니저 초기화"""
        self._ensure_session_exists()
    
    def _ensure_session_exists(self):
        """세션이 존재하지 않으면 생성"""
        if "session_id" not in st.session_state:
            self._create_new_session()
    
    def _create_new_session(self):
        """새 세션 생성"""
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.session_start_time = time.time()
        st.session_state.session_started = False
        st.session_state.session_meta = {"note": "streamlit-proxy"}
        print(f"[SessionManager] 🆔 새 세션 생성: {st.session_state.session_id}")
    
    def get_session_id(self) -> str:
        """현재 세션 ID 반환"""
        self._ensure_session_exists()
        return st.session_state.session_id
    
    def get_session_info(self) -> Dict[str, Any]:
        """현재 세션 정보 반환"""
        session_id = self.get_session_id()
        
        if "session_start_time" in st.session_state:
            duration = time.time() - st.session_state.session_start_time
            return {
                "session_id": session_id,
                "session_started": st.session_state.get("session_started", False),
                "created_at": st.session_state.session_start_time,
                "duration": duration if st.session_state.get("session_started") else 0,
                "session_meta": st.session_state.get("session_meta", {}),
                "wired_lifecycle": st.session_state.get("wired_lifecycle", False)
            }
        
        return {
            "session_id": session_id, 
            "session_started": False,
            "wired_lifecycle": False
        }
    
    def get_session_status(self) -> str:
        """세션 상태를 이모지와 함께 반환"""
        if st.session_state.get("session_started", False):
            return "🟢 활성"
        elif st.session_state.get("wired_lifecycle", False):
            return "🟡 준비"
        else:
            return "⚪ 비활성"
    
    def get_session_duration(self) -> str:
        """세션 지속 시간을 포맷된 문자열로 반환"""
        if "session_start_time" in st.session_state:
            duration = time.time() - st.session_state.session_start_time
            if duration < 60:
                return f"{duration:.1f}초"
            elif duration < 3600:
                return f"{duration/60:.1f}분"
            else:
                return f"{duration/3600:.1f}시간"
        return "0초"
    
    def start_session(self) -> bool:
        """세션 시작"""
        try:
            st.session_state.session_started = True
            print(f"[SessionManager] ✅ 세션 시작됨: {self.get_session_id()}")
            return True
        except Exception as e:
            print(f"[SessionManager] ❌ 세션 시작 실패: {e}")
            return False
    
    def stop_session(self) -> bool:
        """세션 종료"""
        try:
            st.session_state.session_started = False
            print(f"[SessionManager] ✅ 세션 종료됨: {self.get_session_id()}")
            return True
        except Exception as e:
            print(f"[SessionManager] ❌ 세션 종료 실패: {e}")
            return False
    
    def reset_session(self) -> bool:
        """세션 초기화 (새 세션 생성)"""
        try:
            # 기존 세션 정보 백업
            old_session_id = self.get_session_id()
            
            # 새 세션 생성
            self._create_new_session()
            
            print(f"[SessionManager] 🔄 세션 재설정: {old_session_id} → {self.get_session_id()}")
            return True
        except Exception as e:
            print(f"[SessionManager] ❌ 세션 재설정 실패: {e}")
            return False
    
    def update_meta(self, key: str, value: Any) -> bool:
        """세션 메타데이터 업데이트"""
        try:
            if "session_meta" not in st.session_state:
                st.session_state.session_meta = {}
            
            st.session_state.session_meta[key] = value
            print(f"[SessionManager] 📝 메타데이터 업데이트: {key} = {value}")
            return True
        except Exception as e:
            print(f"[SessionManager] ❌ 메타데이터 업데이트 실패: {e}")
            return False
    
    def get_meta(self, key: str, default: Any = None) -> Any:
        """세션 메타데이터 조회"""
        return st.session_state.get("session_meta", {}).get(key, default)
    
    def is_active(self) -> bool:
        """세션이 활성 상태인지 확인"""
        return st.session_state.get("session_started", False)
    
    def is_ready(self) -> bool:
        """세션이 준비 상태인지 확인"""
        return st.session_state.get("wired_lifecycle", False)
    
    def get_session_summary(self) -> Dict[str, Any]:
        """세션 요약 정보 반환"""
        info = self.get_session_info()
        return {
            "session_id": info["session_id"][:8] + "...",  # 짧은 형태
            "status": self.get_session_status(),
            "duration": self.get_session_duration(),
            "created_at": time.strftime("%H:%M:%S", time.localtime(info.get("created_at", 0))),
            "meta": info.get("session_meta", {})
        }


# 전역 세션 매니저 인스턴스
_session_manager: Optional[StreamlitSessionManager] = None


def get_session_manager() -> StreamlitSessionManager:
    """전역 세션 매니저 인스턴스 반환"""
    global _session_manager
    if _session_manager is None:
        _session_manager = StreamlitSessionManager()
    return _session_manager


# 편의 함수들
def get_session_id() -> str:
    """현재 세션 ID 반환"""
    return get_session_manager().get_session_id()


def get_session_info() -> Dict[str, Any]:
    """현재 세션 정보 반환"""
    return get_session_manager().get_session_info()


def get_session_status() -> str:
    """세션 상태 반환"""
    return get_session_manager().get_session_status()


def get_session_duration() -> str:
    """세션 지속 시간 반환"""
    return get_session_manager().get_session_duration()


def start_session() -> bool:
    """세션 시작"""
    return get_session_manager().start_session()


def stop_session() -> bool:
    """세션 종료"""
    return get_session_manager().stop_session()


def reset_session() -> bool:
    """세션 재설정"""
    return get_session_manager().reset_session()
