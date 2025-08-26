import asyncio
import threading
import streamlit as st
from typing import Coroutine


def ensure_bg_loop() -> asyncio.AbstractEventLoop:
    if "bg_loop" not in st.session_state:
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, name="st-bg-loop", daemon=True)
        t.start()
        st.session_state.bg_loop = loop
        st.session_state.bg_loop_thread = t
    return st.session_state.bg_loop


def submit_coro(coro: Coroutine):
    loop = ensure_bg_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop)


