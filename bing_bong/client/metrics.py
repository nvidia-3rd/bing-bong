# metrics.py
import threading
import time
from collections import deque
import streamlit as st

class Metrics:
    def __init__(self):
        self._enq = 0
        self._deq = 0
        self._lock = threading.Lock()
        # FPS 측정
        self._fps = 0.0
        self._frame_count = 0
        self._last_ts = time.monotonic()
    def inc_enq(self): 
        with self._lock: self._enq += 1
    def inc_deq(self): 
        with self._lock: self._deq += 1
    def tick_frame(self):
        with self._lock:
            self._frame_count += 1
            now = time.monotonic()
            elapsed = now - self._last_ts
            if elapsed >= 1.0:
                # 지난 구간의 평균 FPS 계산
                self._fps = self._frame_count / elapsed
                self._frame_count = 0
                self._last_ts = now
    def snapshot(self):
        with self._lock:
            return {"enq": self._enq, "deq": self._deq, "fps": self._fps}

class ErrorBuf:
    def __init__(self, maxlen=30):
        self._buf = deque(maxlen=maxlen)
        self._lock = threading.Lock()
    def push(self, msg: str):
        with self._lock: self._buf.appendleft(msg)
    def take(self):
        with self._lock: return list(self._buf)

@st.cache_resource
def get_metrics() -> Metrics:
    return Metrics()

@st.cache_resource
def get_errbuf() -> ErrorBuf:
    return ErrorBuf()