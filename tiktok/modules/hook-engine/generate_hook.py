#!/usr/bin/env python3
import json, sys, hashlib

topic = (sys.argv[1] if len(sys.argv)>1 else 'general').lower()
formats = {
  'curiosity': [
    'Nobody tells you this about {topic} until it is too late.',
    'The {topic} trick most people discover years too late.'
  ],
  'contrarian': [
    'Everything you heard about {topic} is backwards.',
    'Stop doing this common {topic} advice immediately.'
  ],
  'pain-point': [
    'If {topic} still feels hard, this is probably why.',
    'This hidden {topic} mistake is costing you every week.'
  ],
  'authority': [
    'Top creators use this {topic} framework every day.',
    'Experts won’t skip this {topic} step.'
  ],
  'data-shock': [
    'Most people lose results in the first 7 days of {topic}.',
    '80% of people fail {topic} because of one avoidable mistake.'
  ]
}
order = list(formats.keys())
h = int(hashlib.md5(topic.encode()).hexdigest(),16)
fmt = order[h % len(order)]
choices = formats[fmt]
hook = choices[(h>>8) % len(choices)].format(topic=topic)
print(json.dumps({'format':fmt,'hook':hook}))
