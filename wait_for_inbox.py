# -*- coding: utf-8 -*-
"""
事件驱动的桥接收件箱监听器（零轮询、零 sleep，基于 Win32 内核目录通知）。
一旦 douyin_rpa_gemini.py --bridge 写入新消息，立即消费打印并退出，唤醒外部 AI 本尊回复。
"""
import os
import sys
import json
import time
import ctypes
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
INBOX_DIR = BASE_DIR / "bridge_inbox"
ARCHIVE_DIR = BASE_DIR / "bridge_archive"
INBOX_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x00000000


def pop_pending_messages():
    files = sorted(INBOX_DIR.glob("*.json"))
    if not files:
        return []
    messages = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8")
            data = json.loads(content)
            messages.append(data)
            target = ARCHIVE_DIR / f.name
            os.replace(f, target)
        except Exception as e:
            print(f"[warn] failed reading {f.name}: {e}", file=sys.stderr)
    return messages


def main():
    msgs = pop_pending_messages()
    if msgs:
        print(json.dumps(msgs, ensure_ascii=False, indent=2))
        return

    if os.name != "nt":
        # Non-Windows fallback: lightweight directory check
        while True:
            time.sleep(0.5)
            msgs = pop_pending_messages()
            if msgs:
                print(json.dumps(msgs, ensure_ascii=False, indent=2))
                return

    k32 = ctypes.windll.kernel32
    handle = k32.FindFirstChangeNotificationW(
        str(INBOX_DIR),
        False,
        FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE,
    )
    if not handle or handle == -1:
        raise RuntimeError("Failed to create Win32 FindFirstChangeNotificationW handle")

    try:
        while True:
            res = k32.WaitForSingleObject(handle, INFINITE)
            if res == WAIT_OBJECT_0:
                time.sleep(0.03)
                msgs = pop_pending_messages()
                if msgs:
                    print(json.dumps(msgs, ensure_ascii=False, indent=2))
                    return
                k32.FindNextChangeNotification(handle)
            else:
                raise RuntimeError(f"WaitForSingleObject unexpected return: {res}")
    finally:
        k32.FindCloseChangeNotification(handle)


if __name__ == "__main__":
    main()
