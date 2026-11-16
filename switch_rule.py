# -*- coding: utf-8 -*-
"""
可选辅助脚本：将本地 Clash Verge / Mihomo 代理模式切换为 rule（避免全局代理导致抖音网页端异常）。
支持通过环境变量配置 MIHOMO_API_URL（默认 http://127.0.0.1:9097）、MIHOMO_PIPE 与 MIHOMO_SECRET。
"""
import os
import json
import httpx
from dotenv import load_dotenv

load_dotenv()


def switch_via_pipe(pipe_path: str, secret: str) -> bool:
    payload = json.dumps({"mode": "rule"}).encode("utf-8")
    auth_header = f"Authorization: Bearer {secret}\r\n" if secret else ""
    with open(pipe_path, "r+b", buffering=0) as f:
        req = (
            f"PATCH /configs HTTP/1.1\r\nHost: localhost\r\n{auth_header}"
            f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n"
        ).encode("utf-8") + payload
        f.write(req)
        data = f.read(1024).decode("utf-8", errors="ignore")
        print("PATCH Response:", data.splitlines()[0] if data else "")

    with open(pipe_path, "r+b", buffering=0) as f:
        req = f"GET /configs HTTP/1.1\r\nHost: localhost\r\n{auth_header}\r\n".encode("utf-8")
        f.write(req)
        data = f.read(4096).decode("utf-8", errors="ignore")
        body = data.split("\r\n\r\n", 1)[1]
        cfg = json.loads(body)
        print("Current Mihomo Mode:", cfg.get("mode"))
    return True


def switch_via_http(api_url: str, secret: str) -> bool:
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    resp = httpx.patch(f"{api_url.rstrip('/')}/configs", json={"mode": "rule"}, headers=headers, timeout=5.0)
    resp.raise_for_status()
    cfg = httpx.get(f"{api_url.rstrip('/')}/configs", headers=headers, timeout=5.0).json()
    print("Current Mihomo Mode:", cfg.get("mode"))
    return True


if __name__ == "__main__":
    pipe = os.getenv("MIHOMO_PIPE", "").strip()
    secret = os.getenv("MIHOMO_SECRET", "").strip()
    api_url = os.getenv("MIHOMO_API_URL", "http://127.0.0.1:9097").strip()

    if pipe:
        switch_via_pipe(pipe, secret)
    else:
        switch_via_http(api_url, secret)
