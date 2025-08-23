"""
Audio Processing API Routes
"""
import os
import time
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/fetch", tags=["audio"])

# 오디오 파일 저장 디렉토리
AUDIO_DIR = Path("static/audio")
AUDIO_DIR.mkdir(exist_ok=True, parents=True)

@router.get("/audio/status")
async def get_audio_status():
    """
    오디오 서비스 상태 조회
    
    Returns:
        오디오 서비스 상태 및 통계 정보
    """
    try:
        # 오디오 디렉토리 정보 수집
        audio_files = list(AUDIO_DIR.glob("*.webm"))
        total_size = sum(f.stat().st_size for f in audio_files if f.is_file())
        
        # 최근 파일 정보
        recent_files = []
        for file_path in sorted(audio_files, key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
            if file_path.is_file():
                stat = file_path.stat()
                recent_files.append({
                    "filename": file_path.name,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "modified_at": stat.st_mtime
                })
        
        return {
            "success": True,
            "message": "오디오 서비스 상태 조회 완료",
            "data": {
                "service_type": "audio_recording",
                "status": "running",
                "audio_directory": str(AUDIO_DIR),
                "total_files": len(audio_files),
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "recent_files": recent_files,
                "max_file_size_mb": 10,
                "supported_formats": ["webm", "opus"]
            },
            "timestamp": time.time()
        }
        
    except Exception as e:
        print(f"❌ [audio-api] 오디오 상태 조회 오류: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"오디오 서비스 상태 조회 중 오류가 발생했습니다: {str(e)}"
        )

@router.post("/audio")
async def upload_audio(
    audio: UploadFile = File(..., description="오디오 파일"),
    timestamp: Optional[str] = Form(None, description="타임스탬프"),
    duration: Optional[str] = Form(None, description="녹음 시간 (밀리초)")
):
    """
    오디오 파일 업로드 및 처리
    
    Args:
        audio: WebM/Opus 오디오 파일
        timestamp: 녹음 시작 타임스탬프
        duration: 녹음 지속 시간 (밀리초)
    
    Returns:
        업로드 결과 및 파일 정보
    """
    try:
        # 파일 유효성 검사
        if not audio.filename:
            raise HTTPException(status_code=400, detail="파일명이 없습니다")
        
        if not audio.content_type or not audio.content_type.startswith('audio/'):
            raise HTTPException(status_code=400, detail="오디오 파일이 아닙니다")
        
        # 파일 크기 제한 (10MB)
        MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
        if audio.size and audio.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="파일 크기가 너무 큽니다 (최대 10MB)")
        
        # 파일 저장
        timestamp_str = timestamp or str(int(time.time() * 1000))
        duration_str = duration or "unknown"
        
        # 안전한 파일명 생성
        safe_filename = f"audio_{timestamp_str}_{duration_str}ms.webm"
        file_path = AUDIO_DIR / safe_filename
        
        # 파일 저장
        with open(file_path, "wb") as buffer:
            content = await audio.read()
            buffer.write(content)
        
        # 파일 정보 수집
        file_size = len(content)
        file_size_mb = round(file_size / (1024 * 1024), 2)
        
        print(f"🎵 [audio-api] 오디오 파일 업로드 성공:")
        print(f"   - 파일명: {safe_filename}")
        print(f"   - 크기: {file_size_mb}MB ({file_size} bytes)")
        print(f"   - 타입: {audio.content_type}")
        print(f"   - 타임스탬프: {timestamp_str}")
        print(f"   - 녹음 시간: {duration_str}ms")
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "오디오 파일 업로드 성공",
                "data": {
                    "filename": safe_filename,
                    "file_path": str(file_path),
                    "file_size_bytes": file_size,
                    "file_size_mb": file_size_mb,
                    "content_type": audio.content_type,
                    "timestamp": timestamp_str,
                    "duration_ms": duration_str,
                    "upload_time": time.time()
                },
                "timestamp": time.time()
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [audio-api] 오디오 업로드 오류: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"오디오 파일 처리 중 오류가 발생했습니다: {str(e)}"
        )

@router.get("/audio/files")
async def list_audio_files():
    """
    업로드된 오디오 파일 목록 조회
    
    Returns:
        오디오 파일 목록 및 통계 정보
    """
    try:
        audio_files = []
        total_size = 0
        
        for file_path in AUDIO_DIR.glob("*.webm"):
            if file_path.is_file():
                file_stat = file_path.stat()
                file_size = file_stat.st_size
                total_size += file_size
                
                audio_files.append({
                    "filename": file_path.name,
                    "path": str(file_path),
                    "url": f"/static/audio/{file_path.name}",
                    "size_bytes": file_size,
                    "size_mb": round(file_size / (1024 * 1024), 2),
                    "created_at": file_stat.st_ctime,
                    "modified_at": file_stat.st_mtime
                })
        
        # 생성 시간 기준 정렬 (최신순)
        audio_files.sort(key=lambda x: x['created_at'], reverse=True)
        
        return {
            "success": True,
            "message": f"총 {len(audio_files)}개의 오디오 파일을 찾았습니다",
            "data": {
                "files": audio_files,
                "total_count": len(audio_files),
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "directory": str(AUDIO_DIR)
            },
            "timestamp": time.time()
        }
        
    except Exception as e:
        print(f"❌ [audio-api] 오디오 파일 목록 조회 오류: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"오디오 파일 목록 조회 중 오류가 발생했습니다: {str(e)}"
        )

@router.delete("/audio/files/{filename}")
async def delete_audio_file(filename: str):
    """
    특정 오디오 파일 삭제
    
    Args:
        filename: 삭제할 파일명
    
    Returns:
        삭제 결과
    """
    try:
        file_path = AUDIO_DIR / filename
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
        
        if not file_path.is_file():
            raise HTTPException(status_code=400, detail="파일이 아닙니다")
        
        # 파일 삭제
        file_path.unlink()
        
        print(f"🗑️ [audio-api] 오디오 파일 삭제: {filename}")
        
        return {
            "success": True,
            "message": f"오디오 파일 '{filename}' 삭제 완료",
            "data": {
                "deleted_filename": filename,
                "deleted_at": time.time()
            },
            "timestamp": time.time()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [audio-api] 오디오 파일 삭제 오류: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"오디오 파일 삭제 중 오류가 발생했습니다: {str(e)}"
        )
