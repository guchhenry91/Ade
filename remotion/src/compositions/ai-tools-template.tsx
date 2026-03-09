import React from 'react';
import {AbsoluteFill, Audio, OffthreadVideo, staticFile, useCurrentFrame} from 'remotion';

type Props={title:string;subtitles:string[];audioTrack?:string;backgroundVideo?:string};

export const AIToolsTemplate: React.FC<Props> = ({title, subtitles, audioTrack, backgroundVideo}) => {
  const f=useCurrentFrame();
  const idx=Math.min(subtitles.length-1,Math.floor(f/150));
  const pulse=1+0.04*Math.sin(f/8);
  return <AbsoluteFill style={{background:'linear-gradient(180deg,#020617,#0f172a)',color:'#e5e7eb',fontFamily:'Inter,system-ui'}}>
    {backgroundVideo?<OffthreadVideo src={staticFile(backgroundVideo)} style={{width:'100%',height:'100%',objectFit:'cover',opacity:0.35}}/>:null}
    <AbsoluteFill style={{background:'rgba(2,6,23,.45)'}} />
    {audioTrack?<Audio src={staticFile(audioTrack)}/>:null}
    <AbsoluteFill style={{padding:70,justifyContent:'space-between'}}>
      <div style={{fontSize:70,fontWeight:800,transform:`scale(${pulse})`,transformOrigin:'left top'}}>{title}</div>
      <div style={{fontSize:48,background:'rgba(59,130,246,.18)',padding:24,borderRadius:20}}>{subtitles[idx]||''}</div>
      <div style={{fontSize:32,opacity:.9}}>Use this before everyone else does.</div>
    </AbsoluteFill>
  </AbsoluteFill>;
};
