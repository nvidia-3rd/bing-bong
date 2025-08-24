# http_client.py
import httpx
import streamlit as st
from config import API_BASE

## fastapi로 호출하는 client
@st.cache_resource
def get_http() -> httpx.AsyncClient:
    # 재사용 가능한 AsyncClient
    return httpx.AsyncClient(base_url=API_BASE, timeout=10)

# 편의 함수
async def post_json(http: httpx.AsyncClient, path: str, payload: dict, timeout: float = 10.0):
    r = await http.post(path, json=payload, timeout=timeout)
    r.raise_for_status()
    return r

async def post_multipart(http: httpx.AsyncClient, path: str, files: dict, data: dict, timeout: float = 30.0):
    r = await http.post(path, files=files, data=data, timeout=timeout)
    r.raise_for_status()
    return r