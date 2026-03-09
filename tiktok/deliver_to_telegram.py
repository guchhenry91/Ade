#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import json
import uuid
import urllib.request

WORKSPACE = Path("/data/.openclaw/workspace").resolve()
ALLOWED_EXTS = {".mp4", ".mov", ".png", ".jpg"}
FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519", "credentials", "token", "secret", "key"}
MAX_BOT_BYTES = 50 * 1024 * 1024
CAPTION = "TikTok video ready for posting."
DEFAULT_CHAT_ID = "-1003659714036"
DEFAULT_TOPIC_ID = "66"


def fail(msg: str, code: int = 1):
    print(f"ERROR: {msg}")
    sys.exit(code)


def is_safe_path(p: Path) -> bool:
    try:
        p.resolve().relative_to(WORKSPACE)
        return True
    except Exception:
        return False


def forbidden_file(p: Path) -> bool:
    name = p.name.lower()
    if name in FORBIDDEN_NAMES:
        return True
    if any(tag in name for tag in [".env", "token", "secret", "key"]):
        return True
    return False


def extract_date(p: Path) -> str:
    # expected .../videos/YYYY-MM-DD/file
    for part in p.parts:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", part):
            return part
    return Path().cwd().name


def compress_video(src: Path, dst: Path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        fail("File is >50MB and ffmpeg is not installed for compression.")

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-vf",
        "scale=-2:1920",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        fail(f"Compression failed: {proc.stderr[-400:]}")


def send_file(video_path: Path):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or DEFAULT_CHAT_ID
    topic_id = os.getenv("TELEGRAM_TOPIC_ID") or DEFAULT_TOPIC_ID

    if not token:
        fail("TELEGRAM_BOT_TOKEN is missing.")
    if not chat_id:
        fail("TELEGRAM_CHAT_ID is missing and no default is configured.")

    url = f"https://api.telegram.org/bot{token}/sendVideo"
    boundary = f"----OpenClawBoundary{uuid.uuid4().hex}"

    with video_path.open("rb") as f:
        file_bytes = f.read()

    lines = []
    def add_field(name: str, value: str):
        lines.append(f"--{boundary}\r\n".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        lines.append(value.encode())
        lines.append(b"\r\n")

    add_field("chat_id", chat_id)
    if topic_id:
        add_field("message_thread_id", topic_id)
    add_field("caption", CAPTION)

    lines.append(f"--{boundary}\r\n".encode())
    lines.append(
        f'Content-Disposition: form-data; name="video"; filename="{video_path.name}"\r\n'.encode()
    )
    lines.append(b"Content-Type: video/mp4\r\n\r\n")
    lines.append(file_bytes)
    lines.append(b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode())

    body = b"".join(lines)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            status = resp.getcode()
    except Exception as e:
        fail(f"Upload failed: {e}")

    if status != 200:
        fail(f"Upload failed [{status}]: {resp_body[:500]}")

    try:
        payload = json.loads(resp_body)
    except Exception:
        fail(f"Upload response was not JSON: {resp_body[:500]}")

    if not payload.get("ok"):
        fail(f"Upload failed: {payload}")

    print(f"SUCCESS: delivered {video_path}")


def main():
    if len(sys.argv) < 2:
        fail("Usage: deliver_to_telegram.py /absolute/path/to/video.mp4")

    src = Path(sys.argv[1]).resolve()
    if not src.exists():
        fail(f"File not found: {src}")
    if not is_safe_path(src):
        fail("Only files inside /data/.openclaw/workspace/ are allowed.")
    if forbidden_file(src):
        fail("Refusing to upload sensitive-looking file.")
    if src.suffix.lower() not in ALLOWED_EXTS:
        fail(f"Extension not allowed: {src.suffix}")

    if src.stat().st_size == 0:
        fail("File is empty (0 bytes).")

    date_part = extract_date(src)
    clean_name = f"tiktok-video-{date_part}.mp4"
    clean_path = src.parent / clean_name

    if src != clean_path:
        shutil.copy2(src, clean_path)

    target = clean_path

    if target.stat().st_size > MAX_BOT_BYTES:
        compressed = target.parent / f"tiktok-video-{date_part}-compressed.mp4"
        compress_video(target, compressed)
        target = compressed
        if target.stat().st_size > MAX_BOT_BYTES:
            fail("Compressed file is still larger than 50MB.")

    send_file(target)


if __name__ == "__main__":
    main()
