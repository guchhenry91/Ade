import React from 'react';
import {AbsoluteFill, Audio, OffthreadVideo, interpolate, staticFile, useCurrentFrame} from 'remotion';

type Props = {
  title: string;
  scriptText: string;
  subtitles: string[];
  backgroundStyle: string;
  audioTrack?: string;
  backgroundVideo?: string;
};

export const AIVoiceoverTemplate: React.FC<Props> = ({title, subtitles, audioTrack, backgroundVideo}) => {
  const frame = useCurrentFrame();
  const pulse = interpolate(Math.sin(frame / 10), [-1, 1], [0.95, 1.05]);
  const subtitleIndex = Math.min(subtitles.length - 1, Math.floor(frame / 180));

  return (
    <AbsoluteFill style={{background: 'linear-gradient(180deg,#020617,#111827)', color: '#fff', fontFamily: 'Inter, system-ui'}}>
      {backgroundVideo ? <OffthreadVideo src={staticFile(backgroundVideo)} style={{width:'100%',height:'100%',objectFit:'cover',opacity:.35}} /> : null}
      {audioTrack ? <Audio src={staticFile(audioTrack)} /> : null}
      <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', gap: 30, padding: 80}}>
        <div style={{fontSize: 64, fontWeight: 700}}>{title}</div>
        <div style={{width: 260, height: 260, borderRadius: '50%', background: 'rgba(59,130,246,0.45)', transform: `scale(${pulse})`}} />
        <div style={{fontSize: 48, textAlign: 'center', background: 'rgba(255,255,255,0.08)', padding: 24, borderRadius: 20}}>
          {subtitles[subtitleIndex] || ''}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
