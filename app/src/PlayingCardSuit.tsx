import React from 'react';
import Svg, { Path } from 'react-native-svg';

const suitPaths: Record<string, string> = {
  '♠': 'M8 1C5 5 1 7 1 10a3.5 3.5 0 0 0 6 2l-1 3h4l-1-3a3.5 3.5 0 0 0 6-2c0-3-4-5-7-9Z',
  '♥': 'M8 14 2 8a4 4 0 0 1 6-5 4 4 0 0 1 6 5Z',
  '♣': 'M6 7a3 3 0 1 1 4 0 3.5 3.5 0 1 1-1 6l1 2H6l1-2a3.5 3.5 0 1 1-1-6Z',
  '♦': 'M8 1 14 8 8 15 2 8Z',
};

export default function PlayingCardSuit({ suit, color, size }: { suit: string; color: string; size: number }) {
  return <Svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
    <Path d={suitPaths[suit]} fill={color} />
  </Svg>;
}
