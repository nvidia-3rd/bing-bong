"""
WebRTC 피어 연결 관리
"""
from __future__ import annotations
from typing import Optional
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer

class PeerConnectionManager:
    """
    RTCPeerConnection 생성과 기본 이벤트 핸들러 묶음.
    프로젝트에 맞게 확장 가능.
    """

    def __init__(self, stun_turn: Optional[list] = None):
        # 기본 STUN. 필요시 TURN 추가
        self._ice_servers = stun_turn or [
            RTCIceServer(urls="stun:stun.l.google.com:19302"),
            RTCIceServer(urls="stun:stun1.l.google.com:19302"),
        ]

    async def create_peer_connection(self) -> RTCPeerConnection:
        config = RTCConfiguration(iceServers=self._ice_servers)
        pc = RTCPeerConnection(configuration=config)
        return pc

    def setup_default_handlers(self, pc: RTCPeerConnection, conn_id: str) -> None:
        @pc.on("connectionstatechange")
        async def _on_conn_state_change():
            print(f"[webrtc:{conn_id}] 🔗 Connection state: {pc.connectionState}")
            if pc.connectionState == "connected":
                print(f"[webrtc:{conn_id}] ✅ P2P 연결 완료!")
            elif pc.connectionState == "failed":
                print(f"[webrtc:{conn_id}] ❌ P2P 연결 실패!")

        @pc.on("iceconnectionstatechange")
        async def _on_ice_state_change():
            print(f"[webrtc:{conn_id}] 🧊 ICE state: {pc.iceConnectionState}")
            if pc.iceConnectionState == "completed":
                print(f"[webrtc:{conn_id}] ✅ ICE 연결 완료!")
            elif pc.iceConnectionState == "failed":
                print(f"[webrtc:{conn_id}] ❌ ICE 연결 실패! STUN/TURN 서버 확인 필요")

        @pc.on("icegatheringstatechange")
        async def _on_ice_gathering_change():
            print(f"[webrtc:{conn_id}] 🔍 ICE gathering: {pc.iceGatheringState}")

        @pc.on("signalingstatechange")
        async def _on_signal_state_change():
            print(f"[webrtc:{conn_id}] 📡 Signaling state: {pc.signalingState}")

        # 필요 시 추가 핸들러(negotiationneeded 등) 등록
