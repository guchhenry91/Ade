#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const {spawnSync} = require('child_process');

const ROOT = '/data/.openclaw/workspace';
const REMOTION_DIR = path.join(ROOT, 'remotion');

function parseArgs() {
  const out = {};
  for (let i = 2; i < process.argv.length; i++) {
    const a = process.argv[i];
    if (a.startsWith('--')) {
      const k = a.slice(2);
      const v = process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[++i] : 'true';
      out[k] = v;
    }
  }
  return out;
}

function main() {
  const args = parseArgs();
  const date = args.date || new Date().toISOString().slice(0, 10);
  const title = args.title || 'Daily Insight';
  const scriptText = args.scriptText || 'Consistency beats intensity.';
  const subtitles = args.subtitles ? JSON.parse(args.subtitles) : ['Consistency beats intensity.', 'Small moves stack.', 'Start today.'];
  const backgroundStyle = args.backgroundStyle || 'gradient';
  const composition = args.composition || 'ai-voiceover-template';
  const audioTrack = args.audioTrack || '';
  const backgroundVideo = args.backgroundVideo || '';

  const outDir = path.join(ROOT, 'tiktok', 'videos', date);
  fs.mkdirSync(outDir, {recursive: true});
  const outFile = path.join(outDir, 'final.mp4');

  const publicDir = path.join(REMOTION_DIR, 'public');
  fs.mkdirSync(publicDir, {recursive: true});

  let audioPublicPath = '';
  let backgroundPublicPath = '';
  if (audioTrack && fs.existsSync(audioTrack)) {
    const audioTarget = path.join(publicDir, `voice-${date}.mp3`);
    fs.copyFileSync(audioTrack, audioTarget);
    audioPublicPath = `voice-${date}.mp3`;
  }

  if (backgroundVideo && fs.existsSync(backgroundVideo)) {
    const ext = path.extname(backgroundVideo) || '.mp4';
    const videoTarget = path.join(publicDir, `bg-${date}${ext}`);
    fs.copyFileSync(backgroundVideo, videoTarget);
    backgroundPublicPath = `bg-${date}${ext}`;
  }

  const props = {
    title,
    scriptText,
    subtitles,
    backgroundStyle,
    audioTrack: audioPublicPath,
    backgroundVideo: backgroundPublicPath,
  };

  const run = spawnSync('npx', ['remotion', 'render', 'src/index.ts', composition, outFile, '--props', JSON.stringify(props)], {
    cwd: REMOTION_DIR,
    stdio: 'pipe',
    encoding: 'utf-8',
  });

  if (run.status !== 0) {
    console.error(run.stderr || run.stdout || 'remotion render failed');
    process.exit(1);
  }

  console.log(outFile);
}

main();
