#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt
import json
import mimetypes
import os
import pathlib
import subprocess
import urllib.request
import shutil

WORKSPACE = pathlib.Path('/data/.openclaw/workspace').resolve()
ALLOWED_EXT = {'.mp4', '.mov', '.png', '.jpg', '.jpeg'}
MAX_BYTES = 50 * 1024 * 1024


def in_workspace(p: pathlib.Path) -> bool:
    try:
        p.resolve().relative_to(WORKSPACE)
        return True
    except Exception:
        return False


def clean_name(src: pathlib.Path) -> str:
    d = dt.datetime.now(dt.timezone.utc).date().isoformat()
    ext = src.suffix.lower()
    if ext in {'.mp4', '.mov'}:
        return f'tiktok-video-{d}{ext}'
    return f'media-{d}{ext}'


def maybe_compress(video: pathlib.Path) -> pathlib.Path:
    if video.stat().st_size <= MAX_BYTES:
        return video
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return video
    out = video.with_name(video.stem + '-compressed.mp4')
    cmd = [ffmpeg, '-y', '-i', str(video), '-vcodec', 'libx264', '-preset', 'veryfast', '-crf', '30', '-acodec', 'aac', '-b:a', '96k', str(out)]
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if out.exists() and out.stat().st_size < video.stat().st_size:
        return out
    return video


def video_sanity_check(video: pathlib.Path) -> tuple[bool, str]:
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        static_probe = pathlib.Path('/data/.openclaw/workspace/tools/ffmpeg-static/ffprobe')
        if static_probe.exists():
            ffprobe = str(static_probe)
    if not ffprobe:
        return False, 'ffprobe not available (cannot validate real video)'
    cmd = [ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-show_entries', 'format=duration', '-of', 'json', str(video)]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        data = json.loads(raw)
        width = int((data.get('streams') or [{}])[0].get('width') or 0)
        height = int((data.get('streams') or [{}])[0].get('height') or 0)
        duration = float((data.get('format') or {}).get('duration') or 0)
        if width <= 0 or height <= 0 or duration <= 0:
            return False, 'invalid media metadata'
        # TikTok target sanity bounds
        if duration < 3 or duration > 180:
            return False, f'duration out of range ({duration:.1f}s)'
        if height < width:
            return False, f'non-vertical frame ({width}x{height})'
        return True, f'ok ({width}x{height}, {duration:.1f}s)'
    except Exception:
        return False, 'ffprobe parsing failed'


def send_video(token: str, chat_id: str, thread_id: str | None, file_path: pathlib.Path, caption: str) -> tuple[bool, str]:
    boundary = '----OpenClawBoundary7MA4YWxkTrZu0gW'
    mime = mimetypes.guess_type(file_path.name)[0] or 'application/octet-stream'

    parts = []
    def add_field(name: str, value: str):
        parts.append(f'--{boundary}\r\n'.encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(value.encode())
        parts.append(b'\r\n')

    add_field('chat_id', str(chat_id))
    if thread_id:
        add_field('message_thread_id', str(thread_id))
    add_field('caption', caption)

    parts.append(f'--{boundary}\r\n'.encode())
    parts.append(f'Content-Disposition: form-data; name="video"; filename="{file_path.name}"\r\n'.encode())
    parts.append(f'Content-Type: {mime}\r\n\r\n'.encode())
    parts.append(file_path.read_bytes())
    parts.append(b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())

    body = b''.join(parts)
    req = urllib.request.Request(
        url=f'https://api.telegram.org/bot{token}/sendVideo',
        data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode())
        return bool(payload.get('ok')), json.dumps(payload)
    except Exception as e:
        return False, str(e)


def main(path_str: str):
    p = pathlib.Path(path_str)
    if not p.exists() or not p.is_file():
        print('ERROR: file not found')
        return 2
    if not in_workspace(p):
        print('ERROR: outside workspace')
        return 2
    if p.suffix.lower() not in ALLOWED_EXT:
        print('ERROR: disallowed file type')
        return 2
    if p.stat().st_size == 0:
        print(f'ERROR: file is empty | path={p}')
        return 2

    cleaned = p.with_name(clean_name(p))
    if cleaned != p:
        cleaned.write_bytes(p.read_bytes())

    upload_file = maybe_compress(cleaned)
    if upload_file.stat().st_size > MAX_BYTES:
        print(f'ERROR: file too large after compression: {upload_file}')
        return 3

    if upload_file.suffix.lower() in {'.mp4', '.mov'}:
        ok_media, media_msg = video_sanity_check(upload_file)
        if not ok_media:
            print(f'ERROR: media sanity check failed: {media_msg} | path={upload_file}')
            return 3

    token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_REPORTING_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    thread_id = os.getenv('TELEGRAM_THREAD_ID')

    if not token:
        print('ERROR: missing TELEGRAM_BOT_TOKEN/TELEGRAM_REPORTING_BOT_TOKEN')
        return 4

    if not chat_id:
        routes = pathlib.Path('/data/.openclaw/workspace/agents/telegram-routes.json')
        if routes.exists():
            r = json.loads(routes.read_text())
            tk = r.get('tiktok-production', {})
            chat_id = str(tk.get('chat_id', ''))
            thread_id = thread_id or str(tk.get('message_thread_id') or tk.get('thread_id') or '')

    if not chat_id:
        print('ERROR: missing TELEGRAM_CHAT_ID and no routing fallback')
        return 5

    ok, detail = send_video(token, str(chat_id), thread_id, upload_file, 'TikTok video ready for posting.')
    if ok:
        print('OK: uploaded')
        return 0

    print(f'ERROR: upload failed | {detail} | path={upload_file}')
    return 6


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('Usage: upload_media_telegram.py <workspace-media-file>')
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1]))
