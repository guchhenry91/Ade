#!/usr/bin/env python3
from __future__ import annotations
import json
import pathlib
import sys
from faster_whisper import WhisperModel

WORKSPACE = pathlib.Path('/data/.openclaw/workspace').resolve()
ALLOWED = {'.mp3', '.wav', '.m4a', '.ogg', '.mp4', '.mov', '.webm'}


def in_workspace(p: pathlib.Path) -> bool:
    try:
        p.resolve().relative_to(WORKSPACE)
        return True
    except Exception:
        return False


def main(path_str: str):
    p = pathlib.Path(path_str)
    if not p.exists() or not p.is_file():
        print('ERROR: file not found')
        return 2
    if not in_workspace(p):
        print('ERROR: outside workspace')
        return 2
    if p.suffix.lower() not in ALLOWED:
        print('ERROR: unsupported media type')
        return 2

    model = WhisperModel('small', device='cpu', compute_type='int8')
    segments, info = model.transcribe(str(p), vad_filter=True)

    texts = []
    seg_rows = []
    for s in segments:
        t = s.text.strip()
        if t:
            texts.append(t)
        seg_rows.append({'start': s.start, 'end': s.end, 'text': t})

    out_dir = WORKSPACE / 'stt' / 'transcripts'
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = p.stem
    txt_path = out_dir / f'{stem}.txt'
    json_path = out_dir / f'{stem}.json'

    txt_path.write_text('\n'.join(texts) + '\n', encoding='utf-8')
    json_path.write_text(json.dumps({
        'source': str(p),
        'language': getattr(info, 'language', None),
        'duration': getattr(info, 'duration', None),
        'segments': seg_rows
    }, indent=2) + '\n', encoding='utf-8')

    print(f'OK: transcript saved -> {txt_path}')
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: speech_to_text.py <workspace-media-file>')
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1]))
