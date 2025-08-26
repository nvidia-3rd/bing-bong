# audio_processor.py
import streamlit as st
from core.stream_orchestrator import StreamOrchestrator
from core.data_pipeline import DataPipeline
from infrastructure.metrics import ErrorBuf
from services.dual_audio_service import DualAudioService


class AudioProcessor:
    """
    오디오 처리 및 API 호출 관리
    - VAD 오디오 처리
    - 모델 추론 전송
    - 최적 오디오 선택
    """
    
    def __init__(
        self,
        orchestrator: StreamOrchestrator,
        data_pipeline: DataPipeline,
        dual_audio_service: DualAudioService,
        errbuf: ErrorBuf
    ):
        self.orchestrator = orchestrator
        self.data_pipeline = data_pipeline
        self.dual_audio_service = dual_audio_service
        self.errbuf = errbuf
        
    def on_orchestrator_state_change(self, orchestrator, event_type):
        """Orchestrator 상태 변화 처리"""
        print(f"[AudioProcessor] Orchestrator 상태 변화: {event_type}")
        
        if event_type == "created":
            # 새로운 orchestrator가 생성됨
            self.orchestrator = orchestrator
            print("[AudioProcessor] ✅ 새로운 Orchestrator 설정됨")
            
        elif event_type == "stopping":
            # orchestrator가 중지 중
            print("[AudioProcessor] ⏸️ Orchestrator 중지 중...")
            
        elif event_type == "stopped":
            # orchestrator가 완전히 중지됨
            self.orchestrator = None
            print("[AudioProcessor] 🛑 Orchestrator 정리 완료")
            
        else:
            print(f"[AudioProcessor] ⚠️ 알 수 없는 Orchestrator 이벤트: {event_type}")
        
    async def process_vad_audio(self, vad_assembler, audio_acc):
        """VAD 오디오 처리 루프"""
        try:
            print("[AudioProcessor] VAD 오디오 처리 시작")
            
            # VAD에서 오디오 블록 가져오기
            wav_bytes = await vad_assembler.get_block(timeout=0.5)
            if not wav_bytes:
                return None
                
            print(f"[AudioProcessor] VAD 오디오 블록 수신: {len(wav_bytes)} bytes")
            
            # AudioAccumulator 상태 확인
            if audio_acc:
                audio_info = audio_acc.get_audio_info()
                print(f"[AudioProcessor] AudioAccumulator 상태: {audio_info['wav_files_count']}개 WAV 파일, {audio_info['total_bytes']} bytes")
            
            # 오케스트레이터가 있으면 API 호출
            if self.orchestrator:
                session_id = getattr(self.orchestrator, 'session_id', 'unknown')
                try:
                    # 업로드 → 전사
                    resp = await self.orchestrator.audio_service.upload_audio(session_id, wav_bytes, self.errbuf)
                    tr = self.orchestrator.audio_service.extract_transcript(resp)
                    if tr:
                        self.orchestrator.emotion_service.add_audio_emotion(tr)
                        self.data_pipeline.safe_put(self.data_pipeline.transcript_q, tr)
                        print(f"[AudioProcessor] transcript: {tr[:80]}...")
                        return tr
                except Exception as e:
                    self.errbuf.push(f"VAD 오디오 처리 오류: {e}")
                    print(f"[AudioProcessor] 처리 오류: {e}")
                    
        except Exception as e:
            print(f"[AudioProcessor] VAD 오디오 처리 전체 오류: {e}")
            self.errbuf.push(f"VAD 오디오 처리 전체 오류: {e}")
            
        return None
        
    async def send_model_inference(self, session_id: str, wav_bytes: bytes, audio_info: dict):
        """모델 추론 전송"""
        try:
            print(f"[AudioProcessor] 🚀 모델 추론 전송 시작: {len(wav_bytes)} bytes")
            
            # 1. 오디오 업로드
            upload_response = await self.orchestrator.audio_service.upload_audio(session_id, wav_bytes, self.errbuf)
            if upload_response:
                print("[AudioProcessor] ✅ 오디오 업로드 완료")
                
                # 2. 전사 결과 추출
                transcript = self.orchestrator.audio_service.extract_transcript(upload_response)
                if transcript:
                    print(f"[AudioProcessor] 📝 전사 결과: {transcript[:100]}...")
                    
                    # 3. 감정 분석 추가
                    self.orchestrator.emotion_service.add_audio_emotion(transcript)
                    self.data_pipeline.safe_put(self.data_pipeline.transcript_q, transcript)
                    
                    # 4. 추가 분석 결과 표시
                    print(f"[AudioProcessor] 🔍 분석 완료: 세션 {session_id}, 길이 {audio_info.get('total_duration', 0):.1f}초")
                    
                    return {
                        'success': True,
                        'transcript': transcript,
                        'audio_info': audio_info
                    }
                else:
                    print("[AudioProcessor] ⚠️ 전사 결과를 가져올 수 없습니다")
                    return {'success': False, 'error': '전사 결과 없음'}
                    
            else:
                print("[AudioProcessor] ❌ 오디오 업로드 실패")
                return {'success': False, 'error': '업로드 실패'}
                
        except Exception as e:
            print(f"[AudioProcessor] ❌ 모델 추론 전송 실패: {e}")
            self.errbuf.push(f"모델 추론 전송 실패: {e}")
            return {'success': False, 'error': str(e)}
            
    def select_best_audio(self, webrtc_wav_bytes: bytes) -> tuple[bytes, str]:
        """최적 오디오 선택 (PyAudio 우선)"""
        inference_audio_bytes = webrtc_wav_bytes
        inference_audio_source = "WebRTC"
        
        # PyAudio 고품질 오디오가 있으면 우선 사용
        if (self.dual_audio_service and 
            self.dual_audio_service.is_pyaudio_active):
            try:
                pyaudio_wav_bytes = self.dual_audio_service.build_pyaudio_wav_bytes()
                if pyaudio_wav_bytes and len(pyaudio_wav_bytes) > 1000:  # 최소 크기 체크
                    inference_audio_bytes = pyaudio_wav_bytes
                    inference_audio_source = "PyAudio (고품질)"
                    print(f"[AudioProcessor] 🎤 PyAudio 고품질 오디오 선택 ({len(pyaudio_wav_bytes)} bytes)")
                else:
                    print("[AudioProcessor] ⚠️ PyAudio 오디오가 너무 작아서 WebRTC 사용")
            except Exception as e:
                print(f"[AudioProcessor] PyAudio 오디오 선택 실패, WebRTC 사용: {e}")
        else:
            print(f"[AudioProcessor] ℹ️ WebRTC 오디오 사용 ({len(webrtc_wav_bytes)} bytes)")
            
        return inference_audio_bytes, inference_audio_source
        
    def get_audio_processing_stats(self):
        """오디오 처리 통계 반환"""
        return {
            'orchestrator_exists': self.orchestrator is not None,
            'dual_audio_service_exists': self.dual_audio_service is not None,
            'pyaudio_active': self.dual_audio_service.is_pyaudio_active if self.dual_audio_service else False
        }
