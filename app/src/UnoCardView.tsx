// UNO 牌面渲染 —— 对局屏与战绩回放**共用一份**。
//
// 拆出来的理由与 LudoBoardView 相同：牌面美术只有在两处用同一份实现时，
// 「战绩里看到的牌」才和「打的时候看到的牌」长得一样（曾经各写一份，
// 同一张牌在两个页面颜色不同）。
//
// 形制照实体 UNO 牌（联网调研后重做，v1.9）：
// - 整张牌 = 色块 + **白色椭圆环** + 中央大字形 + **左上/右下角的小字形**。
//   角标不是装饰：手牌是扇形重叠着摆的（每张只露出左边缘），**只有角标能在
//   重叠状态下被认出来**——这就是实体牌把牌面复制到角上的原因。
// - 牌背 = 黑底 + 白边 + 倾斜的红色椭圆（UNO 的招牌背面）。
//
// 可访问性（实测值见 theme.ts 的注释）：
// - 四色牌上的字形一律**深墨**（红 3.74 / 绿 5.14 / 蓝 4.33 / 黄 11.68 全部 ≥3）；
//   白字在黄牌上只有 1.52，那是 2048 黄格 1.42:1 的同一个坑。**黑牌例外**——
//   深墨压黑牌只有 1.21，那里用白字（14.68）。
// - 白色椭圆环在黄牌上只有 1.52（**纯装饰**，实体 UNO 牌就是这样）：
//   牌面信息由深墨字形 + 角标 + 形状徽标承载，不依赖这条环线。
// - **颜色不单独承载信息**：每张牌角上画一个专属形状徽标（红圆/蓝方/绿三角/黄星），
//   读不出颜色也认得出是哪一色。
// - **可出牌的第二通道是「位移 + 双环」**，不是颜色：可出的牌整体上移 12px，
//   并套上「内白外墨」双环。没有任何单色环能在四色+黑牌上都可见
//   （深墨压黑牌 1.21、白色压黄牌 1.52），成对出现则永远至少有一环可见。
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Ellipse, Polygon, Text as SvgText } from 'react-native-svg';
import {
  fontSize, radius, unoColors, unoWild, unoInk, unoOnWild, unoBackEdge, unoOnTableMuted,
} from './theme';
import { UnoColorBadge, UnoSymbolGlyph } from './icons';
import { cardColor, cardKind, isWild, cardLabel } from './uno';

/** 牌的默认尺寸。手牌横排时按屏宽再压一档（见 UnoScreen 的扇形布局）。 */
export const UNO_CARD_W = 64;
export const UNO_CARD_H = 96;

/**
 * 小于这个宽度就不画角标。
 *
 * 角标字号是 `width * 0.2`，26px 宽的牌（战绩回放里就是这么大）算出来只有 5px，
 * 既看不清、又会被 `visual_audit` 的「可见文字 ≥10px」扫出来。小牌本来也不
 * 重叠摆，不需要靠角标认牌。
 */
const CORNER_MIN_W = 52;

export function UnoCardView({
  card,
  width = UNO_CARD_W,
  height = UNO_CARD_H,
  faceDown = false,
  playable = false,
  chosenColor,
}: {
  card: string;
  width?: number;
  height?: number;
  /** 牌背（别人的手牌只显示张数，不给牌面）。 */
  faceDown?: boolean;
  /** 可出：上移一档 + 内白外墨双环。 */
  playable?: boolean;
  /** 变色牌当前指定的颜色（弃牌堆顶那张用，在风车上叠一个色徽）。 */
  chosenColor?: string;
}) {
  if (faceDown) {
    return (
      <View
        style={[
          s.card,
          { width, height, backgroundColor: unoWild, borderColor: unoBackEdge },
        ]}
        accessibilityLabel="未知牌"
      >
        {/* 倾斜的红椭圆 = UNO 牌背的招牌图案。红 vs 黑底 3.10 ✓；卡片轮廓由
            白边给出（vs 深色毡面 16.83）。
            **椭圆刻意画小**（只占 60%×63%）：座位上的牌背只有 20×30px，
            画满 + 再加一圈白环看着像一排眼睛。「UNO」字样只在牌够大时画
            （牌堆 54px 起），小尺寸下它只是三团糊字 */}
        <Svg
          style={StyleSheet.absoluteFill}
          viewBox="0 0 100 140"
          preserveAspectRatio="none"
        >
          <Ellipse cx="50" cy="70" rx="30" ry="44" fill={unoColors.R} transform="rotate(-22 50 70)" />
          {width >= 44 ? (
            <SvgText
              x="50" y="79" fontSize="26" fontWeight="bold" fill={unoOnWild}
              textAnchor="middle" transform="rotate(-22 50 70)"
            >
              UNO
            </SvgText>
          ) : null}
        </Svg>
      </View>
    );
  }

  const color = cardColor(card);
  const kind = cardKind(card);
  const wild = isWild(card);
  const face = wild ? unoWild : (unoColors[color] || unoWild);
  // 只有黑牌用白字（见文件头注释里的实测值）
  const ink = wild ? unoOnWild : unoInk;
  const badgeColor = wild ? (chosenColor || 'W') : color;
  const cornerSize = Math.max(11, width * 0.2);
  const showCorners = width >= CORNER_MIN_W && kind !== '';

  const body = (
    <View
      style={[
        s.card,
        { width, height, backgroundColor: face },
        // 牌边：深墨 vs 四色牌面 3.74~11.68 ✓。黄牌压在浅底上只有 1.45，
        // 这条边就是它成形的原因。黑牌不需要深边，用白边描出轮廓（14.68 ✓）。
        { borderColor: wild ? unoBackEdge : unoInk },
      ]}
      accessibilityLabel={cardLabel(card) + (playable ? '（可出）' : '')}
    >
      {/* 中央：白椭圆环 + 大字形 */}
      <View style={s.oval}>
        <Svg
          style={StyleSheet.absoluteFill}
          viewBox="0 0 100 140"
          preserveAspectRatio="none"
          pointerEvents="none"
        >
          <Ellipse
            cx="50" cy="70" rx="46" ry="64"
            fill="none" stroke={unoOnWild} strokeWidth={7} opacity={0.92}
          />
        </Svg>
        <CenterFace kind={kind} width={width} ink={ink} chosenColor={chosenColor} />
      </View>

      {/* 四角字形：重叠成扇形时唯一露在外面的部分 */}
      {showCorners ? (
        <>
          <View style={[s.corner, s.cornerTL]}>
            <UnoColorBadge color={badgeColor} size={cornerSize * 0.9} fill={ink} />
            <CornerGlyph kind={kind} size={cornerSize} ink={ink} />
          </View>
          <View style={[s.corner, s.cornerBR, s.flip]}>
            <UnoColorBadge color={badgeColor} size={cornerSize * 0.9} fill={ink} />
            <CornerGlyph kind={kind} size={cornerSize} ink={ink} />
          </View>
        </>
      ) : null}
    </View>
  );

  if (!playable) return body;

  // 双环：外层深墨、内层白。任何牌面上至少有一环与牌面对比达标。
  return (
    <View style={[s.ringOuter, { borderRadius: radius.sm + 6 }]}>
      <View style={[s.ringInner, { borderRadius: radius.sm + 4 }]}>{body}</View>
    </View>
  );
}

/** 牌面中央的大字形（椭圆环里的那一个）。 */
function CenterFace({
  kind, width, ink, chosenColor,
}: { kind: string; width: number; ink: string; chosenColor?: string }) {
  if (kind === '') {
    // 变色牌：四色风车圆盘（已选色时在盘心叠一个色徽）
    return (
      <View style={[s.pinwheel, { width: width * 0.42, height: width * 0.42 }]}>
        <Svg width="100%" height="100%" viewBox="0 0 100 100">
          <Polygon points="50,50 1,1 99,1" fill={unoColors.G} />
          <Polygon points="50,50 99,1 99,99" fill={unoColors.B} />
          <Polygon points="50,50 99,99 1,99" fill={unoColors.R} />
          <Polygon points="50,50 1,99 1,1" fill={unoColors.Y} />
        </Svg>
        {chosenColor ? (
          <View style={s.chosenWrap}>
            <UnoColorBadge color={chosenColor} size={width * 0.22} fill={unoOnWild} />
          </View>
        ) : null}
      </View>
    );
  }
  if (GLYPH_KINDS.has(kind)) {
    // 功能牌一律用矢量符号：无限缩放，缩到 5px 的角标也不糊（黑牌上是白字形，
    // 压黑牌 14.68 ✓；风车留给纯变色牌，两张一眼分得开）
    return <UnoSymbolGlyph kind={kind} size={width * 0.6} fill={ink} />;
  }
  return <Text style={[s.num, { color: ink, fontSize: width * 0.48 }]}>{kind}</Text>;
}

/**
 * 用矢量符号画的牌面。
 *
 * **所有功能牌都在这里**，包括 `+6` / `+10` / `+RD4` / `+RO` —— 它们都不是数字牌
 * （`"+6".isdigit()` 是 false），走文字兜底的话牌面会跟 +2/+4 长得不一样。
 */
const GLYPH_KINDS = new Set(['S', 'R', 'SE', 'DA', '+2', '+4', '+6', '+10',
                             '+RD4', '+RO', 'TP', 'TD4']);

/** 角上的小字形。数字用文字（≥11px），功能牌用同一份矢量符号（无限缩放）。 */
function CornerGlyph({ kind, size, ink }: { kind: string; size: number; ink: string }) {
  if (GLYPH_KINDS.has(kind)) {
    return <UnoSymbolGlyph kind={kind} size={size * 1.35} fill={ink} />;
  }
  return <Text style={[s.num, { color: ink, fontSize: size }]}>{kind}</Text>;
}

const s = StyleSheet.create({
  card: {
    borderRadius: radius.sm + 2,
    borderWidth: 2,
    overflow: 'hidden',
    justifyContent: 'center',
    alignItems: 'center',
  },
  oval: {
    width: '76%',
    height: '84%',
    justifyContent: 'center',
    alignItems: 'center',
  },
  corner: {
    position: 'absolute',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 2,
  },
  cornerTL: { top: 3, left: 4 },
  cornerBR: { bottom: 3, right: 4 },
  flip: { transform: [{ rotate: '180deg' }] },
  num: {
    fontWeight: '800',
    includeFontPadding: false,
  },
  pinwheel: {
    borderRadius: 999,
    overflow: 'hidden',
    justifyContent: 'center',
    alignItems: 'center',
  },
  chosenWrap: {
    position: 'absolute',
    justifyContent: 'center',
    alignItems: 'center',
  },
  ringOuter: {
    backgroundColor: unoInk,
    padding: 3,
    transform: [{ translateY: -12 }],
  },
  ringInner: {
    backgroundColor: unoOnWild,
    padding: 2,
  },
});

/**
 * 牌背一排（别人的手牌：只给张数）。
 *
 * 摆成**小扇形**而不是并排的方块——并排看起来像进度条，扇形一眼就知道是「一沓牌」。
 * 每张只露 42%，旋转 ±4°，中间的牌最高。
 */
export function UnoHandBacks({
  count, width = 20, height = 30, max = 7,
}: { count: number; width?: number; height?: number; max?: number }) {
  const shown = Math.min(count, max);
  const step = width * 0.42;
  const mid = (shown - 1) / 2;
  return (
    <View style={[s2.row, { width: width + step * Math.max(0, shown - 1), height }]}>
      {Array.from({ length: shown }).map((_, i) => (
        <View
          key={i}
          style={{
            position: 'absolute',
            left: i * step,
            transform: [
              { rotate: `${shown > 1 ? ((i - mid) / Math.max(1, mid)) * 4 : 0}deg` },
              { translateY: shown > 1 ? Math.abs(i - mid) / Math.max(1, mid) * 2 : 0 },
            ],
          }}
        >
          <UnoCardView card="" faceDown width={width} height={height} />
        </View>
      ))}
      {count > max ? (
        <Text style={[s2.more, { left: width + step * Math.max(0, shown - 1) + 2 }]}>
          {`+${count - max}`}
        </Text>
      ) : null}
    </View>
  );
}

const s2 = StyleSheet.create({
  row: { position: 'relative' },
  more: {
    // 牌背只出现在深色毡面的座位区里，所以用毡面专用的浅色（11.98 ✓），
    // 不能借用页面浅底上的 textMuted
    position: 'absolute',
    top: 8,
    fontSize: fontSize.xs,
    color: unoOnTableMuted,
    fontWeight: '700',
  },
});