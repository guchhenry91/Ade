#!/usr/bin/env python3
import json, sys

lines = json.loads(sys.argv[1]) if len(sys.argv)>1 else ['Line one','Line two','Line three']
out = []
start = 0.0
for ln in lines:
    words = ln.split()
    dur = max(2.5, min(6.0, len(words)*0.45))
    out.append({'text':ln,'start':round(start,2),'end':round(start+dur,2),'highlight_words':[w for w in words[:2]]})
    start += dur
print(json.dumps({'captions':out,'style':'mobile-bold-highlight'}))
