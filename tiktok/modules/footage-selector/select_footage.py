#!/usr/bin/env python3
import json, os, pathlib, sys, urllib.parse, urllib.request

ROOT = pathlib.Path('/data/.openclaw/workspace')
OUT = ROOT/'tiktok'/'assets'/'footage'
OUT.mkdir(parents=True, exist_ok=True)
query = sys.argv[1] if len(sys.argv)>1 else 'person working'
category = sys.argv[2] if len(sys.argv)>2 else 'lifestyle'

pexels_key = os.getenv('PEXELS_API_KEY')
pixabay_key = os.getenv('PIXABAY_API_KEY')

def download(url, dst):
    with urllib.request.urlopen(url, timeout=30) as r:
        dst.write_bytes(r.read())

selected = None
source = None
meta = {'query':query,'category':category,'candidates':[]}

try:
    if pexels_key:
        u='https://api.pexels.com/videos/search?'+urllib.parse.urlencode({'query':query,'per_page':5,'orientation':'portrait'})
        req=urllib.request.Request(u, headers={'Authorization':pexels_key})
        with urllib.request.urlopen(req, timeout=30) as r:
            data=json.loads(r.read().decode())
        for v in data.get('videos',[]):
            files=v.get('video_files',[])
            if not files: continue
            f=sorted(files, key=lambda x:(x.get('width',0)*x.get('height',0)), reverse=True)[0]
            meta['candidates'].append({'provider':'pexels','id':v.get('id'),'url':f.get('link')})
            if not selected and f.get('link'):
                selected=f.get('link'); source='pexels'
    if (not selected) and pixabay_key:
        u='https://pixabay.com/api/videos/?'+urllib.parse.urlencode({'key':pixabay_key,'q':query,'per_page':5})
        with urllib.request.urlopen(u, timeout=30) as r:
            data=json.loads(r.read().decode())
        for h in data.get('hits',[]):
            vids=h.get('videos',{})
            cand=(vids.get('large') or vids.get('medium') or vids.get('small') or {})
            if cand.get('url'):
                meta['candidates'].append({'provider':'pixabay','id':h.get('id'),'url':cand.get('url')})
                if not selected:
                    selected=cand.get('url'); source='pixabay'
except Exception as e:
    meta['error']=str(e)

if not selected:
    # fallback: use latest local footage clip
    previous = list((ROOT/'tiktok'/'videos').glob('*/final.mp4'))
    clips=sorted(list(OUT.glob('*.mp4'))+list((ROOT/'tiktok'/'source-footage').glob('*.mp4'))+previous, key=lambda p:p.stat().st_mtime, reverse=True)
    if clips:
        print(json.dumps({'ok':True,'source':'local','path':str(clips[0]),'meta':meta}))
        raise SystemExit(0)
    print(json.dumps({'ok':False,'error':'no footage available','meta':meta}))
    raise SystemExit(1)

filename = f"{category}-{query.replace(' ','-')[:30]}.mp4"
dst = OUT/filename
try:
    download(selected, dst)
    print(json.dumps({'ok':True,'source':source,'path':str(dst),'meta':meta}))
except Exception as e:
    print(json.dumps({'ok':False,'error':str(e),'meta':meta}))
    raise
