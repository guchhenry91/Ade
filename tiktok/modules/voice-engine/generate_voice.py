#!/usr/bin/env python3
import json, os, pathlib, sys, subprocess, urllib.request

ROOT = pathlib.Path('/data/.openclaw/workspace')
OUT = ROOT/'tiktok'/'audio'/'voiceover.wav'
OUT.parent.mkdir(parents=True, exist_ok=True)
text = sys.argv[1] if len(sys.argv)>1 else 'Hello from OpenClaw.'
voice_id = os.getenv('ELEVENLABS_VOICE_ID','EXAVITQu4vr4xnSDxMaL')
api_key = os.getenv('ELEVENLABS_API_KEY')

if api_key:
    url = f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}'
    payload = json.dumps({
      'text': text,
      'model_id': 'eleven_multilingual_v2',
      'voice_settings': {'stability':0.45,'similarity_boost':0.8,'style':0.35,'use_speaker_boost':True}
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={'xi-api-key':api_key,'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        audio = r.read()
    mp3 = OUT.with_suffix('.mp3'); mp3.write_bytes(audio)
    ffmpeg = '/data/.openclaw/workspace/tools/ffmpeg-static/ffmpeg'
    subprocess.run([ffmpeg,'-y','-i',str(mp3),str(OUT)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps({'ok':True,'engine':'elevenlabs','path':str(OUT)}))
else:
    code = f"from gtts import gTTS\ngTTS(text={text!r},lang='en',slow=False).save({str(OUT.with_suffix('.mp3'))!r})\n"
    r = subprocess.run([sys.executable,'-c',code], capture_output=True, text=True)
    if r.returncode != 0:
        print(json.dumps({'ok':False,'error':r.stderr or r.stdout}))
        raise SystemExit(1)
    ffmpeg = '/data/.openclaw/workspace/tools/ffmpeg-static/ffmpeg'
    subprocess.run([ffmpeg,'-y','-i',str(OUT.with_suffix('.mp3')),str(OUT)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps({'ok':True,'engine':'gtts','path':str(OUT)}))
