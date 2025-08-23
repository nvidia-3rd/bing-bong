from fastapi import APIRouter, Depends
import uuid, time

from ...services.connection_service import ConnectionService
from ...services.webrtc_service import WebRTCService

from ...models.webrtc import SDPOffer, WebRTCResponse
from ..dependencies import get_webrtc_service, get_connection_service


router = APIRouter(prefix="/api/connection", tags=["connection"])


@router.post("/create", response_model=WebRTCResponse)
async def create_test_connection(
     offer: SDPOffer,
    connection_service: ConnectionService = Depends(get_connection_service)):
    """
    ConnectionContext 테스트용 연결 생성
    """
    # connection_id는 offer의 sessionInfo에서 가져오거나 새로 생성
    connection_id = (offer.sessionInfo and offer.sessionInfo.connectionId) or str(uuid.uuid4())
    print(f"[connection-api] 🧪 테스트 연결 생성 시작: {connection_id}")
    
    try:
        
        # ConnectionContext를 사용한 안전한 연결 생성
        context = await connection_service.create_connection_context(
            offer, connection_id
        )
        
        # 연결 상태 정보
        stats = await connection_service.get_connection_stats(connection_id)
        active_connections = connection_service.list_active_connections()
        
        print(f"[connection-api] ✅ 테스트 연결 생성 완료: {connection_id}")
        print(f"[connection-api] 📊 현재 활성 연결 수: {len(active_connections)}")
        
        return WebRTCResponse(
            success=True,
            message=f"Test connection {connection_id} created successfully",
            data={
                "connection_id": connection_id,
                "status": "connected",
                "timestamp": time.time(),
                "connection_stats": stats,
                "active_connections_count": len(active_connections),
                "test_mode": True,
                "lifecycle_managed": True
            },
            timestamp=time.time()
        )
        
    except Exception as e:
        print(f"[connection-api] ❌ 테스트 연결 생성 실패: {e}")
        return WebRTCResponse(
            success=False,
            message=f"Failed to create test connection: {str(e)}",
            data={"connection_id": connection_id, "error": str(e)},
            timestamp=time.time()
        )


@router.delete("/{connection_id}")
async def cleanup_connection(
    connection_id: str, 
    connection_service: ConnectionService = Depends(get_connection_service)
):
    """
    테스트 연결 정리 및 생명주기 확인
    """
    print(f"[connection-api] 🧹 연결 정리 시작: {connection_id}")
    
    try:
        # 정리 전 상태 확인
        before_stats = await connection_service.get_connection_stats(connection_id)
        before_connections = connection_service.list_active_connections()
        
        # ConnectionContext를 통한 안전한 정리
        await connection_service.cleanup_connection(connection_id)
        
        # 정리 후 상태 확인
        after_stats = await connection_service.get_connection_stats(connection_id)
        after_connections = connection_service.list_active_connections()
        
        print(f"[connection-api] ✅ 연결 정리 완료: {connection_id}")
        print(f"[connection-api] 📊 연결 수 변화: {len(before_connections)} → {len(after_connections)}")
        
        return {
            "success": True,
            "message": f"Connection {connection_id} cleaned up successfully",
            "data": {
                "connection_id": connection_id,
                "before_cleanup": {
                    "connection_found": before_stats is not None,
                    "active_connections_count": len(before_connections)
                },
                "after_cleanup": {
                    "connection_found": after_stats is not None,
                    "active_connections_count": len(after_connections)
                },
                "lifecycle_verified": before_stats is not None and after_stats is None
            },
            "timestamp": time.time()
        }
        
    except Exception as e:
        print(f"[connection-api] ❌ 연결 정리 실패: {e}")
        return {
            "success": False,
            "message": f"Failed to cleanup connection: {str(e)}",
            "data": {"connection_id": connection_id, "error": str(e)},
            "timestamp": time.time()
        }


@router.get("/status")
async def get_connections_status(connection_service: ConnectionService = Depends(get_connection_service)):
    """
    모든 연결 상태 조회
    """
    try:
        active_connections = connection_service.list_active_connections()
        
        # 각 연결의 상세 정보
        detailed_connections = {}
        for conn_id, conn_info in active_connections.items():
            stats = await connection_service.get_connection_stats(conn_id)
            detailed_connections[conn_id] = {
                "basic_info": conn_info,
                "detailed_stats": stats
            }
        
        return {
            "success": True,
            "message": "Connections status retrieved",
            "data": {
                "total_connections": len(active_connections),
                "connections": detailed_connections,
                "pool_status": "healthy" if len(active_connections) >= 0 else "error"
            },
            "timestamp": time.time()
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to get connections status: {str(e)}",
            "timestamp": time.time()
        }


@router.delete("/all")
async def cleanup_all_connections(connection_service: ConnectionService = Depends(get_connection_service)):
    """
    모든 연결 정리
    """
    print(f"[connection-api] 🧹 모든 연결 정리 시작")
    
    try:
        before_connections = connection_service.list_active_connections()
        
        # 모든 연결 정리
        await connection_service.cleanup_all_connections()
        
        after_connections = connection_service.list_active_connections()
        
        print(f"[connection-api] ✅ 모든 연결 정리 완료")
        print(f"[connection-api] 📊 연결 수 변화: {len(before_connections)} → {len(after_connections)}")
        
        connections_cleared = len(before_connections) - len(after_connections)
        
        return {
            "success": True,
            "message": "All connections cleaned up successfully",
            "data": {
                "before_cleanup": {
                    "total_connections": len(before_connections)
                },
                "after_cleanup": {
                    "total_connections": len(after_connections)
                },
                "connections_cleared": connections_cleared
            },
            "timestamp": time.time()
        }
        
    except Exception as e:
        print(f"[connection-api] ❌ 모든 연결 정리 실패: {e}")
        return {
            "success": False,
            "message": f"Failed to cleanup all connections: {str(e)}",
            "timestamp": time.time()
        }
