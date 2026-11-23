import React from 'react';
import Svg, { Circle, G, Path, Polyline, Rect, Text } from 'react-native-svg';

const ports = [
  [37, 144], [78, 120], [115, 145], [145, 100], [182, 130], [219, 109],
  [268, 110], [283, 70], [247, 39], [196, 48], [148, 35], [104, 60],
];
const shorelines = [
  'M0 179 V152 L22 140 L34 114 L53 116 L61 138 L48 151 L41 179 Z',
  'M68 102 L79 101 L87 110 L85 128 L96 141 L86 154 L66 149 L59 135 Z',
  'M114 90 L128 80 L146 81 L163 94 L156 112 L138 119 L128 110 Z',
  'M165 146 L172 135 L191 124 L207 139 L196 155 L176 159 Z',
  'M223 75 L239 58 L257 62 L272 78 L264 95 L245 103 L226 94 Z',
  'M132 0 H201 L206 23 L194 42 L169 48 L150 38 L125 23 Z',
  'M304 26 L320 20 V107 L303 102 L294 80 L296 54 Z',
];

const origin = [18, 159];
function points(route: number[][]) {
  return route.map(port => port.join(',')).join(' ');
}

export default function LighthouseRouteMap({ completed, sailing }: { completed: number; sailing: boolean }) {
  const current = ports[completed];
  return (
    <Svg width="100%" height={190} viewBox="0 0 320 180" accessible={false}>
      <Rect width="320" height="180" rx="10" fill="#10262e" />
      {shorelines.map(path => <Path key={path} d={path} fill="#28464a" stroke="#3b6060" strokeWidth="1" />)}
      <Polyline points={points([origin, ...ports])} fill="none" stroke="#7c9a9c" strokeWidth="1.5" strokeDasharray="3 4" />
      {completed > 0 && <Polyline points={points([origin, ...ports.slice(0, completed)])} fill="none" stroke="#86cbbb" strokeWidth="2.5" />}
      {sailing && current && <Polyline points={points([ports[completed - 1] ?? origin, current])}
        fill="none" stroke="#f4d38c" strokeWidth="2.5" strokeDasharray="5 3" />}
      <Circle cx="18" cy="159" r="4" fill="#86cbbb" />
      {ports.map(([x, y], index) => {
        const active = index === completed;
        const reached = index < completed;
        const ink = active ? '#f4d38c' : reached ? '#86cbbb' : '#a0b8ba';
        return <G key={index}>
          {active && <Circle cx={x} cy={y} r="13" stroke={ink} strokeWidth="1" fill="none" />}
          <Circle cx={x} cy={y} r="9.5" fill={reached ? ink : '#10262e'} stroke={ink} strokeWidth="1.4" />
          <Text x={x} y={y + 3.2} fill={reached ? '#10262e' : ink} fontSize="9"
            fontWeight="700" textAnchor="middle">{index + 1}</Text>
        </G>;
      })}
    </Svg>
  );
}
