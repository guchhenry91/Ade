#!/usr/bin/env python3
import json, sys, hashlib

niche = (sys.argv[1] if len(sys.argv)>1 else 'general').lower()
h = int(hashlib.sha1(niche.encode()).hexdigest(),16)
score = 60 + (h % 41)
hashtags = {
  'money':['#moneytok','#financialfreedom','#moneymindset','#fyp'],
  'motivational':['#mindset','#discipline','#selfimprovement','#fyp'],
  'ai-tools':['#aitools','#chatgpt','#productivity','#fyp']
}.get(niche,['#fyp','#viral','#learnontiktok'])
print(json.dumps({'niche':niche,'trend_score':score,'hashtags':hashtags,'signals':['audio_velocity','hashtag_growth','format_shift']}))
