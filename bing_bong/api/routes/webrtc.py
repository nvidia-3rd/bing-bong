from fastapi import APIRouter, Depends
import uuid, time
import base64
import cv2
import numpy as np

from ...services.webrtc_service import WebRTCService
from ...services.video_queue_service import video_queue_service
from ...services.inference_service import emotion_inference_service
from ...models.webrtc import SDPOffer, WebRTCResponse
from ..dependencies import get_webrtc_service

router = APIRouter(prefix="/api/webrtc", tags=["webrtc"])

@router.post("/offer", response_model=WebRTCResponse)
async def create_webrtc_connection(
    offer: SDPOffer,
    webrtc_service: WebRTCService = Depends(get_webrtc_service)
):
    connection_id = str(uuid.uuid4())
    print(f"[webrtc-api] Offer received. connection_id={connection_id}")

    if offer.mediaData:
        print(f"[webrtc-api] MediaData: {offer.mediaData.model_dump(exclude_unset=True)}")
    if offer.sessionInfo:
        print(f"[webrtc-api] SessionInfo: {offer.sessionInfo.model_dump(exclude_unset=True)}")
    if offer.videoFrame:
        print(f"[webrtc-api] 🎥 VideoFrame received: frame={offer.videoFrame.get('frame_number')}, duration={offer.videoFrame.get('connection_duration'):.1f}s")
    if offer.connectionStatus:
        print(f"[webrtc-api] 🔗 Connection Status: {offer.connectionStatus.get('status')} - {offer.connectionStatus.get('duration'):.1f}s")

    # 비디오 프레임 처리
    if offer.videoFrame:
        await process_video_frame(connection_id, offer.videoFrame)

    answer = await webrtc_service.create_connection(offer, connection_id)



    return WebRTCResponse(
        success=True,
        message=f"WebRTC connection {connection_id} established successfully",
        data={
            "sdp": answer.sdp,
            "type": answer.type or "answer",
            "connection_id": connection_id,
            "status": "connected",
            "timestamp": time.time(),
            "media_info": {
                "received_media_data": offer.mediaData is not None,
                "received_session_info": offer.sessionInfo is not None,
                "offer_size": len(offer.sdp),
                "client_timestamp": getattr(offer.mediaData, 'timestamp', None) if offer.mediaData else None
            }
        },
        timestamp=time.time()
    )


# 상세 메타데이터 수신
@router.post("/connections/{connection_id}/metadata")
async def update_metadata(connection_id: str, payload: dict):
    print(f"[webrtc-api] metadata {connection_id}: size={len(str(payload))} chars")
    return {"ok": True}



# 연결별 추론 결과 조회
@router.get("/connections/{connection_id}/results")
async def get_connection_results(connection_id: str, limit: int = 10):
    """특정 연결의 추론 결과들 조회"""
    results = video_queue_service.get_recent_results(connection_id, limit)
    connection_info = video_queue_service.get_connection_info(connection_id)
    
    return {
        "success": True,
        "message": f"Results for connection {connection_id}",
        "data": {
            "connection_info": connection_info,
            "recent_results": [
                {
                    "frame_id": r.frame_id,
                    "model_type": r.model_type,
                    "success": r.success,
                    "results": r.results,
                    "processing_time": r.processing_time,
                    "completed_at": r.completed_at,
                    "error_message": r.error_message
                } for r in results
            ]
        },
        "timestamp": time.time()
    }

# 비디오 프레임 처리 함수
async def process_video_frame(connection_id: str, video_frame_data: dict):
    """비디오 프레임 데이터 처리 (Streamlit에서 전송된 데이터)"""
    try:
        frame_number = video_frame_data.get('frame_number', 0)
        timestamp = video_frame_data.get('timestamp', time.time())
        connection_duration = video_frame_data.get('connection_duration', 0)
        status = video_frame_data.get('status', 'unknown')
        image_data = video_frame_data.get('image_data')
        image_shape = video_frame_data.get('image_shape', [480, 640, 3])
        
        print(f"[webrtc-api] 🎥 Processing Streamlit frame {frame_number} for connection {connection_id}")
        print(f"[webrtc-api] 📊 Duration: {connection_duration:.1f}s, Status: {status}")
        
        # 큐 상태 체크
        queue_stats = video_queue_service.get_statistics()
        extracted_count = queue_stats.get('image_extraction_by_connection', {}).get(connection_id, 0)
        
        print(f"[webrtc-api] 📊 Queue Status: {queue_stats['frame_queue_size']}/{video_queue_service.max_queue_size} frames, "
              f"{queue_stats['total_inference_completed']} completed")
        print(f"[webrtc-api] 🖼️  Image Extraction: {extracted_count} from {connection_id[:8]}... "
              f"(총 {queue_stats['total_extracted_images']}개 추출됨)")
        
        if image_data:
            # Base64 이미지 디코딩
            img_bytes = base64.b64decode(image_data)
            img_array = np.frombuffer(img_bytes, dtype=np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            
            if img is not None:
                print(f"[webrtc-api] 📷 Streamlit image decoded: {img.shape}")
                
                # 10프레임마다 상세 로그
                if frame_number % 10 == 0:
                    print(f"[webrtc-api] 📈 Streamlit milestone frame {frame_number} processed successfully")
                    
                    # 최근 추론 결과 확인
                    results = video_queue_service.get_recent_results(connection_id, 3)
                    if results:
                        latest = results[0]
                        print(f"[webrtc-api] 🧠 Latest inference: {latest.model_type} - "
                              f"{latest.results.get('face_count', 0)} faces detected")
            else:
                print(f"[webrtc-api] ⚠️ Failed to decode Streamlit image for frame {frame_number}")
        
        return True
        
    except Exception as e:
        print(f"[webrtc-api] ❌ Error processing Streamlit video frame: {e}")
        return False


@router.post("/connections/advanced", response_model=WebRTCResponse)
async def create_advanced_connection(
    offer: SDPOffer,
    webrtc_service: WebRTCService = Depends(get_webrtc_service)
):
    """
    새로운 ConnectionContext를 사용한 고급 연결 생성
    완전한 생명주기 관리와 에러 처리를 제공합니다.
    """
    connection_id = str(uuid.uuid4())
    print(f"[webrtc-api] Advanced connection offer received. connection_id={connection_id}")

    try:
        # ConnectionContext를 사용한 안전한 연결 생성
        context = await webrtc_service.connection_service.create_connection_context(offer, connection_id)
        answer = context.get_answer()
        
        # 연결 상태 정보
        stats = await webrtc_service.get_connection_stats(connection_id)
        
        return WebRTCResponse(
            success=True,
            message=f"Advanced WebRTC connection {connection_id} established with lifecycle management",
            data={
                "sdp": answer.sdp,
                "type": answer.type or "answer",
                "connection_id": connection_id,
                "status": "connected",
                "timestamp": time.time(),
                "lifecycle_managed": True,
                "connection_stats": stats,
                "media_info": {
                    "received_media_data": offer.mediaData is not None,
                    "received_session_info": offer.sessionInfo is not None,
                    "offer_size": len(offer.sdp),
                }
            },
            timestamp=time.time()
        )
        
    except Exception as e:
        print(f"[webrtc-api] ❌ Advanced connection creation failed: {e}")
        return WebRTCResponse(
            success=False,
            message=f"Failed to create advanced connection: {str(e)}",
            data={"connection_id": connection_id, "error": str(e)},
            timestamp=time.time()
        )


@router.get("/connections")
async def list_connections(webrtc_service: WebRTCService = Depends(get_webrtc_service)):
    """활성 연결 목록 조회"""
    connections = webrtc_service.list_active_connections()
    
    return {
        "success": True,
        "message": "Active connections retrieved",
        "data": {
            "connections": connections,
            "total_count": len(connections)
        },
        "timestamp": time.time()
    }


@router.delete("/connections/{connection_id}")
async def cleanup_connection(
    connection_id: str, 
    webrtc_service: WebRTCService = Depends(get_webrtc_service)
):
    """특정 연결 정리"""
    try:
        await webrtc_service.connection_service.cleanup_connection(connection_id)
        return {
            "success": True,
            "message": f"Connection {connection_id} cleaned up successfully",
            "timestamp": time.time()
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to cleanup connection: {str(e)}",
            "timestamp": time.time()
        }


@router.post("/data/{connection_id}")
async def send_data_to_connection(
    connection_id: str,
    data: dict,
    webrtc_service: WebRTCService = Depends(get_webrtc_service)
):
    """
    특정 연결에 데이터 전송 및 비즈니스 로직 실행
    """
    print(f"[webrtc-data] 📨 데이터 전송 요청: {connection_id}")
    print("성공")  # 요청받은 로그
    
    try:
        # 1. Connection 상태 확인
        connection_context = await webrtc_service.connection_service.get_connection_context(connection_id)
        if not connection_context:
            print(f"[webrtc-data] ❌ Connection {connection_id} 찾을 수 없음")
            return {
                "success": False,
                "message": f"Connection {connection_id} not found",
                "timestamp": time.time()
            }
        
        # 2. Connection 활성 상태 확인
        stats = await webrtc_service.connection_service.get_connection_stats(connection_id)
        connection_info = webrtc_service.connection_service._active_connections.get(connection_id)
        
        print(f"[webrtc-data] 🔍 디버깅 정보:")
        print(f"  - stats: {stats}")
        print(f"  - connection_info: {connection_info is not None}")
        print(f"  - active_connections 개수: {len(webrtc_service.connection_service._active_connections)}")
        print(f"  - active_connections keys: {list(webrtc_service.connection_service._active_connections.keys())}")
        
        # 연결이 존재하면 처리 허용 (상태에 관계없이)
        if not connection_info:
            print(f"[webrtc-data] ❌ Connection {connection_id} 비활성 상태")
            return {
                "success": False,
                "message": f"Connection {connection_id} is not active",
                "data": {"connection_state": stats.get('state') if stats else 'unknown'},
                "timestamp": time.time()
            }
        
        print(f"[webrtc-data] ✅ Connection {connection_id} 활성 확인됨")
        
        # 3. 비즈니스 로직 실행
        result = await process_connection_data(connection_id, data, connection_context)
        
        print(f"[webrtc-data] ✅ 비즈니스 로직 처리 완료: {connection_id}")
        
        return {
            "success": True,
            "message": f"Data processed successfully for connection {connection_id}",
            "data": {
                "connection_id": connection_id,
                "processed_data": result,
                "connection_stats": stats
            },
            "timestamp": time.time()
        }
        
    except Exception as e:
        print(f"[webrtc-data] ❌ 데이터 처리 실패: {connection_id} - {e}")
        return {
            "success": False,
            "message": f"Failed to process data: {str(e)}",
            "data": {"connection_id": connection_id, "error": str(e)},
            "timestamp": time.time()
        }


async def process_connection_data(connection_id: str, data: dict, context) -> dict:
    """
    Connection을 통한 데이터 처리 비즈니스 로직
    """
    print(f"[business-logic] 🎮 비즈니스 로직 시작: {connection_id}")
    
    # 예시 비즈니스 로직들
    processed_result = {
        "received_data": data,
        "processing_timestamp": time.time(),
        "connection_info": {
            "connection_id": connection_id,
            "uptime": context.service._active_connections.get(connection_id, {}).get('created_at'),
            "state": "processing"
        }
    }
    
    # 실제 데이터 처리 로직 (예시)
    if data.get("type") == "video_frame":
        print(f"[business-logic] 🎥 비디오 프레임 처리: {data.get('frame_number', 'unknown')}")
        processed_result["video_processed"] = True
        
    elif data.get("type") == "audio_data":
        print(f"[business-logic] 🔊 오디오 데이터 처리: {data.get('duration', 'unknown')}ms")
        processed_result["audio_processed"] = True
        
    elif data.get("type") == "control_message":
        print(f"[business-logic] 🎛️ 제어 메시지 처리: {data.get('command', 'unknown')}")
        processed_result["control_processed"] = True
        
    else:
        print(f"[business-logic] 📄 일반 데이터 처리: {len(str(data))} chars")
        processed_result["general_processed"] = True
    
    # 비동기 처리 시뮬레이션
    import asyncio
    await asyncio.sleep(0.1)  # 실제 처리 시간 시뮬레이션
    
    print(f"[business-logic] ✅ 비즈니스 로직 완료: {connection_id}")
    
    return processed_result

