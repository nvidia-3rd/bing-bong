"""
WebRTC 연결 풀 관리
"""
from __future__ import annotations
from typing import Dict, Any, Optional
from aiortc import RTCPeerConnection
import uuid
import time

class ConnectionPool:
    def __init__(self):
        # { pool_id: {"pc": RTCPeerConnection, "state": str, "created_at": float} }
        self._pool: Dict[str, Dict[str, Any]] = {}

    def add_connection(self, pc: RTCPeerConnection) -> str:
        pool_id = str(uuid.uuid4())
        self._pool[pool_id] = {
            "pc": pc,
            "state": "creating",
            "created_at": time.time(),
        }
        return pool_id

    def get(self, pool_id: str) -> Optional[RTCPeerConnection]:
        entry = self._pool.get(pool_id)
        return entry["pc"] if entry else None

    def update_connection_state(self, pool_id: str, state: str) -> None:
        if pool_id in self._pool:
            self._pool[pool_id]["state"] = state

    def pop(self, pool_id: str) -> None:
        if pool_id in self._pool:
            entry = self._pool.pop(pool_id)
            pc: RTCPeerConnection = entry.get("pc")
            # 실제 close는 서비스 레벨에서 수행 권장

    async def cleanup_all(self):
        """모든 연결 정리"""
        for pool_id in list(self._pool.keys()):
            self.pop(pool_id)
