// DOM 요소들
const localVideo = document.getElementById("localVideo");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const recordBtn = document.getElementById("recordBtn");
const stopRecordBtn = document.getElementById("stopRecordBtn");
const streamStatus = document.getElementById("streamStatus");
const frameCounter = document.getElementById("frameCounter");
const connectionStatus = document.getElementById("connectionStatus");
const sentFrames = document.getElementById("sentFrames");
const frameRate = document.getElementById("frameRate");
const connectionTime = document.getElementById("connectionTime");

// 상태 변수들
let localStream = null;
let pc = null;
let isStreaming = false;
let connectionStartTime = null;
let frameCount = 0;
let lastFrameCount = 0;
let statsInterval = null;
let timeInterval = null;

// 오디오 분석 관련 변수들
let audioContext = null;
let audioAnalyser = null;
let microphone = null;
let audioDataArray = null;
let audioRecorder = null;
let isRecordingAudio = false;
let isManualRecording = false;  // 수동 녹음 상태
let recordingStartTime = null;  // 녹음 시작 시간

// 실시간 스트리밍 시작
startBtn.onclick = async () => {
  try {
    updateStreamStatus("카메라 접근 중...");

    // 고품질 웹캠 설정
    const constraints = {
      video: {
        width: { ideal: 1280, min: 640 },
        height: { ideal: 720, min: 480 },
        frameRate: { ideal: 30, min: 15 },
        facingMode: 'user'
      }, audio: {
        channelCount: { ideal: 1 },   // STT/분석이면 모노 권장
        sampleRate: 48000,            // 브라우저/디바이스에 따라 무시될 수 있음
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    };

    localStream = await navigator.mediaDevices.getUserMedia(constraints);
    localVideo.srcObject = localStream;

    // 오디오 분석 초기화
    await initAudioAnalysis();

    // WebRTC 연결 자동 시작
    await startWebRTCConnection();

  } catch (error) {
    console.error("스트리밍 시작 실패:", error);
    updateStreamStatus("카메라 접근 실패");
    alert("카메라 접근 실패: " + error.message);
  }
};

// WebRTC 연결 시작
async function startWebRTCConnection() {
  try {
    updateStreamStatus("WebRTC 연결 중...");
    updateConnectionStatus("연결 중...");

    // RTCPeerConnection 생성 (서버와 동일한 STUN 설정)
    pc = new RTCPeerConnection({
      iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:stun1.l.google.com:19302' }
      ],
      iceCandidatePoolSize: 10
    });

    // 연결 상태 모니터링 강화
    pc.onconnectionstatechange = () => {
      console.log(`🔗 Connection state: ${pc.connectionState}`);
      updateConnectionStatus(pc.connectionState);

      if (pc.connectionState === 'connected') {
        console.log("✅ WebRTC 연결 완료! 실시간 모니터링 시작");
        startRealTimeMonitoring();
        // startAudioCapture(); // 자동 녹음 제거
        // 녹음 버튼 활성화
        recordBtn.disabled = false;
        console.log("🎵 녹음 버튼 활성화됨");
      } else if (pc.connectionState === 'disconnected' || pc.connectionState === 'failed') {
        console.log("❌ WebRTC 연결 실패 또는 끊김");
        stopRealTimeMonitoring();
        stopAudioCapture();
        // 녹음 버튼 비활성화
        recordBtn.disabled = true;
        stopRecordBtn.disabled = true;
      }
    };

    pc.oniceconnectionstatechange = () => {
      console.log(`🧊 ICE connection state: ${pc.iceConnectionState}`);
      if (pc.iceConnectionState === 'failed') {
        console.error("❌ ICE 연결 실패 - STUN 서버 또는 네트워크 문제");
      }
      
      // ICE 연결 완료 시 녹음 버튼 활성화 시도
      if (pc.iceConnectionState === 'completed' && pc.connectionState === 'connected') {
        console.log("🎵 ICE 연결 완료 - 녹음 버튼 활성화 시도");
        recordBtn.disabled = false;
        console.log("🎵 녹음 버튼 활성화됨 (ICE 완료)");
      }
    };

    pc.onicegatheringstatechange = () => {
      console.log(`🔍 ICE gathering state: ${pc.iceGatheringState}`);
    };

    pc.onicecandidate = (event) => {
      if (event.candidate) {
        console.log(`🎯 ICE candidate: ${event.candidate.candidate}`);
      } else {
        console.log("🏁 ICE candidate gathering 완료");
      }
    };

    // 로컬 스트림 추가
    localStream.getTracks().forEach(track => {
      pc.addTrack(track, localStream);
      console.log(`Track added: ${track.kind} - ${track.label}`);
    });

    // Offer 생성 (서버가 수신 전용이므로 클라이언트는 송신 전용)
    const offer = await pc.createOffer();

    await pc.setLocalDescription(offer);
    console.log("🎯 Generated Offer:", offer);

    // ICE 수집 완료까지 대기 (참조 글 모범 사례)
    if (pc.iceGatheringState !== 'complete') {
      console.log("⏳ ICE candidate 수집 중...");
      await new Promise((resolve) => {
        pc.onicegatheringstatechange = () => {
          if (pc.iceGatheringState === 'complete') {
            console.log("✅ ICE candidate 수집 완료!");
            resolve();
          }
        };
      });
    }

    // 서버에 Offer 전송
    const response = await fetch("/api/webrtc/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sdp: offer.sdp,
        type: offer.type,
        mediaData: {
          timestamp: Date.now(),
          streamType: "realtime_webcam",
          video: {
            width: localVideo.videoWidth || 640,
            height: localVideo.videoHeight || 480,
            frameRate: 30
          },
          audio: await getAudioMetadata(),
          quality: "high",
          continuous: true
        },
        sessionInfo: {
          userAgent: navigator.userAgent,
          platform: navigator.platform,
          connectionId: generateConnectionId(),
          sessionStart: Date.now()
        }
      })
    });

    const result = await response.json();
    console.log("Server Response:", result);

    if (result.success && result.data) {
      // Answer 설정
      const answer = new RTCSessionDescription({
        sdp: result.data.sdp,
        type: result.data.type
      });

      await pc.setRemoteDescription(answer);
      console.log("WebRTC 연결 성공!");

      // 연결 성공 상태 업데이트
      isStreaming = true;
      connectionStartTime = Date.now();
      updateStreamStatus("실시간 스트리밍 중");
      updateConnectionStatus("연결됨");

      // UI 상태 업데이트
      startBtn.disabled = true;
      stopBtn.disabled = false;
      streamStatus.classList.add('streaming');
      frameCounter.classList.add('active');
      
      // 녹음 버튼 초기 상태 설정
      recordBtn.disabled = false;
      stopRecordBtn.disabled = true;
      recordBtn.classList.remove('recording');
      recordBtn.textContent = '🎵 녹음 시작';
      
      // WebRTC 연결 상태 확인 및 녹음 버튼 활성화
      setTimeout(() => {
        if (pc && pc.connectionState === 'connected') {
          console.log("🎵 WebRTC 연결 확인됨 - 녹음 버튼 최종 활성화");
          recordBtn.disabled = false;
          console.log("🎵 녹음 버튼 최종 활성화됨");
        } else {
          console.log("⚠️ WebRTC 연결 상태 확인 중...");
          console.log(`   - connectionState: ${pc?.connectionState}`);
          console.log(`   - iceConnectionState: ${pc?.iceConnectionState}`);
        }
      }, 1000); // 1초 후 확인

    } else {
      throw new Error("서버 응답 오류: " + result.message);
    }

  } catch (error) {
    console.error("WebRTC 연결 실패:", error);
    updateStreamStatus("연결 실패");
    updateConnectionStatus("실패");
    alert("WebRTC 연결 실패: " + error.message);
  }
}

// 상태 업데이트 함수들
function updateStreamStatus(status) {
  streamStatus.textContent = status;
}

function updateConnectionStatus(status) {
  connectionStatus.textContent = status;
}

function generateConnectionId() {
  return 'conn_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

// 실제 오디오 스트림에서 메타데이터 추출
async function getAudioMetadata() {
  const audioMeta = {
    sampleRate: 48000,
    channelCount: 1,
    codec: "opus",
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true
  };

  try {
    // 실제 오디오 트랙에서 설정 확인
    if (localStream) {
      const audioTracks = localStream.getAudioTracks();
      if (audioTracks.length > 0) {
        const audioTrack = audioTracks[0];
        const settings = audioTrack.getSettings();
        const constraints = audioTrack.getConstraints();
        
        console.log("🎵 [AudioMeta 추출] 실제 오디오 트랙 설정:", settings);
        console.log("🎵 [AudioMeta 추출] 오디오 트랙 제약사항:", constraints);
        
        // 실제 설정값으로 업데이트
        if (settings.sampleRate) audioMeta.sampleRate = settings.sampleRate;
        if (settings.channelCount) audioMeta.channelCount = settings.channelCount;
        if (settings.echoCancellation !== undefined) audioMeta.echoCancellation = settings.echoCancellation;
        if (settings.noiseSuppression !== undefined) audioMeta.noiseSuppression = settings.noiseSuppression;
        if (settings.autoGainControl !== undefined) audioMeta.autoGainControl = settings.autoGainControl;
        
        console.log("✅ [AudioMeta 추출] 최종 AudioMeta:", audioMeta);
      } else {
        console.warn("⚠️ [AudioMeta 추출] 오디오 트랙을 찾을 수 없음");
      }
    }
  } catch (error) {
    console.error("❌ [AudioMeta 추출] 오디오 메타데이터 추출 실패:", error);
  }

  return audioMeta;
}

// 오디오 분석 초기화 (Web Audio API 사용)
async function initAudioAnalysis() {
  try {
    console.log("🎵 오디오 분석 초기화 시작...");
    
    if (!localStream) {
      throw new Error("로컬 스트림이 없습니다");
    }

    const audioTracks = localStream.getAudioTracks();
    if (audioTracks.length === 0) {
      throw new Error("오디오 트랙이 없습니다");
    }

    // AudioContext 생성
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    
    // MediaStream을 AudioContext에 연결
    microphone = audioContext.createMediaStreamSource(localStream);
    
    // AnalyserNode 생성 (실시간 오디오 분석용)
    audioAnalyser = audioContext.createAnalyser();
    audioAnalyser.fftSize = 2048;
    audioAnalyser.smoothingTimeConstant = 0.8;
    
    // 오디오 데이터 배열 초기화
    audioDataArray = new Uint8Array(audioAnalyser.frequencyBinCount);
    
    // 마이크를 분석기에 연결
    microphone.connect(audioAnalyser);
    
    // MediaRecorder 설정 (실제 오디오 데이터 캡처용)
    const audioStream = new MediaStream([audioTracks[0]]);
    audioRecorder = new MediaRecorder(audioStream, {
      mimeType: 'audio/webm;codecs=opus'
    });
    
    let audioChunks = [];
    
    audioRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        audioChunks.push(event.data);
        console.log(`🎵 오디오 청크 수집: ${event.data.size} bytes`);
      }
    };
    
    audioRecorder.onstop = async () => {
      if (audioChunks.length > 0) {
        const audioBlob = new Blob(audioChunks, { type: 'audio/webm;codecs=opus' });
        
        // 녹음 시간 계산
        let duration = 5000; // 기본값
        if (recordingStartTime) {
          duration = Date.now() - recordingStartTime;
        }
        
        // 파일명에 녹음 시간 포함
        const timestamp = Date.now();
        const durationSec = Math.round(duration / 1000);
        const filename = `audio_${timestamp}_${durationSec}s.webm`;
        
        console.log(`🎵 녹음 완료: ${durationSec}초, 파일: ${filename}`);
        
        // FormData 생성 및 전송
        const formData = new FormData();
        formData.append('audio', audioBlob, filename);
        formData.append('timestamp', timestamp.toString());
        formData.append('duration', duration.toString());
        
        // /fetch/audio 엔드포인트로 전송
        try {
          const response = await fetch('/fetch/audio', {
            method: 'POST',
            body: formData
          });
          
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
          }
          
          const result = await response.json();
          console.log("✅ 오디오 데이터 전송 성공:", result);
          
        } catch (error) {
          console.error("❌ 오디오 데이터 전송 실패:", error);
        }
        
        audioChunks = [];
      }
    };
    
    console.log("✅ 오디오 분석 초기화 완료");
    console.log(`- 샘플레이트: ${audioContext.sampleRate}Hz`);
    console.log(`- 분석기 FFT 크기: ${audioAnalyser.fftSize}`);
    console.log(`- 주파수 빈 수: ${audioAnalyser.frequencyBinCount}`);
    
  } catch (error) {
    console.error("❌ 오디오 분석 초기화 실패:", error);
    throw error;
  }
}



// 실시간 모니터링 시작
function startRealTimeMonitoring() {
  // 통계 업데이트 (1초마다)
  statsInterval = setInterval(async () => {
    if (!pc || pc.connectionState !== 'connected') {
      stopRealTimeMonitoring();
      return;
    }

    try {
      const stats = await pc.getStats();
      let currentFrameCount = 0;
      let bytesSent = 0;

      stats.forEach(report => {
        if (report.type === 'outbound-rtp' && report.mediaType === 'video') {
          currentFrameCount = report.framesSent || 0;
          bytesSent = report.bytesSent || 0;
        }
      });

      // FPS 계산
      const fps = currentFrameCount - lastFrameCount;
      lastFrameCount = currentFrameCount;

      // UI 업데이트
      frameCounter.textContent = `프레임: ${currentFrameCount}`;
      sentFrames.textContent = currentFrameCount.toLocaleString();
      frameRate.textContent = `${fps} FPS`;

      console.log(`📊 실시간 통계 - 프레임: ${currentFrameCount}, FPS: ${fps}, 전송: ${(bytesSent / 1024 / 1024).toFixed(2)}MB`);

    } catch (error) {
      console.error("Stats 조회 실패:", error);
    }
  }, 1000);

  // 연결 시간 업데이트 (1초마다)
  timeInterval = setInterval(() => {
    if (!connectionStartTime) return;

    const elapsed = Math.floor((Date.now() - connectionStartTime) / 1000);
    const minutes = Math.floor(elapsed / 60).toString().padStart(2, '0');
    const seconds = (elapsed % 60).toString().padStart(2, '0');

    connectionTime.textContent = `${minutes}:${seconds}`;
  }, 1000);
}

// 실시간 모니터링 중지
function stopRealTimeMonitoring() {
  if (statsInterval) {
    clearInterval(statsInterval);
    statsInterval = null;
  }

  if (timeInterval) {
    clearInterval(timeInterval);
    timeInterval = null;
  }
}

// 스트리밍 중지
stopBtn.onclick = () => {
  console.log("🛑 스트리밍 중지 요청");

  // 모니터링 중지
  stopRealTimeMonitoring();

  // WebRTC 연결 종료
  if (pc) {
    pc.close();
    pc = null;
    console.log("WebRTC 연결 종료됨");
  }

  // 스트림 정지
  if (localStream) {
    localStream.getTracks().forEach(track => {
      track.stop();
      console.log(`Track stopped: ${track.kind}`);
    });
    localStream = null;
  }

  // 비디오 정리
  localVideo.srcObject = null;

  // 상태 초기화
  isStreaming = false;
  connectionStartTime = null;
  frameCount = 0;
  lastFrameCount = 0;

  // UI 상태 초기화
  updateStreamStatus("대기 중");
  updateConnectionStatus("대기 중");
  frameCounter.textContent = "프레임: 0";
  sentFrames.textContent = "0";
  frameRate.textContent = "0 FPS";
  connectionTime.textContent = "00:00";

  // 버튼 상태 초기화
  startBtn.disabled = false;
  stopBtn.disabled = true;
  recordBtn.disabled = true;
  stopRecordBtn.disabled = true;

  // 애니메이션 클래스 제거
  streamStatus.classList.remove('streaming');
  frameCounter.classList.remove('active');

  console.log("✅ 스트리밍 완전히 중지됨");
};


// 녹음 버튼 상태 디버깅 함수
function debugRecordButtonState() {
  console.log("🔍 녹음 버튼 상태 디버깅:");
  console.log(`   - recordBtn.disabled: ${recordBtn.disabled}`);
  console.log(`   - stopRecordBtn.disabled: ${stopRecordBtn.disabled}`);
  console.log(`   - WebRTC 연결 상태: ${pc?.connectionState || 'N/A'}`);
  console.log(`   - ICE 연결 상태: ${pc?.iceConnectionState || 'N/A'}`);
  console.log(`   - 로컬 스트림: ${localStream ? '있음' : '없음'}`);
  console.log(`   - 오디오 트랙: ${localStream?.getAudioTracks().length || 0}개`);
}

// 전역 함수로 노출 (브라우저 콘솔에서 호출 가능)
window.debugRecordButton = debugRecordButtonState;

// 테스트 관련 변수들
let currentTestConnectionId = null;

// 테스트 결과 출력 함수
function updateTestResults(message, type = 'info') {
  const testResults = document.getElementById('testResults');
  const timestamp = new Date().toLocaleTimeString();
  const typeIcon = type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️';
  
  testResults.textContent += `[${timestamp}] ${typeIcon} ${message}\n`;
  testResults.scrollTop = testResults.scrollHeight;
}

// ConnectionContext 테스트 버튼 이벤트 리스너들
document.addEventListener('DOMContentLoaded', () => {
  const createTestConnectionBtn = document.getElementById('createTestConnectionBtn');
  const cleanupTestConnectionBtn = document.getElementById('cleanupTestConnectionBtn');
  const statusTestConnectionBtn = document.getElementById('statusTestConnectionBtn');
  const cleanupAllConnectionsBtn = document.getElementById('cleanupAllConnectionsBtn');
  
  // 데이터 전송 버튼들
  const sendVideoDataBtn = document.getElementById('sendVideoDataBtn');
  const sendAudioDataBtn = document.getElementById('sendAudioDataBtn');
  const sendControlDataBtn = document.getElementById('sendControlDataBtn');
  const sendCustomDataBtn = document.getElementById('sendCustomDataBtn');

  // 테스트 연결 생성
  createTestConnectionBtn.onclick = async () => {
    updateTestResults('테스트 연결 생성을 시작합니다...');
    updateTestResults('실제 WebRTC Offer를 생성합니다...');
    
    try {
      // 실제 WebRTC PeerConnection 생성
      const testPc = new RTCPeerConnection({
        iceServers: [
          { urls: 'stun:stun.l.google.com:19302' },
          { urls: 'stun:stun1.l.google.com:19302' }
        ]
      });

      // 실제 미디어 스트림 획득 시도
      let testStream;
      try {
        updateTestResults('웹캠/마이크 접근 시도 중...');
        testStream = await navigator.mediaDevices.getUserMedia({
          video: { width: 640, height: 480, frameRate: 30 },
          audio: { sampleRate: 48000, channelCount: 1 }
        });
        updateTestResults('✅ 실제 미디어 스트림 획득 성공');
      } catch (e) {
        updateTestResults('⚠️ 미디어 접근 실패, Canvas 스트림 생성');
        // Canvas로 비디오 스트림 생성
        const canvas = document.createElement('canvas');
        canvas.width = 640;
        canvas.height = 480;
        const ctx = canvas.getContext('2d');
        
        // 애니메이션 효과
        let frame = 0;
        const drawFrame = () => {
          ctx.fillStyle = `hsl(${frame % 360}, 70%, 50%)`;
          ctx.fillRect(0, 0, 640, 480);
          ctx.fillStyle = 'white';
          ctx.font = 'bold 40px Arial';
          ctx.textAlign = 'center';
          ctx.fillText('REAL TEST', 320, 200);
          ctx.fillText(`Frame: ${frame}`, 320, 280);
          frame++;
        };
        
        drawFrame();
        setInterval(drawFrame, 33);
        testStream = canvas.captureStream(30);
        
        // Web Audio API로 실제 오디오 톤 생성
        try {
          const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
          const oscillator = audioCtx.createOscillator();
          const gain = audioCtx.createGain();
          const dest = audioCtx.createMediaStreamDestination();
          
          oscillator.frequency.setValueAtTime(440, audioCtx.currentTime);
          gain.gain.setValueAtTime(0.1, audioCtx.currentTime);
          
          oscillator.connect(gain);
          gain.connect(dest);
          oscillator.start();
          
          testStream.addTrack(dest.stream.getAudioTracks()[0]);
          updateTestResults('✅ 실제 오디오 톤 생성 완료');
        } catch (audioErr) {
          updateTestResults('⚠️ 오디오 생성 실패');
        }
      }

      // 트랙을 PeerConnection에 추가
      testStream.getTracks().forEach(track => {
        testPc.addTrack(track, testStream);
        updateTestResults(`✅ 트랙 추가: ${track.kind}`);
      });

      // 실제 WebRTC Offer 생성
      updateTestResults('WebRTC Offer 생성 중...');
      const offer = await testPc.createOffer();
      await testPc.setLocalDescription(offer);
      updateTestResults('✅ setLocalDescription 완료');

      // ICE candidate 수집 대기
      updateTestResults('ICE candidate 수집 중...');
      await new Promise((resolve) => {
        const timeout = setTimeout(resolve, 3000);
        testPc.onicegatheringstatechange = () => {
          if (testPc.iceGatheringState === 'complete') {
            clearTimeout(timeout);
            updateTestResults('✅ ICE candidate 수집 완료!');
            resolve();
          }
        };
      });

      const finalOffer = testPc.localDescription;
      updateTestResults(`SDP 크기: ${finalOffer.sdp.length} chars`);

      // 실제 WebRTC 데이터로 요청 객체 구성
      const realOffer = {
        sdp: finalOffer.sdp,
        type: finalOffer.type,
        mediaData: {
          timestamp: Date.now(),
          streamType: "realtime_webcam",
          video: { width: 640, height: 480, frameRate: 30 },
          audio: { sampleRate: 48000, channelCount: 1 },
          quality: "high",
          continuous: true
        },
        sessionInfo: {
          userAgent: navigator.userAgent,
          platform: navigator.platform,
          connectionId: `real-${Date.now()}`,
          sessionStart: Date.now()
        }
      };

      updateTestResults('서버로 실제 WebRTC Offer 전송 중...');
      
      const response = await fetch('/api/connection/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(realOffer)
      });

      // 테스트용 리소스 정리
      testStream.getTracks().forEach(track => track.stop());
      testPc.close();
      updateTestResults('✅ 테스트 리소스 정리 완료');
      
      const result = await response.json();
      
      if (result.success) {
        currentTestConnectionId = result.data.connection_id;
        updateTestResults(`테스트 연결 생성 성공: ${currentTestConnectionId}`, 'success');
        updateTestResults(`활성 연결 수: ${result.data.active_connections_count}개`);
        
        // 정리 버튼 활성화
        cleanupTestConnectionBtn.disabled = false;
        
        // 데이터 전송 버튼들 활성화
        sendVideoDataBtn.disabled = false;
        sendAudioDataBtn.disabled = false;
        sendControlDataBtn.disabled = false;
        sendCustomDataBtn.disabled = false;
        
        // 연결 상태 표시
        if (result.data.connection_stats) {
          updateTestResults(`연결 상태: ${result.data.connection_stats.state}`);
          updateTestResults(`ICE 상태: ${result.data.connection_stats.ice_state}`);
        }
        
      } else {
        updateTestResults(`테스트 연결 생성 실패: ${result.message}`, 'error');
      }
      
    } catch (error) {
      updateTestResults(`API 호출 실패: ${error.message}`, 'error');
    }
  };

  // 테스트 연결 정리
  cleanupTestConnectionBtn.onclick = async () => {
    if (!currentTestConnectionId) {
      updateTestResults('정리할 연결이 없습니다.', 'error');
      return;
    }
    
    updateTestResults(`테스트 연결 정리를 시작합니다: ${currentTestConnectionId}`);
    
    try {
      const response = await fetch(`/api/connection/${currentTestConnectionId}`, {
        method: 'DELETE'
      });
      
      const result = await response.json();
      
      if (result.success) {
        updateTestResults('테스트 연결 정리 성공!', 'success');
        updateTestResults(`연결 수 변화: ${result.data.before_cleanup.active_connections_count} → ${result.data.after_cleanup.active_connections_count}`);
        updateTestResults(`생명주기 검증: ${result.data.lifecycle_verified ? '성공' : '실패'}`);
        
        // 상태 초기화
        currentTestConnectionId = null;
        cleanupTestConnectionBtn.disabled = true;
        
      } else {
        updateTestResults(`테스트 연결 정리 실패: ${result.message}`, 'error');
      }
      
    } catch (error) {
      updateTestResults(`API 호출 실패: ${error.message}`, 'error');
    }
  };

  // 연결 상태 조회
  statusTestConnectionBtn.onclick = async () => {
    updateTestResults('연결 상태를 조회하고 있습니다...');
    
    try {
      const response = await fetch('/api/connection/status');
      const result = await response.json();
      
      if (result.success) {
        updateTestResults(`총 활성 연결 수: ${result.data.total_connections}개`, 'success');
        updateTestResults(`연결 풀 상태: ${result.data.pool_status}`);
        
        // 각 연결의 상세 정보
        Object.entries(result.data.connections).forEach(([connId, connData]) => {
          updateTestResults(`연결 ${connId.substring(0, 8)}...: ${connData.basic_info.state}`);
          if (connData.detailed_stats) {
            updateTestResults(`  - 업타임: ${connData.detailed_stats.uptime?.toFixed(1)}초`);
            updateTestResults(`  - 태스크: ${connData.detailed_stats.tasks_count}개`);
          }
        });
        
      } else {
        updateTestResults(`상태 조회 실패: ${result.message}`, 'error');
      }
      
    } catch (error) {
      updateTestResults(`API 호출 실패: ${error.message}`, 'error');
    }
  };

  // 모든 연결 정리
  cleanupAllConnectionsBtn.onclick = async () => {
    if (!confirm('모든 연결을 정리하시겠습니까?')) {
      return;
    }
    
    updateTestResults('모든 연결을 정리하고 있습니다...');
    
    try {
      const response = await fetch('/api/connection/all', {
        method: 'DELETE'
      });
      
      const result = await response.json();
      
      if (result.success) {
        updateTestResults('모든 연결 정리 성공!', 'success');
        updateTestResults(`정리된 연결 수: ${result.data?.connections_cleared || 0}개`);
        updateTestResults(`정리 후 활성 연결: ${result.data?.after_cleanup?.total_connections || 0}개`);
        
        // 상태 초기화
        currentTestConnectionId = null;
        cleanupTestConnectionBtn.disabled = true;
        
      } else {
        updateTestResults(`모든 연결 정리 실패: ${result.message}`, 'error');
      }
      
    } catch (error) {
      updateTestResults(`API 호출 실패: ${error.message}`, 'error');
    }
  };

  // 비디오 데이터 전송
  sendVideoDataBtn.onclick = async () => {
    if (!currentTestConnectionId) {
      updateTestResults('활성 연결이 없습니다.', 'error');
      return;
    }
    
    updateTestResults('비디오 데이터를 전송합니다...');
    
    try {
      const videoData = {
        type: "video_frame",
        frame_number: Math.floor(Math.random() * 1000),
        timestamp: Date.now(),
        width: 640,
        height: 480,
        format: "RGB24",
        size_bytes: 921600
      };
      
      const response = await fetch(`/api/webrtc/data/${currentTestConnectionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(videoData)
      });
      
      const result = await response.json();
      
      if (result.success) {
        updateTestResults('✅ 비디오 데이터 전송 성공!', 'success');
        updateTestResults(`처리 시간: ${result.data.processed_data.processing_timestamp}`);
      } else {
        updateTestResults(`❌ 비디오 데이터 전송 실패: ${result.message}`, 'error');
      }
      
    } catch (error) {
      updateTestResults(`❌ API 호출 실패: ${error.message}`, 'error');
    }
  };

  // 오디오 데이터 전송
  sendAudioDataBtn.onclick = async () => {
    if (!currentTestConnectionId) {
      updateTestResults('활성 연결이 없습니다.', 'error');
      return;
    }
    
    updateTestResults('오디오 데이터를 전송합니다...');
    
    try {
      const audioData = {
        type: "audio_data",
        duration: Math.floor(Math.random() * 5000) + 1000,
        timestamp: Date.now(),
        sample_rate: 48000,
        channels: 1,
        format: "PCM_F32LE"
      };
      
      const response = await fetch(`/api/webrtc/data/${currentTestConnectionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(audioData)
      });
      
      const result = await response.json();
      
      if (result.success) {
        updateTestResults('✅ 오디오 데이터 전송 성공!', 'success');
        updateTestResults(`처리된 오디오: ${audioData.duration}ms`);
      } else {
        updateTestResults(`❌ 오디오 데이터 전송 실패: ${result.message}`, 'error');
      }
      
    } catch (error) {
      updateTestResults(`❌ API 호출 실패: ${error.message}`, 'error');
    }
  };

  // 제어 메시지 전송
  sendControlDataBtn.onclick = async () => {
    if (!currentTestConnectionId) {
      updateTestResults('활성 연결이 없습니다.', 'error');
      return;
    }
    
    updateTestResults('제어 메시지를 전송합니다...');
    
    try {
      const controlData = {
        type: "control_message",
        command: ["start_recording", "stop_recording", "pause", "resume"][Math.floor(Math.random() * 4)],
        timestamp: Date.now(),
        parameters: {
          quality: "high",
          format: "webm"
        }
      };
      
      const response = await fetch(`/api/webrtc/data/${currentTestConnectionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(controlData)
      });
      
      const result = await response.json();
      
      if (result.success) {
        updateTestResults('✅ 제어 메시지 전송 성공!', 'success');
        updateTestResults(`실행된 명령: ${controlData.command}`);
      } else {
        updateTestResults(`❌ 제어 메시지 전송 실패: ${result.message}`, 'error');
      }
      
    } catch (error) {
      updateTestResults(`❌ API 호출 실패: ${error.message}`, 'error');
    }
  };

  // 커스텀 데이터 전송
  sendCustomDataBtn.onclick = async () => {
    if (!currentTestConnectionId) {
      updateTestResults('활성 연결이 없습니다.', 'error');
      return;
    }
    
    updateTestResults('커스텀 데이터를 전송합니다...');
    
    try {
      const customData = {
        type: "custom_data",
        message: "Hello from client!",
        timestamp: Date.now(),
        custom_field: Math.random(),
        nested: {
          level1: {
            level2: "deep data"
          }
        }
      };
      
      const response = await fetch(`/api/webrtc/data/${currentTestConnectionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(customData)
      });
      
      const result = await response.json();
      
      if (result.success) {
        updateTestResults('✅ 커스텀 데이터 전송 성공!', 'success');
        updateTestResults(`메시지: ${customData.message}`);
      } else {
        updateTestResults(`❌ 커스텀 데이터 전송 실패: ${result.message}`, 'error');
      }
      
    } catch (error) {
      updateTestResults(`❌ API 호출 실패: ${error.message}`, 'error');
    }
  };
});