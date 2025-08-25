# ai_doll_module.py
import os
import sqlite3
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores.faiss import FAISS
from langchain.schema import Document
from langchain.docstore.in_memory import InMemoryDocstore
from langchain.prompts import PromptTemplate
import faiss

load_dotenv("env.txt")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

sentiment_prompts = {
    "Angry": "사용자가 화가 난 상태입니다. 화난 말투 표현.",
    "Happy": "사용자가 행복한 상태입니다. 행복한 말투 표현.",
    "Sad": "사용자가 슬픈 상태입니다. 슬픈 말투 표현.",
    "Disgust": "사용자가 역겨움/불쾌함을 표현했습니다. 역겨움, 불쾌함 공감.",
    "Neutral": "사용자가 중립적입니다. 일반적인 정보 제공과 자연스러운 대화를 이어가세요.",
    "Surprise": "사용자가 놀람을 표현했습니다. 놀람의 이유를 묻거나 공감하며 대화를 이어가세요.",
    "Fear": "사용자가 두려움을 표현했습니다. 안정감을 주고 안전한 느낌을 전달하세요."
}

DB_PATH = "user_history.db"
VEC_PATH = "vector_store_faiss"

class AIDoll:
    def __init__(self):
        self.user_states: Dict[str, Dict[str, Any]] = {}
        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()
        self._init_db()
        self.embeddings_model = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        dim = len(self.embeddings_model.embed_query("test"))
        index = faiss.IndexFlatL2(dim)
        self.vecdb = FAISS(index=index,
                          embedding_function=self.embeddings_model,
                          docstore=InMemoryDocstore({}),
                          index_to_docstore_id={})
        self.chat_model = ChatOpenAI(openai_api_key=OPENAI_API_KEY, model_name="gpt-4o-mini", temperature=0.3)
        self.prompt_template = PromptTemplate(input_variables=["final_prompt"],
                                              template="""너는 'AI 인형'이라는 가상 캐릭터야.
유저가 제공한 대화 내용과 요약 정보(final_prompt)에 따라 행동해야 해.
행동 지침:
1. 유저의 감정과 상황을 파악하고 공감해줘.
2. 유저의 최근 대화, 중요한 문장, 장기 기억 요약을 참고하여 맥락에 맞게 답변.
3. 최근 대화로 지금 말하고 있는 맥락 파악.
4. 중요한 문장은 유저에 대한 특성 파악.
5. 장기 기억 요약으로 유저에 대한 특별한 상황 인식.
6. 질문, 제안, 조언 등을 적절히 섞어 자연스럽게 대화 이어가기.
7. 1~3문장 정도로 간결하게 작성.

{final_prompt}""")
        self.llm_pipeline = self.prompt_template | self.chat_model

    def _init_db(self):
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            starttime TEXT NOT NULL,
            text TEXT NOT NULL,
            sentiment TEXT NOT NULL
        )""")
        self.conn.commit()

    def add_to_vecdb(self, rows: List[Dict[str, Any]]):
        if not rows:
            return
        texts = [f"User: {r['username']}\nText: {r['text']}\nSentiment: {r['sentiment']}" for r in rows]
        docs = [Document(page_content=t, metadata=r) for t, r in zip(texts, rows)]
        self.vecdb.add_documents(docs)
        self.vecdb.save_local(VEC_PATH)

    def summarize_with_gpt(self, prompt: str) -> str:
        return self.llm_pipeline.invoke({"final_prompt": prompt})

    def summarize_recent_context_str(self, text: str) -> str:
        prompt = f"다음 대화 내용을 주요 내용 중심으로 5문장 이하로 요약하세요:\n{text}"
        return self.summarize_with_gpt(prompt)

    def summarize_rag_context_text(self, rag_text: str) -> str:
        if not rag_text.strip():
            return ""
        combined_text = " ".join(dict.fromkeys([line.strip() for line in rag_text.split("\n") if line.strip()]))
        prompt = f"다음 내용을 주요 내용 중심으로 5문장 이하로 요약하세요:\n{combined_text}"
        return self.summarize_with_gpt(prompt)

    def handle_user_input(self, username: str, starttime: str, text: str, sentiment: str) -> str:
        if username not in self.user_states:
            self.user_states[username] = {"history": [], "trigger_index": None, "important_sentences_rows": []}
        state = self.user_states[username]

        entry = {"starttime": starttime, "text": text, "sentiment": sentiment, "username": username}
        state["history"].append(entry)

        trigger = False
        if len(state["history"]) >= 2 and state["history"][-2]["sentiment"] != sentiment:
            trigger = True

        if trigger:
            state["trigger_index"] = len(state["history"]) - 1

        if state["trigger_index"] is not None:
            trig_idx = state["trigger_index"]
            if len(state["history"]) >= trig_idx + 3:
                start_idx = max(0, trig_idx - 2)
                end_idx = trig_idx + 3
                selected = state["history"][start_idx:end_idx]
                combined_text = " ".join([h["text"] for h in selected])
                combined_row = {"username": username, "starttime": selected[0]["starttime"], "text": combined_text, "sentiment": state["history"][trig_idx]["sentiment"]}

                if not any(row["text"] == combined_text for row in state["important_sentences_rows"]):
                    state["important_sentences_rows"].append(combined_row)
                    self.cursor.execute(
                        "INSERT INTO user_history (username, starttime, text, sentiment) VALUES (?, ?, ?, ?)",
                        (combined_row["username"], combined_row["starttime"], combined_row["text"], combined_row["sentiment"])
                    )
                    self.conn.commit()
                    self.add_to_vecdb([combined_row])

                state["trigger_index"] = None

        query_embedding = np.array(self.embeddings_model.embed_query(text), dtype=np.float32)
        D, I = self.vecdb.index.search(query_embedding.reshape(1, -1), k=5)
        retrieved_docs = [self.vecdb.docstore._dict[self.vecdb.index_to_docstore_id[i]] for i in I[0] if i in self.vecdb.index_to_docstore_id]
        rag_context_text = "\n".join([doc.page_content for doc in retrieved_docs])
        rag_context = self.summarize_rag_context_text(rag_context_text)

        recent_conversation = state["history"][-5:] if len(state["history"]) > 5 else state["history"]
        conversation_text = "\n".join([f"User: {h['text']}" for h in recent_conversation])
        conversation_text = self.summarize_recent_context_str(conversation_text)

        current_prompt = sentiment_prompts.get(sentiment, sentiment_prompts["Neutral"])
        final_prompt_parts = [current_prompt]
        if rag_context:
            final_prompt_parts.append(f"참고 컨텍스트(RAG):\n{rag_context}")
        final_prompt_parts.append(f"현재 대화:\n{conversation_text}")
        final_prompt_parts.append("\nAI 인형 응답:")
        final_prompt = "\n".join(final_prompt_parts)

        response = self.llm_pipeline.invoke({"final_prompt": final_prompt}).content
        return response
