# ai_doll_module.py
import os
import json
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from openai import OpenAI

# ----------------------------
# 환경변수
# ----------------------------
load_dotenv("env.txt")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ----------------------------
# 감정별 프롬프트
# ----------------------------
sentiment_prompts = {
    "Angry": "사용자가 화가 난 상태입니다. 화난 말투 표현.",
    "Happy": "사용자가 행복한 상태입니다. 행복한 말투 표현.",
    "Sad": "사용자가 슬픈 상태입니다. 슬픈 말투 표현.",
    "Disgust": "사용자가 역겨움/불쾌함을 표현했습니다. 역겨움, 불쾌함 공감.",
    "Neutral": "사용자가 중립적입니다. 일반적인 정보 제공과 자연스러운 대화를 이어가세요.",
    "Surprise": "사용자가 놀람을 표현했습니다. 놀람의 이유를 묻거나 공감하며 대화를 이어가세요.",
    "Fear": "사용자가 두려움을 표현했습니다. 안정감을 주고 안전한 느낌을 전달하세요."
}

# ----------------------------
# CSV 및 VectorStore 설정
# ----------------------------
CSV_PATH = "important_sentences.csv"
VEC_PATH = "vector_store.npz"
META_PATH = "vector_store_meta.json"
EMBED_MODEL = "text-embedding-3-small"

# CSV 초기화
if not os.path.exists(CSV_PATH):
    pd.DataFrame(columns=["starttime", "text", "sentiment"]).to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

# ----------------------------
# 간단한 VectorStore
# ----------------------------
def get_embedding(text: str) -> List[float]:
    resp = client.embeddings.create(model=EMBED_MODEL, input=text)
    return resp.data[0].embedding

class VectorStore:
    def __init__(self, vec_path: str, meta_path: str):
        self.vec_path = vec_path
        self.meta_path = meta_path
        self.embeddings = None
        self.metas: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        if os.path.exists(self.vec_path) and os.path.exists(self.meta_path):
            try:
                data = np.load(self.vec_path)
                self.embeddings = data["embeddings"]
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self.metas = json.load(f)
            except Exception:
                self.embeddings = None
                self.metas = []
        else:
            self.embeddings = None
            self.metas = []

    def _save(self):
        np.savez(self.vec_path, embeddings=self.embeddings if self.embeddings is not None else np.zeros((0,0)))
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(self.metas, f, ensure_ascii=False, indent=2)

    def add_texts(self, rows: List[Dict[str, Any]]):
        if not rows:
            return
        new_embs, new_metas = [], []
        for r in rows:
            payload = f"Text: {r['text']}\nSentiment: {r['sentiment']}"
            emb = get_embedding(payload)
            new_embs.append(emb)
            new_metas.append(r)
        new_embs = np.array(new_embs, dtype=np.float32)
        if self.embeddings is None or self.embeddings.size == 0:
            self.embeddings = new_embs
        else:
            self.embeddings = np.vstack([self.embeddings, new_embs])
        self.metas.extend(new_metas)
        self._save()

    def similarity_search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        if self.embeddings is None or self.embeddings.size == 0 or not self.metas:
            return []
        q_emb = np.array(get_embedding(query), dtype=np.float32)
        A_norm = self.embeddings / (np.linalg.norm(self.embeddings, axis=1, keepdims=True) + 1e-12)
        q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-12)
        sims = A_norm @ q_norm
        idxs = np.argsort(-sims)[:k]
        return [self.metas[i] for i in idxs]

# ----------------------------
# AICharacter 클래스
# ----------------------------
class AICharacter:
    def __init__(self):
        self.history: List[Dict[str, Any]] = []
        self.last_sentiment: Optional[str] = None
        self.sentiment_change_index: Optional[int] = None
        self.important_sentences_rows: List[Dict[str, Any]] = []
        self.vecdb = VectorStore(VEC_PATH, META_PATH)
        self.sync_csv_to_vecdb()

    # CSV → VectorDB 동기화
    def sync_csv_to_vecdb(self):
        if os.path.exists(CSV_PATH):
            df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
            if not df.empty:
                if self.vecdb.embeddings is None or len(self.vecdb.metas) != len(df):
                    self.vecdb.embeddings = None
                    self.vecdb.metas = []
                    self.vecdb.add_texts(df.to_dict(orient="records"))

    # 최근 대화 30문장
    def get_recent_30_sentences(self):
        return self.history[-30:] if len(self.history) > 30 else self.history

    # 중요 문장 context
    def get_important_sentences_context(self) -> str:
        if os.path.exists(CSV_PATH):
            df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
            if not df.empty:
                df_sorted = df.sort_values('starttime', ascending=False).head(10)
                return "\n".join([f"[중요] {row['text']} (감정: {row['sentiment']})" for _, row in df_sorted.iterrows()])
        return ""

    # RAG context 빌드
    def build_context_for_ai(self, current_user_text: str, current_sentiment: str, k_retrieve: int = 5) -> str:
        parts = []
        # 최근 대화 요약
        recent = self.get_recent_30_sentences()
        if recent:
            combined_text = " ".join([h["text"] for h in recent])
            parts.append(f"최근 대화 요약:\n{combined_text}")

        # 중요 문장
        important_context = self.get_important_sentences_context()
        if important_context:
            parts.append(f"중요한 이전 대화들:\n{important_context}")

        # VectorDB Retrieval
        retrieve_query = f"Query Text: {current_user_text}\nQuery Sentiment: {current_sentiment}"
        retrieved = self.vecdb.similarity_search(retrieve_query, k=k_retrieve)
        if retrieved:
            parts.append("VectorDB 유사 컨텍스트:\n" + "\n".join(
                [f"[R{i+1}] {r['text']} (감정: {r['sentiment']}, 시간: {r.get('starttime','')})" 
                 for i, r in enumerate(retrieved)]
            ))
        return "\n\n".join(parts)

    # 핵심 처리
    def handle_user_input(self, starttime: str, text: str, sentiment: str) -> str:
        # 감정 변화 확인
        importance_type = "normal"
        if self.last_sentiment != sentiment:
            importance_type = "sentiment_change"
        self.last_sentiment = sentiment

        # 기록
        entry = {"starttime": starttime, "text": text, "sentiment": sentiment}
        self.history.append(entry)

        if importance_type == "sentiment_change":
            self.sentiment_change_index = len(self.history) - 1

        # sentiment_change 처리\
        if self.sentiment_change_index is not None:
            if len(self.history) >= self.sentiment_change_index + 2:
                start_idx = max(0, self.sentiment_change_index - 2)
                end_idx = min(len(self.history), self.sentiment_change_index + 2)
                selected = self.history[start_idx:end_idx]

                # -----------------------------
                # 중복 문장 제거
                # -----------------------------
                seen_texts = set()
                deduped_texts = []
                for h in selected:
                    text = h["text"].strip()
                    if text not in seen_texts:
                        deduped_texts.append(text)
                        seen_texts.add(text)
                combined_text = " ".join(deduped_texts)

                combined_row = {
                    "starttime": selected[0]["starttime"],
                    "text": combined_text,
                    "sentiment": self.history[self.sentiment_change_index]["sentiment"]
                }

                # 중복 combined_text 확인 후 추가
                if not any(row["text"] == combined_text for row in self.important_sentences_rows):
                    self.important_sentences_rows.append(combined_row)
                    pd.DataFrame([combined_row]).to_csv(
                        CSV_PATH, mode='a', header=False, index=False, encoding="utf-8-sig"
                    )
                    self.vecdb.add_texts([combined_row])

                self.sentiment_change_index = None

        # RAG 컨텍스트
        rag_context = self.build_context_for_ai(current_user_text=text, current_sentiment=sentiment)

        # 최종 프롬프트
        current_prompt = sentiment_prompts.get(sentiment, sentiment_prompts["Neutral"])
        recent_conv = self.history[-5:] if len(self.history) > 5 else self.history
        conversation_text = "\n".join([f"User: {h['text']}" for h in recent_conv])

        final_prompt_parts = [current_prompt]
        if rag_context:
            final_prompt_parts.append(f"참고 컨텍스트(RAG):\n{rag_context}")
        final_prompt_parts.extend([
            f"현재 대화:\n{conversation_text}",
            "\nAI 인형 응답:"
        ])
        final_prompt = "".join(final_prompt_parts)

        # 최신 OpenAI SDK 호출
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": """너는 'AI 인형'이라는 가상 캐릭터야. \
                유저가 제공한 대화 내용과 요약 정보(final_prompt)에 따라 행동해야 해. \
                행동 지침: \
                    1. 유저의 감정과 상황을 파악하고 공감해줘. \
                    2. 유저의 최근 대화, 중요한 문장, 장기 기억 요약을 참고하여 맥락에 맞게 답변. \
                    3. 최근 대화로 지금 말하고 있는 맥락 파악. \
                    4. 중요한 문장은 유저에 대한 특성 파악. \
                    5. 장기 기억 요약으로 유저에 대한 특별한 상황 인식. \
                    6. 질문, 제안, 조언 등을 적절히 섞어 자연스럽게 대화 이어가기. \
                    7. 1~3문장 정도로 간결하게 작성."""},
                {"role": "user", "content": final_prompt},
            ],
            temperature=0.7
        )

        return response.choices[0].message.content
