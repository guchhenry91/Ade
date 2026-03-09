# TikTok Content Agent Workflow

## Schedule
- Run daily at 10:00 AM via cron (`jobs/topic-routing-cron.txt`).

## Per-run outputs
- Vertical MP4 package in `workspace/tiktok/videos/YYYY-MM-DD/`
- Caption and hashtags files
- Approval request in `workspace/agents/approvals/tiktok-YYYY-MM-DD.md`

## Auto-delivery to Telegram
After video generation completes, run:

`python3 /data/.openclaw/workspace/tiktok/deliver_to_telegram.py /data/.openclaw/workspace/tiktok/videos/YYYY-MM-DD/final.mp4`

This delivery step enforces:
- file must be inside `/data/.openclaw/workspace/`
- only allowed types (`mp4`, `mov`, `png`, `jpg`)
- safe delivery filename: `tiktok-video-YYYY-MM-DD.mp4`
- Telegram bot size limit (50MB) with automatic compression attempt if needed
- upload via Telegram Bot API `sendVideo` with caption: `TikTok video ready for posting.`
- errors are reported while keeping files in workspace

## Required quality gate (Commander)
Commander verifies before publish:
- MP4 format
- 1080x1920 resolution (9:16)
- clear audio quality
- strong first-3s hook

If verification fails:
- regenerate video package
- replace artifacts
- keep status in `waiting approval` until pass

## Notification target
- All updates and approvals go to `tiktok-production` topic.

## Learning loop
After publish, track and store in `workspace/mission-control/learning-data.json`:
- views
- likes
- shares
- comments
Use trends to improve future hook style and creative direction.
