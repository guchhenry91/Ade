import React from 'react';
import {AbsoluteFill, Audio, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame} from 'remotion';

type Props={title:string;subtitles:string[];audioTrack?:string;backgroundVideo?:string};

export const FacelessMoneyTemplate: React.FC<Props> = ({title, subtitles, audioTrack, backgroundVideo}) => {
  const f=useCurrentFrame();
  const sub=subtitles[Math.min(subtitles.length-1,Math.floor(f/160))]||'';
  const zoom=interpolate((f%90),[0,90],[1,1.08]);
  const interrupt = Math.floor(f/75)%2===0;
  return <AbsoluteFill style={{background:'#0b1220',color:'#fff',fontFamily:'Inter,system-ui'}}>
    {backgroundVideo?<OffthreadVideo src={staticFile(backgroundVideo)} style={{width:'100%',height:'100%',objectFit:'cover',opacity:0.45}}/>:null}
    <AbsoluteFill style={{background: interrupt?'rgba(15,23,42,.45)':'rgba(17,24,39,.35)'}} />
    {audioTrack?<Audio src={staticFile(audioTrack)}/>:null}
    <AbsoluteFill style={{justifyContent:'center',alignItems:'center',padding:70,transform:`scale(${zoom})`}}>
      <div style={{fontSize:78,fontWeight:800,textAlign:'center',background:'rgba(0,0,0,.35)',padding:20,borderRadius:20}}> {title} </div>
      <Sequence from={20}><div style={{marginTop:30,fontSize:52,lineHeight:1.15,textAlign:'center',background:'rgba(255,255,255,.1)',padding:24,borderRadius:18}}>{sub}</div></Sequence>
    </AbsoluteFill>
  </AbsoluteFill>;
};
