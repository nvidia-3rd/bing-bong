import time
import asyncio
from typing import Optional, Dict, Any, Set
from ..components.webrtc.connection_pool import ConnectionPool
from ..components.webrtc.peer_connection import PeerConnectionManager
from ..models.webrtc import SDPOffer, SDPAnswer, ConnectionInfo
from ..core.exceptions import WebRTCError, ConnectionError


class ConnectionContext:
    """
    개별 WebRTC 연결의 생명주기를 관리하는 컨텍스트 매니저
    """
    
    def __init__(self, service: 'ConnectionService'):
        self.service = service
        self.connection_id: Optional[str] = None
        self.pc = None
        self._answer: Optional[SDPAnswer] = None
        self._tasks: Set[asyncio.Task] = set()
        self._is_initialized = False
        self._is_cleaned = False
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()
        
    async def initialize(self, offer: SDPOffer, connection_id: Optional[str] = None) -> None:
        """
        Connection 초기화
        """
        if self._is_initialized:
            raise ConnectionError("Connection already initialized")
            
        try:
            # 1. Connection ID 생성
            if connection_id is None:
                import uuid
                connection_id = str(uuid.uuid4())
            self.connection_id = connection_id
            
            # 2. PeerConnection 생성
            self.pc = await self.service.peer_manager.create_peer_connection()
            
            # 3. 연결 풀에 추가 (우리 ID를 사용)
            pool_connection_id = self.service.connection_pool.add_connection(self.pc)
            print(f"[connection:{self.connection_id}] Pool assigned ID: {pool_connection_id}, using our ID: {self.connection_id}")
            
            # 4. 기본 핸들러 설정
            self.service.peer_manager.setup_default_handlers(self.pc, self.connection_id)
            self._setup_lifecycle_handlers()
            
            # 5. 연결 정보 저장
            self.service._active_connections[self.connection_id] = {
                'pc': self.pc,
            'created_at': time.time(),
            'state': 'creating',
            'offer_info': {
                'sdp': offer.sdp,
                'type': offer.type,
                'mediaData': offer.mediaData,
                'sessionInfo': offer.sessionInfo,
                'timestamp': time.time()
            },
                'connection_id': connection_id,
                'context': self
            }
            
            # 6. WebRTC 협상
            await self.pc.setRemoteDescription(offer.to_rtc())
            rtc_answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(rtc_answer)
            
            # 7. ICE 수집 대기
            await self._wait_for_ice_gathering()
            
            # 8. Answer 생성
            self._answer = SDPAnswer.from_rtc(self.pc.localDescription, connection_id)
            
            # 9. 상태 업데이트
            self.service.connection_pool.update_connection_state(self.connection_id, 'connected')
            self.service._active_connections[self.connection_id]['state'] = 'connected'
            
            self._is_initialized = True
            
            print(f"[connection:{self.connection_id}] ✅ Connection initialized successfully")
            
        except Exception as e:
            # 초기화 실패 시 부분 생성된 리소스 정리
            await self._cleanup_partial_resources()
            raise ConnectionError(f"Connection initialization failed: {e}")
    
    def _setup_lifecycle_handlers(self) -> None:
        """
        Connection 생명주기 이벤트 핸들러 설정
        """
        @self.pc.on("connectionstatechange")
        async def _on_connection_state_change():
            state = self.pc.connectionState
            print(f"[connection:{self.connection_id}] Connection state: {state}")
            
            if state == "connected":
                print(f"[connection:{self.connection_id}] ✅ WebRTC connection established!")
                await self.service._notify_lifecycle_event('connected', self.connection_id)
            elif state in ("failed", "closed"):
                print(f"[connection:{self.connection_id}] ❌ Connection {state}")
                await self.service._notify_lifecycle_event('failed' if state == "failed" else 'closed', self.connection_id)
                await self.cleanup()

        @self.pc.on("iceconnectionstatechange")
        async def _on_ice_state_change():
            ice_state = self.pc.iceConnectionState
            print(f"[connection:{self.connection_id}] ICE state: {ice_state}")
            
            if ice_state == "connected":
                print(f"[connection:{self.connection_id}] ✅ ICE connection established!")
            elif ice_state == "failed":
                print(f"[connection:{self.connection_id}] ❌ ICE connection failed!")
    
    async def _wait_for_ice_gathering(self, max_wait: float = 10.0) -> None:
        """
        ICE candidate 수집 완료 대기
        """
        print(f"[connection:{self.connection_id}] ⏳ ICE candidate 수집 중...")
        wait_time = 0
        while self.pc.iceGatheringState != "complete" and wait_time < max_wait:
            await asyncio.sleep(0.1)
            wait_time += 0.1
        
        if self.pc.iceGatheringState == "complete":
            print(f"[connection:{self.connection_id}] ✅ ICE candidate 수집 완료!")
        else:
            print(f"[connection:{self.connection_id}] ⚠️ ICE 수집 타임아웃, 현재 상태로 진행")
    
    def get_answer(self) -> SDPAnswer:
        """
        SDP Answer 반환
        """
        if not self._is_initialized or not self._answer:
            raise ConnectionError("Connection not initialized or answer not available")
        return self._answer
    
    def add_task(self, task: asyncio.Task) -> None:
        """
        Connection과 연관된 태스크 추가
        """
        self._tasks.add(task)
        task.add_done_callback(lambda t: self._tasks.discard(t))
    
    async def cleanup(self) -> None:
        """
        Connection 및 관련 리소스 정리
        """
        if self._is_cleaned:
            return
            
        self._is_cleaned = True
        
        if self.connection_id:
            print(f"[connection:{self.connection_id}] 🧹 Cleaning up connection...")
            
            # 태스크 정리
            await self._cleanup_tasks()
            
            # PeerConnection 정리
            if self.pc and self.pc.connectionState != "closed":
                try:
                    await self.pc.close()
                    print(f"[connection:{self.connection_id}] Peer connection closed")
                except Exception as e:
                    print(f"[connection:{self.connection_id}] Error closing peer connection: {e}")
            
            # 서비스에서 제거
            self.service._active_connections.pop(self.connection_id, None)
            self.service._tasks.pop(self.connection_id, None)
            
            # 연결 풀 정리
            self.service.connection_pool.update_connection_state(self.connection_id, 'closed')
            
            print(f"[connection:{self.connection_id}] ✅ Cleanup completed")
    
    async def _cleanup_tasks(self) -> None:
        """
        Connection과 연관된 모든 태스크 정리
        """
        if not self._tasks:
            return
            
        print(f"[connection:{self.connection_id}] Cancelling {len(self._tasks)} tasks...")
        
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
                
        # 태스크 완료 대기
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        self._tasks.clear()
    
    async def _cleanup_partial_resources(self) -> None:
        """
        초기화 실패 시 부분 생성된 리소스 정리
        """
        if self.pc:
            try:
                await self.pc.close()
            except Exception:
                pass
                
        if self.connection_id and self.connection_id in self.service._active_connections:
            self.service._active_connections.pop(self.connection_id, None)


class ConnectionService:
    """
    WebRTC 연결 생성 및 관리를 담당하는 서비스
    """
    
    def __init__(self):
        self.connection_pool = ConnectionPool()
        self.peer_manager = PeerConnectionManager()
        self._active_connections = {}
        self._tasks = {}
        self._lifecycle_handlers = {
            'created': [],
            'connected': [],
            'failed': [],
            'closed': []
        }

    def register_lifecycle_handler(self, event: str, handler) -> None:
        """
        생명주기 이벤트 핸들러 등록
        
        Args:
            event: 'created', 'connected', 'failed', 'closed' 중 하나
            handler: async callable(connection_id: str) -> None
        """
        if event not in self._lifecycle_handlers:
            raise ValueError(f"Invalid event: {event}")
        self._lifecycle_handlers[event].append(handler)

    async def _notify_lifecycle_event(self, event: str, connection_id: str) -> None:
        """
        생명주기 이벤트 알림
        """
        print(f"[lifecycle] 🔔 이벤트 발생: {event} for {connection_id}")
        
        for handler in self._lifecycle_handlers[event]:
            try:
                await handler(connection_id)
            except Exception as e:
                print(f"[connection:{connection_id}] Lifecycle handler error ({event}): {e}")
        
        # 추가: 연결 성공 시 자동 콜백 처리
        if event == 'connected':
            await self._handle_connection_success_callback(connection_id)

    async def create_connection_context(self, offer: SDPOffer, connection_id: Optional[str] = None) -> ConnectionContext:
        """
        새로운 ConnectionContext 생성
        
        Args:
            offer: SDP Offer
            connection_id: 선택적 연결 ID
            
        Returns:
            ConnectionContext: 생명주기가 관리되는 연결 컨텍스트
        """
        context = ConnectionContext(self)
        await context.initialize(offer, connection_id)
        await self._notify_lifecycle_event('created', context.connection_id)
        return context

    async def create_connection(self, 
                                offer: SDPOffer,
                                connection_id: Optional[str] = None,
                                await_ice_complete: bool = False) -> SDPAnswer:
        """
        기존 API 호환성을 위한 간단한 연결 생성 메서드
        
        주의: 이 메서드는 connection에 대한 완전한 책임을 지지 않습니다.
        완전한 생명주기 관리를 원한다면 create_connection_context를 사용하세요.
        """
        context = await self.create_connection_context(offer, connection_id)
        return context.get_answer()

    async def get_connection_context(self, connection_id: str) -> Optional[ConnectionContext]:
        """
        연결 ID로 ConnectionContext 조회
        """
        connection_info = self._active_connections.get(connection_id)
        if connection_info:
            return connection_info.get('context')
        return None

    async def get_connection_stats(self, connection_id: str) -> Optional[Dict[str, Any]]:
        """
        연결 통계 반환
        """
        entry = self._active_connections.get(connection_id)
        if not entry:
            return None
        
        pc = entry.get('pc')
        if not pc:
            return None
        
        return {
            'connection_id': connection_id,
            'state': pc.connectionState,
            'ice_state': pc.iceConnectionState,
            'created_at': entry['created_at'],
            'uptime': time.time() - entry['created_at'],
            'tasks_count': len(entry.get('context', ConnectionContext(self))._tasks),
        }

    async def cleanup_connection(self, connection_id: str) -> None:
        """
        특정 연결 정리
        """
        context = await self.get_connection_context(connection_id)
        if context:
            await context.cleanup()

    async def cleanup_all_connections(self) -> None:
        """
        모든 연결 정리
        """
        print("[ConnectionService] Cleaning up all connections...")
        
        # 모든 활성 연결들을 정리
        for connection_id in list(self._active_connections.keys()):
            await self.cleanup_connection(connection_id)
        
        # 연결 풀 정리
        try:
            await self.connection_pool.cleanup_all()
        except Exception as e:
            print(f"[ConnectionService] Connection pool cleanup error: {e}")
        
        print("[ConnectionService] All connections cleaned up")

    async def _handle_connection_success_callback(self, connection_id: str) -> None:
        """
        연결 성공 시 자동 실행되는 콜백 처리
        """
        print(f"[callback] 🎯 연결 성공 콜백 실행: {connection_id}")
        
        try:
            # 연결 정보 조회
            connection_info = self._active_connections.get(connection_id)
            if not connection_info:
                print(f"[callback] ❌ 연결 정보를 찾을 수 없음: {connection_id}")
                return
            
            # 연결 성공 후 자동 실행할 비즈니스 로직들
            await self._execute_post_connection_logic(connection_id, connection_info)
            
            print(f"[callback] ✅ 연결 성공 콜백 완료: {connection_id}")
            
        except Exception as e:
            print(f"[callback] ❌ 연결 성공 콜백 실패: {connection_id} - {e}")

    async def _execute_post_connection_logic(self, connection_id: str, connection_info: dict) -> None:
        """
        연결 성공 후 실행할 비즈니스 로직
        """
        print(f"[post-connection] 🚀 연결 후 로직 시작: {connection_id}")
        
        # 1. 연결 통계 업데이트
        stats = await self.get_connection_stats(connection_id)
        print(f"[post-connection] 📊 연결 통계: {stats}")
        
        # 2. 자동 실행할 초기화 작업들
        initialization_tasks = [
            self._initialize_data_channels(connection_id),
            self._setup_monitoring(connection_id),
            self._prepare_for_data_exchange(connection_id)
        ]
        
        # 3. 병렬로 초기화 작업 실행
        import asyncio
        results = await asyncio.gather(*initialization_tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"[post-connection] ⚠️ 초기화 작업 {i+1} 실패: {result}")
            else:
                print(f"[post-connection] ✅ 초기화 작업 {i+1} 완료")
        
        print(f"[post-connection] ✅ 연결 후 로직 완료: {connection_id}")

    async def _initialize_data_channels(self, connection_id: str) -> None:
        """데이터 채널 초기화"""
        print(f"[init] 📡 데이터 채널 초기화: {connection_id}")
        await asyncio.sleep(0.05)  # 실제 초기화 시뮬레이션
        
    async def _setup_monitoring(self, connection_id: str) -> None:
        """모니터링 설정"""
        print(f"[init] 📈 모니터링 설정: {connection_id}")
        await asyncio.sleep(0.05)  # 실제 설정 시뮬레이션
        
    async def _prepare_for_data_exchange(self, connection_id: str) -> None:
        """데이터 교환 준비"""
        print(f"[init] 🔄 데이터 교환 준비: {connection_id}")
        await asyncio.sleep(0.05)  # 실제 준비 시뮬레이션

    def list_active_connections(self) -> Dict[str, Dict[str, Any]]:
        """
        활성 연결 목록 반환
        """
        return {
            conn_id: {
                'state': conn_info.get('state', 'unknown'),
                'created_at': conn_info.get('created_at'),
                'uptime': time.time() - conn_info.get('created_at', time.time()),
                'has_context': 'context' in conn_info
            }
            for conn_id, conn_info in self._active_connections.items()
        }

    async def get_connection_context(self, connection_id: str) -> Optional[ConnectionContext]:
        """
        특정 connection의 context 반환
        """
        connection_info = self._active_connections.get(connection_id)
        if connection_info and 'context' in connection_info:
            return connection_info['context']
        return None
