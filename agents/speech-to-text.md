# Speech-to-Text (STT) Capability

Status: enabled
Engine: `faster-whisper` (local CPU)

## Script
- `workspace/agents/speech_to_text.py`

## Allowed input paths
- Only files under `/data/.openclaw/workspace/`

## Supported media types
- `.mp3`, `.wav`, `.m4a`, `.ogg`, `.mp4`, `.mov`, `.webm`

## Output
- Text transcript: `workspace/stt/transcripts/<filename>.txt`
- Segment JSON: `workspace/stt/transcripts/<filename>.json`

## Usage
```bash
python3 /data/.openclaw/workspace/agents/speech_to_text.py /data/.openclaw/workspace/<media-file>
```
