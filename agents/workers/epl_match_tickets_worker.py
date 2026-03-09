#!/usr/bin/env python3
from __future__ import annotations
import json, pathlib, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

ROOT = pathlib.Path('/data/.openclaw/workspace')
REPORT_CFG = ROOT / 'agents' / 'reporting-config.json'
STATE = ROOT / 'agents' / 'status' / 'epl-tickets-state.json'


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={'User-Agent': 'OpenClaw/1.0'})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def pct(rec: str) -> float:
    import re
    m = re.match(r'^(\d+)-(\d+)-(\d+)$', rec or '')
    if not m:
        return 0.5
    w, d, l = map(int, m.groups())
    return (w + 0.5 * d) / max(1, w + d + l)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"sent_event_ids": []}


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2) + "\n")


def tg_send(token: str, chat_id: int, thread_id: int, text: str):
    data = urllib.parse.urlencode({
        'chat_id': chat_id,
        'message_thread_id': thread_id,
        'text': text
    }).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def main():
    cfg = json.loads(REPORT_CFG.read_text())
    token = cfg.get('reporting_bot', {}).get('token')
    topic = cfg.get('topics', {}).get('sports-analytics', {})
    chat_id = topic.get('chat_id')
    thread_id = topic.get('message_thread_id')
    if not token or chat_id is None or thread_id is None:
        return

    now = datetime.now(timezone.utc)
    start = now.strftime('%Y%m%d')
    end = (now + timedelta(days=7)).strftime('%Y%m%d')
    board = fetch_json(f'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard?dates={start}-{end}')
    events = board.get('events', [])

    st = load_state()
    sent = set(st.get('sent_event_ids', []))

    for e in sorted(events, key=lambda x: x.get('date', '')):
        event_id = str(e.get('id', ''))
        if event_id in sent:
            continue

        try:
            kick = datetime.fromisoformat(e['date'].replace('Z', '+00:00'))
        except Exception:
            continue

        mins = (kick - now).total_seconds() / 60
        # Send around 3h before each match
        if not (150 <= mins <= 230):
            continue

        c = (e.get('competitions') or [{}])[0].get('competitors') or []
        home = next((x for x in c if x.get('homeAway') == 'home'), None)
        away = next((x for x in c if x.get('homeAway') == 'away'), None)
        if not home or not away:
            continue

        hs = pct(((home.get('records') or [{}])[0]).get('summary', '')) + 0.08
        aws = pct(((away.get('records') or [{}])[0]).get('summary', ''))
        total = max(0.01, hs + aws)
        hp, ap = hs / total, aws / total

        draw = max(0.2, 0.27 - abs(hp - ap) * 0.15)
        hwin = hp * (1 - draw)
        awin = ap * (1 - draw)

        if hwin >= awin:
            winner = home['team']['displayName']
            win_prob = hwin
            safer = f"{home['team']['displayName']} or Draw (1X)"
            safer_prob = hwin + draw
        else:
            winner = away['team']['displayName']
            win_prob = awin
            safer = f"{away['team']['displayName']} or Draw (X2)"
            safer_prob = awin + draw

        confidence = int(max(53, min(82, round(52 + abs(hwin - awin) * 130))))

        text = (
            f"@HenryAgentsbot EPL per-match ticket update\n\n"
            f"Match/Game Analysis\n"
            f"- {away['team']['displayName']} @ {home['team']['displayName']}\n"
            f"  • Predicted winner: {winner}\n"
            f"  • Win probability: {round(win_prob*100,1)}%\n"
            f"  • Confidence rating: {confidence}%\n\n"
            f"Safest 3-Leg Parlay\n"
            f"- Ticket A (strict win-only): {winner} to win\n"
            f"- Ticket B (safer): {safer}\n"
            f"- Risk note: strict win-only has lower hit rate; safer ticket trades payout for consistency.\n\n"
            f"Value / Upset Opportunities\n"
            f"- Strict value side: {winner} ML if market implied probability is below {round(win_prob*100,1)}%."
        )

        tg_send(token, chat_id, thread_id, text[:3900])
        sent.add(event_id)

    st['sent_event_ids'] = list(sent)[-2000:]
    save_state(st)


if __name__ == '__main__':
    main()
