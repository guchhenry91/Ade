import React from 'react';
import {Composition} from 'remotion';
import {MotivationalTemplate} from './compositions/motivational-template';
import {StorytellingTemplate} from './compositions/storytelling-template';
import {AIVoiceoverTemplate} from './compositions/ai-voiceover-template';
import {FacelessMoneyTemplate} from './compositions/faceless-money-template';
import {AIToolsTemplate} from './compositions/ai-tools-template';

export const Root: React.FC = () => {
  const defaultProps = {
    title: 'Daily Insight',
    scriptText: 'Your consistency beats motivation. Keep going.',
    subtitles: [
      'Your consistency beats motivation.',
      'Small steps compound over time.',
      'Keep going.'
    ],
    backgroundStyle: 'gradient',
    audioTrack: '',
    backgroundVideo: ''
  };

  return (
    <>
      <Composition id="motivational-template" component={MotivationalTemplate} durationInFrames={600} fps={30} width={1080} height={1920} defaultProps={defaultProps} />
      <Composition id="storytelling-template" component={StorytellingTemplate} durationInFrames={600} fps={30} width={1080} height={1920} defaultProps={defaultProps} />
      <Composition id="ai-voiceover-template" component={AIVoiceoverTemplate} durationInFrames={600} fps={30} width={1080} height={1920} defaultProps={defaultProps} />
      <Composition id="faceless-money-template" component={FacelessMoneyTemplate} durationInFrames={600} fps={30} width={1080} height={1920} defaultProps={defaultProps} />
      <Composition id="ai-tools-template" component={AIToolsTemplate} durationInFrames={600} fps={30} width={1080} height={1920} defaultProps={defaultProps} />
    </>
  );
};
