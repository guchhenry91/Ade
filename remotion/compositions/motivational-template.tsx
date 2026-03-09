import React from 'react';
import {AbsoluteFill, Audio, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
type Props = {
  title: string;
  scriptText: string;
  subtitles: string[];
  backgroundStyle: string;
  audioTrack?: string;
  backgroundVideo?: string;
};

export const MotivationalTemplate: React.FC<Props> = ({title, subtitles, backgroundStyle, audioTrack, backgroundVideo}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const scale = spring({frame, fps, config: {damping: 12}});
  const y = interpolate(frame, [0, 120], [80, 0], {extrapolateRight: 'clamp'});

  const bg = backgroundStyle === 'video'
    ? 'radial-gradient(circle at 20% 20%, #1f2937, #0b1020 60%, #05070f)'
    : 'linear-gradient(160deg, #111827 0%, #1e3a8a 50%, #0f172a 100%)';

  const subtitleIndex = Math.min(subtitles.length - 1, Math.floor(frame / 180));

  return (
    <AbsoluteFill style={{background: bg, color: 'white', fontFamily: 'Inter, system-ui'}}>
      {backgroundVideo ? <OffthreadVideo src={staticFile(backgroundVideo)} style={{width:'100%',height:'100%',objectFit:'cover',opacity:0.55}} /> : null}
      {audioTrack ? <Audio src={staticFile(audioTrack)} /> : null}
      <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', padding: 80}}>
        <h1 style={{fontSize: 84, textAlign: 'center', transform: `translateY(${y}px) scale(${scale})`, marginBottom: 40}}>{title}</h1>
        <div style={{fontSize: 56, textAlign: 'center', lineHeight: 1.2, background: 'rgba(255,255,255,0.08)', padding: 28, borderRadius: 24}}>
          {subtitles[subtitleIndex] || ''}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
