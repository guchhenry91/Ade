import React from 'react';
import {AbsoluteFill, Audio, OffthreadVideo, Sequence, staticFile, useCurrentFrame} from 'remotion';
type Props = {
  title: string;
  scriptText: string;
  subtitles: string[];
  backgroundStyle: string;
  audioTrack?: string;
  backgroundVideo?: string;
};

export const StorytellingTemplate: React.FC<Props> = ({title, scriptText, subtitles, audioTrack, backgroundVideo}) => {
  const frame = useCurrentFrame();
  const blocks = scriptText.split('.').filter(Boolean);

  return (
    <AbsoluteFill style={{background: 'linear-gradient(145deg,#0b1220,#1a1f35)', color: '#fff', fontFamily: 'Inter, system-ui'}}>
      {backgroundVideo ? <OffthreadVideo src={staticFile(backgroundVideo)} style={{width:'100%',height:'100%',objectFit:'cover',opacity:.45}} /> : null}
      {audioTrack ? <Audio src={staticFile(audioTrack)} /> : null}
      <AbsoluteFill style={{padding: 70, justifyContent: 'space-between'}}>
        <h1 style={{fontSize: 72, margin: 0}}>{title}</h1>
        <div style={{display: 'grid', gap: 14}}>
          {blocks.slice(0,3).map((b, i) => (
            <Sequence key={i} from={i * 120}>
              <div style={{fontSize: 44, background: 'rgba(255,255,255,0.08)', padding: 22, borderRadius: 20, opacity: frame > i * 120 ? 1 : 0}}>
                {b.trim()}.
              </div>
            </Sequence>
          ))}
        </div>
        <div style={{fontSize: 38, opacity: 0.9}}>{subtitles[Math.min(subtitles.length - 1, Math.floor(frame / 180))] || ''}</div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
