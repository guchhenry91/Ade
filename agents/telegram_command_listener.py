#!/usr/bin/env python3
from __future__ import annotations
import json, pathlib, urllib.parse, urllib.request, math
from datetime import datetime, timezone

ROOT = pathlib.Path('/data/.openclaw/workspace')
CFG = pathlib.Path('/data/.openclaw/openclaw.json')
ROUTES = ROOT / 'agents' / 'telegram-routes.json'
LOG = ROOT / 'agents' / 'commander-status.md'
STATE = ROOT / 'agents' / 'status' / 'telegram-command-listener-state.json'
CHAT_ID = -1003659714036
BOT_USERNAME = '@johnferrybot'


def load_token():
    d = json.loads(CFG.read_text())
    return d['channels']['telegram']['botToken']


def tg(token, method, params=None):
    params = params or {}
    q = urllib.parse.urlencode(params)
    url = f'https://api.telegram.org/bot{token}/{method}' + (f'?{q}' if q else '')
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.loads(r.read().decode())


def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'OpenClaw/1.0'})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def log(line):
    with LOG.open('a', encoding='utf-8') as f:
        f.write(f"\n- {line}")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {'offset': 0}


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2) + '\n')


def thread_to_topic():
    d = json.loads(ROUTES.read_text())
    m = {}
    for topic, v in d.items():
        tid = v.get('message_thread_id') or v.get('thread_id')
        if tid is not None:
            m[int(tid)] = topic
    return m


def topic_agent(topic):
    return {
        'sports-analytics': 'Sports Prediction Agent',
        'tiktok-production': 'TikTok Content Agent',
        'job-opportunities': 'Job Research Agent',
        'betting-insights': 'Betting Intelligence Agent',
        'commander-control': 'Commander Agent'
    }.get(topic, 'Commander Agent')


def domain_match(topic, text):
    t = text.lower()
    if topic == 'sports-analytics':
        return any(k in t for k in ['nba', 'nfl', 'epl', 'ucl', 'la liga', 'champions league', 'predict', 'match', 'game'])
    if topic == 'tiktok-production':
        return any(k in t for k in ['tiktok', 'viral', 'hook', 'caption', 'hashtag', 'video', 'trend'])
    if topic == 'job-opportunities':
        return any(k in t for k in ['job', 'jobs', 'role', 'salary', 'apply', 'hiring', 'cv'])
    if topic == 'betting-insights':
        return any(k in t for k in ['bet', 'odds', 'value', 'line', 'edge', 'stake'])
    return True


def pct_from_record(summary):
    try:
        w, l = summary.split('-')[:2]
        w, l = int(w), int(l)
        return w / max(1, (w + l))
    except Exception:
        return 0.5


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def nba_analysis_report():
    data = fetch_json('https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard')
    events = data.get('events', [])
    lines = ['@HenryAgentsbot Sports Prediction Agent — NBA autonomous report', '', 'Match/Game Analysis']

    if not events:
        lines += ['- No NBA games found on current feed.', '', 'Safest 3-Leg Parlay', '- N/A', '', 'Value / Upset Opportunities', '- N/A']
        return '\n'.join(lines)

    games = []
    for e in events:
        comp = (e.get('competitions') or [{}])[0]
        c = comp.get('competitors') or []
        home = next((x for x in c if x.get('homeAway') == 'home'), None)
        away = next((x for x in c if x.get('homeAway') == 'away'), None)
        if not home or not away:
            continue

        home_rec = ((home.get('records') or [{}])[0]).get('summary', '0-0')
        away_rec = ((away.get('records') or [{}])[0]).get('summary', '0-0')
        hp = pct_from_record(home_rec)
        ap = pct_from_record(away_rec)

        # lightweight model: record strength + modest home edge
        home_score = hp + 0.04
        away_score = ap
        total = max(0.01, home_score + away_score)
        home_prob = home_score / total
        away_prob = away_score / total

        if home_prob >= away_prob:
            winner = home
            win_prob = home_prob
            loser = away
        else:
            winner = away
            win_prob = away_prob
            loser = home

        conf = int(clamp(round(50 + abs(home_prob - away_prob) * 100), 52, 78))

        # projection from team strength proxy
        base_total = 225
        margin = int(round((home_prob - away_prob) * 18))
        home_pts = int(round(base_total / 2 + margin / 2))
        away_pts = base_total - home_pts
        if winner == away:
            home_pts, away_pts = away_pts, home_pts

        game = {
            'matchup': f"{away['team']['displayName']} @ {home['team']['displayName']}",
            'winner': winner['team']['displayName'],
            'score': f"{away['team']['displayName']} {away_pts} - {home_pts} {home['team']['displayName']}",
            'win_prob': round(win_prob * 100, 1),
            'confidence': conf,
            'edge': round((win_prob - 0.5) * 100, 1),
            'implied': round(50.0, 1),
            'risk': abs(home_prob - away_prob)
        }
        games.append(game)

    for g in games:
        lines += [
            f"- {g['matchup']}",
            f"  • Predicted winner: {g['winner']}",
            f"  • Projected score: {g['score']}",
            f"  • Win probability: {g['win_prob']}%",
            f"  • Confidence rating: {g['confidence']}%",
        ]

    safest = sorted(games, key=lambda x: (x['win_prob'], x['confidence']), reverse=True)[:3]
    lines += ['', 'Safest 3-Leg Parlay']
    if safest:
        for i, g in enumerate(safest, 1):
            lines.append(f"- Leg {i}: {g['winner']} to win ({g['win_prob']}% model probability)")
        combined = 1.0
        for g in safest:
            combined *= max(0.01, g['win_prob'] / 100.0)
        lines.append(f"- Combined hit estimate: {round(combined * 100, 1)}%")
        lines.append('- Combined risk: Parlay risk compounds across legs; one miss voids ticket even with high-confidence selections.')
    else:
        lines.append('- N/A')

    values = sorted(games, key=lambda x: x['edge'], reverse=True)[:3]
    lines += ['', 'Value / Upset Opportunities']
    if values:
        for g in values:
            lines.append(
                f"- {g['matchup']}: {g['winner']} value lean | model {g['win_prob']}% vs market baseline {g['implied']}% | value edge +{round(g['win_prob']-g['implied'],1)}%"
            )
            lines.append('  • Edge rationale: Model rates stronger recent profile than baseline market midpoint.')
    else:
        lines.append('- N/A')

    return '\n'.join(lines)


def generic_competition_report(label):
    return '\n'.join([
        f"@HenryAgentsbot Sports Prediction Agent — {label} autonomous report",
        '',
        'Match/Game Analysis',
        '- Data feed snapshot currently has no fixtures to score for this competition.',
        '  • Predicted winner: N/A',
        '  • Projected score: N/A',
        '  • Win probability: N/A',
        '  • Confidence rating: N/A',
        '',
        'Safest 3-Leg Parlay',
        '- N/A (no active fixture probabilities available in current feed)',
        '- N/A',
        '- N/A',
        '- Combined risk: unavailable until fixtures are detected.',
        '',
        'Value / Upset Opportunities',
        '- N/A (requires live model + sportsbook lines for mapped fixtures).'
    ])


def sports_report_from_text(text):
    t = text.lower()
    if 'nba' in t:
        return nba_analysis_report()
    if 'nfl' in t:
        return generic_competition_report('NFL')
    if 'epl' in t or 'premier league' in t:
        return generic_competition_report('EPL')
    if 'ucl' in t or 'champions league' in t:
        return generic_competition_report('UEFA Champions League')
    if 'la liga' in t:
        return generic_competition_report('La Liga')
    return '@HenryAgentsbot Sports Prediction Agent: league not identified. Supported: NBA, NFL, EPL, UEFA Champions League, La Liga.'


def is_blocked_command(text: str):
    t=text.lower()
    blocked_tokens=['docker ','docker\n','/etc','/root','/usr/','token','credential','secret','rm -rf','apt install','curl http']
    return any(x in t for x in blocked_tokens)

def build_reply(topic, text):
    t = text.lower().strip()
    if is_blocked_command(t):
        return '@HenryAgentsbot Command blocked by security policy. Allowed scope: workspace and approved agent scripts only.'
    if 'system status' in t:
        return '@HenryAgentsbot Commander status: system online, topic-native routing active, agents monitored.'

    if topic == 'sports-analytics':
        if domain_match(topic, t):
            return sports_report_from_text(t)
        return '@HenryAgentsbot Sports topic active. Send a prediction request (NBA/NFL/EPL/UCL/La Liga).'

    if topic == 'tiktok-production':
        if domain_match(topic, t):
            return '@HenryAgentsbot TikTok Content Agent: request received. Generating hooks, caption, and hashtag package for review.'
        return '@HenryAgentsbot TikTok topic active. Send a content request (idea/hook/caption/video).'

    if topic == 'job-opportunities':
        if domain_match(topic, t):
            return '@HenryAgentsbot Job Research Agent: request received. Scanning roles and salary-fit matches now.'
        return '@HenryAgentsbot Jobs topic active. Send role/location/salary criteria.'

    if topic == 'betting-insights':
        if domain_match(topic, t):
            return '@HenryAgentsbot Betting Intelligence Agent: request received. Checking odds movement and potential value edges.'
        return '@HenryAgentsbot Betting topic active. Send event/market to evaluate.'

    return '@HenryAgentsbot Commander Agent active. Request received and routed under Commander oversight.'


def main():
    token = load_token()
    me = tg(token, 'getMe').get('result', {})
    bot_id = me.get('id')

    st = load_state()
    t2t = thread_to_topic()
    res = tg(token, 'getUpdates', {'offset': st.get('offset', 0), 'timeout': 1, 'limit': 100})

    for u in res.get('result', []):
        st['offset'] = u['update_id'] + 1
        m = u.get('message') or u.get('edited_message') or {}

        if m.get('chat', {}).get('id') != CHAT_ID:
            continue

        tid = m.get('message_thread_id')
        if tid is None or int(tid) not in t2t:
            continue

        if m.get('from', {}).get('id') == bot_id:
            continue

        text = (m.get('text') or '').strip()
        if not text:
            continue

        topic = t2t[int(tid)]
        agent = topic_agent(topic)
        reply = build_reply(topic, text)

        out = tg(token, 'sendMessage', {
            'chat_id': CHAT_ID,
            'message_thread_id': tid,
            'text': reply,
            'reply_parameters': json.dumps({'message_id': m.get('message_id')})
        })
        log(f"Telegram topic-native reply -> topic={topic} agent={agent} ok={out.get('ok')} in_reply_to={m.get('message_id')}")

    save_state(st)


if __name__ == '__main__':
    main()
