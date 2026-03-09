#!/usr/bin/env python3
import json, sys

hook = sys.argv[1] if len(sys.argv)>1 else 'Stop scrolling.'
topic = sys.argv[2] if len(sys.argv)>2 else 'growth'
insight = f'Use one clear {topic} system instead of random tips.'
payoff = f'Try this for 7 days and track results. Follow for more {topic} breakdowns.'
script = {
  'timeline': {
    '0-2s': hook,
    '3-12s': insight,
    '13-20s': payoff
  },
  'lines': [hook, insight, payoff]
}
print(json.dumps(script))
