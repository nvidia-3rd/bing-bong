
<p align="center">
  <img src="YOUR_LOGO_URL_HERE" alt="프로젝트 로고" width="300">
</p>
<br>
<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=FastAPI&logoColor=white" alt="FastAPI badge"> 
  <img src="https://img.shields.io/badge/Streamlit-FF4B4B?style=flat-square&logo=streamlit&logoColor=white" alt="Streamlit badge"> 
  <img src="https://img.shields.io/badge/WebRTC-333333?style=flat-square&logo=webrtc&logoColor=white" alt="WebRTC badge"> 
  <img src="https://img.shields.io/badge/PyTorch-EE4C2C?style=flat-square&logo=PyTorch&logoColor=white" alt="PyTorch badge"> 
  <img src="https://img.shields.io/badge/FAISS-0467DF?style=flat-square&logo=meta&logoColor=white" alt="FAISS badge">
  <br>
  <img src="https://img.shields.io/badge/PostgreSQL-4169e1?style=flat-square&logo=postgresql&logoColor=white" alt="PostgreSQL badge"> 
  <img src="https://img.shields.io/badge/Redis-DC382D?style=flat-square&logo=redis&logoColor=white" alt="Redis badge"> 
  <img src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker badge"> 
  <img src="https://img.shields.io/badge/OpenAI%20Whisper-412991?style=flat-square&logo=openai&logoColor=white" alt="Whisper badge"> 
  <img src="https://img.shields.io/badge/DeepFace-555555?style=flat-square" alt="DeepFace badge">
</p>

<br><br>

## 프로젝트 설명 🧩✨
**[🎧 멀티모달 공감 AI 인형 데모(링크 예정)](#)**
<br><br>
<strong>멀티모달 공감 AI 인형</strong>은 카메라 기반 표정 인식과 음성 대화를 결합해 사용자의 감정을 이해하고 공감적으로 응답하는 서비스입니다.  
현대사회에서 심화되는 노인 및 1인 가구의 고독사 문제를 완화하기 위해, 실시간 감정 인식·장기기억·맥락 인식 프롬프팅을 통해 **정서적 안정과 교감**을 제공합니다.

> 보건복지부 「2024년 고독사 사망자 실태조사」에 따르면 2023년 한 해 3,661명이 홀로 생을 마감했습니다. 🕯️ 단순한 수치를 넘어, 기억되지 못한 채 사라지는 **사회적 고립**의 문제에 기술로 응답하고자 합니다.  
> 표정 인식 + 음성 기반 대화 + 공감 프롬프팅으로 **진정성 있는 동반자**를 구현합니다. 🤝

<p align="center">
  <img src="YOUR_SCREENSHOT_1_URL" width="200">
  <img src="YOUR_SCREENSHOT_2_URL" width="200">
  <img src="YOUR_SCREENSHOT_3_URL" width="200">
  <img src="YOUR_SCREENSHOT_4_URL" width="200">
</p>

<br><br><br>

## 시연 영상 🎬
데모 링크: (업로드 예정) ⏳

<br><br><br>

## 💻 팀 구성 및 역할 👥
| 지욱 | 준서 | 채연 | 정상 | 민성 | 진수 |
|:---:|:---:|:---:|:---:|:---:|:---:|
|Vision · STT (DeepFace, WebRTC+Whisper) 🎥🎙️|감정 프롬프팅 설계(7감정) 🧩|CoE 공감 체인·여섯 모자 프롬프트·UX 🧠🎩|대화 맥락·요약·Vector DB 🧵|TTS(감정 반영 음성) 🔊|Streamlit/WebRTC Client · FastAPI 🧰|

<br><br><br>

## 서비스 아키텍쳐 🏗️
<img width="100%" src="YOUR_ARCHITECTURE_IMAGE_URL"/>

- **Client (Streamlit + WebRTC)**: 카메라/마이크 입력 스트리밍, 실시간 UI 🎛️
- **FastAPI Adapter**: STT/TTS/LLM/Vision 외부 API 연계, 백엔드 Orchestration 🔌
- **Emotion Engine**: DeepFace 기반 7-class 표정 감정 추론 😊
- **LLM Orchestrator**: 공감 체인(CoE) + 여섯 모자 기법 기반 프롬프트 구성 🧠
- **Memory Layer**: RDB(사용자/세션/이벤트), Vector DB(의미 기반 검색) 🗂️
- **Storage**: PostgreSQL, Redis(세션/큐), 객체 스토리지(선택) 💾

<br><br><br>

## 모델 아키텍쳐 ⚙️🧠
<table>
  <tr align='center'>
    <td>
      <strong>Emotion Recognition (DeepFace)</strong>
      <img width="60%" src="YOUR_MODEL_IMAGE_EMOTION_URL"/>
      <p align="left">
      - 입력: 영상 프레임(10-frame window) 🎥<br>
      - 출력: 7 감정(Joy, Sadness, Anger, Surprise, Fear, Disgust, Neutral) 😀😢😠😮😨🤢😐<br>
      - 용도: 발화 전/후 감정 변화 탐지 및 이벤트 태깅 🏷️
      </p>
    </td>
    <td>
      <strong>LLM Orchestration (CoE + Six Hats)</strong>
      <img width="72%" src="YOUR_MODEL_IMAGE_LLM_URL"/>
      <p align="left">
      - 공감 체인(CoE) 프롬프트로 응답 톤/구조 가이드 🫂<br>
      - 여섯 모자(방향·수용·객관·강점·가능성·현실)로 사고 틀 제공 🎩🎩🎩🎩🎩🎩<br>
      - 최근 요약(3~5줄) + 장기기억 + 감정 컨텍스트로 개인화 응답 🧠📝
      </p>
    </td>
  </tr>
</table>

<br><br><br>

## 데이터셋 🗃️
본 프로젝트는 **실시간 입력(영상·음성)**과 **대화 로그/이벤트**를 결합해 **감정-맥락-기억**을 동시에 다룹니다.

### 1) 감정 이벤트 스트림 📈
- **수집 단위**: 10 프레임당 인코딩 및 이미지 캡처 → DeepFace 추론 🧪
- **핵심 필드**
  | Column | Description |
  |---|---|
  | `user_id` | 사용자 식별자 |
  | `ts` | 이벤트 시각(UTC) ⏱️ |
  | `emotion_before` | 발화 전 감정 |
  | `emotion_after` | 발화 후 감정 |
  | `delta_flag` | 감정 변화 여부(중요 순간) ⚡ |
  | `frame_ref` | 프레임/썸네일 참조(옵션) 🖼️ |

### 2) 음성 → STT 텍스트 🎤➡️📝
- **Whisper** 기반 STT (침묵 감지로 문장 분할) 🔇✂️
- **핵심 필드**
  | Column | Description |
  |---|---|
  | `user_id` | 사용자 식별자 |
  | `utterance_id` | 발화 ID 🔖 |
  | `text` | 인식된 문장 |
  | `start_ts` / `end_ts` | 발화 시간 범위 ⏰ |
  | `audio_ref` | 원본 오디오 참조(옵션) 🎧 |

### 3) 대화/기억 스토어 💭🗂️
- **RDB**: 사용자/세션/감정 이벤트/발화 로그
- **Vector DB(FAISS)**: 발화 임베딩, 유사 문맥 검색
- **핵심 필드**
  | Table | Key Fields |
  |---|---|
  | `users` | `user_id`, profile/meta 👤 |
  | `sessions` | `session_id`, `user_id`, started_at 🧭 |
  | `utterances` | `utterance_id`, `user_id`, text, ts 💬 |
  | `emotions` | `event_id`, `user_id`, emotion_before/after, delta_flag 🧾 |
  | `memories` | long-term notes, tags, last_accessed 📚 |

<br><br>

## 프로젝트 목표 🎯
### 1) 개발 목표
- **카메라 표정 인식 + 음성 감정 분석**으로 공감적 소통이 가능한 **멀티유저 지원 AI 인형** 구현 🤖💞

### 2) 핵심 기능
- **실시간 감정 인식**: 카메라 표정 분석으로 감정 상태 파악 🛰️
- **음성 기반 대화**: 오디오 입력으로 자연스러운 대화 🗣️
- **지능형 장기기억**: 감정 변화의 “중요 순간”을 자동 축적 🧠📌
- **맥락 인식 응답**: 과거 경험/감정 히스토리 기반 개인화 응답 🧵

### 3) 차별화 요소
- **발화 전/후 감정 변화 탐지 → 중요 순간 자동 식별 → 장기기억화** 🔍➡️📎  
  LLM이 단발성 대화가 아닌 **관계형 동반자**로 진화 🌱

<br><br>

## 과제 해결방안 🛠️
### 1) 표정 인식 감정 분류 및 대화 반영
- DeepFace 기반 **7 감정** 추론  
  - 기쁨(Joy), 슬픔(Sadness), 분노(Anger), 놀람(Surprise), 두려움(Fear), 혐오(Disgust), 중립(Neutral) 😀😢😠😮😨🤢😐
- **발화 전/후** 감정 변화를 중요 순간으로 정의, 해당 구간의 이전/이후 발화를 함께 RDB에 저장 🗄️
- **공감 체인(CoE)** 프롬프트로 대화지침 제공 🫂
- **여섯 모자 기법**으로 사고 틀(방향·수용·객관·강점·가능성·현실) 주입 🎩
- 감정별 **우선 모자**를 설정해 현재 정서에 맞는 답변 톤/내용 유도 🎯

### 2) 언어모델 기반 맥락 반영 대화
- **최근 5턴 요약**(3~5문장)으로 컨텍스트 유지 📝
- **RDB 1차 필터링** 후 **Vector DB** 유사 문맥 검색 → 응답에 반영 🧭🔎

### 3) 음성 입출력
- **STT**: WebRTC 실시간 스트림, 침묵 기반 문장 분할, Whisper 인식 🎙️
- **TTS**: 감정 반영 음성 합성(사용자 정서와 톤 정렬) 🔈💖

### 4) 클라이언트/서버
- **Streamlit + WebRTC**로 저지연 스트림 UI ⚡
- **FastAPI**로 외부 API 어댑터/오케스트레이션, 장애 시 유연 대응 🧯

<br><br>

## 과제를 위한 프로세스 🔄
[사용자 입력(영상+음성)] 🎥🎙️  
&nbsp;&nbsp;&darr;  
[표정 인식 모델(7-class) 추론] 😊  
&nbsp;&nbsp;&darr;  
[감정 이벤트 저장 → 감정 컨텍스트 생성] 🗃️  
&nbsp;&nbsp;&darr;  
[최근 대화 요약 업데이트(3~5줄) & 장기기억 조회] 📝🧠  
&nbsp;&nbsp;&darr;  
[프롬프트 구성: 시스템 규칙 + 감정 톤 가이드 + 사용자 질문 + 최근요약 + 장기기억] 🧩  
&nbsp;&nbsp;&darr;  
[LLM 응답 생성] 🤖  
&nbsp;&nbsp;&darr;  
[TTS + 화면 UI] 🔊🖥️

<br><br>

## 기대효과 🌟
1) **정서적 안정과 공감 경험**: 7 감정 분류에 맞춘 톤/내용 제공 💗  
2) **개인화 상담/학습**: 장기기억 기반 지속·진화형 관계 🔄🧠  
3) **몰입형 멀티모달 소통**: 음성 + 표정으로 실제 같은 교감 🎭  
4) **자기 표현·자존감 향상**: 감정 인식으로 왜곡 없는 상호작용 🚀  
5) **맞춤형 답변 제공**: 상황·감정 최적화 LLM 응답 (교육·상담·돌봄 확장) 🎯

<br><br>

## 해당 과제 수행을 위한 진행 계획 🗓️
1. **데이터 준비/전처리** 📥🧹  
   - Streamlit-WebRTC로 `videoFrame`/`audioFrame` 획득 🎥🎙️  
   - 10-frame 단위 인코딩/이미지 캡처 → Emotion dict 생성 🧪  
   - Whisper API로 `.wav` STT 텍스트화 📝
2. **표정 인식 모델 로딩** 🧠  
   - DeepFace 7-class 추론 파이프라인
3. **STT 파이프라인** 🔊  
   - 침묵 감지 문장 분할, 타임스탬프 동기화 ⏱️
4. **LLM 레이어** 🤖  
   - 공감 체인(CoE) + 여섯 모자 프롬프트 🎩  
   - 멀티유저 발화/감정 데이터를 **RDB+VectorDB** 테이블로 관리 🗂️
5. **TTS 레이어** 🎶  
   - 감정 반영 음성 합성(톤/속도/강세 조절) 🎚️
6. **성능 평가/개선** 📊  
   - F1 Score(감정 분류), 검색 정확도(Vector), 상담 효율성 지표 → 지속 개선 🔁

<br><br>

## 예시 결과물 📌
**[예시 1]**  
입력: “프로젝트가 조금 진전돼서 기분이 나아”  
LLM: “프로젝트의 진전이 기분을 좋게 해주었다니 정말 다행이에요! 작은 성과가 큰 힘이 될 수 있죠. 특히 어떤 부분에서 변화가 가장 크게 느껴졌나요? 그 경험이 앞으로의 동기부여에 어떻게 도움이 될 것 같아요?” 😊

**[예시 2]**  
입력: “시험이 다가오는데 지난주보다 마음이 안정돼”  
LLM: “시험이 다가오고 마음이 안정된 상태라니 정말 다행이에요! 이렇게 긍정적인 마음가짐이 시험 준비에 큰 도움이 될 거예요. 혹시 공부 방법이나 필요한 자료에 대해 이야기하고 싶다면 언제든지 말씀해 주세요!” 📚✨

<br><br>

## Appendix 📎
- 발표 자료: (링크 예정)
