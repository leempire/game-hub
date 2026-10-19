/**
 * 深空休眠的**弹道表现层**：枪口焰 / 弹丸 / 命中冲击环。
 *
 * ── 这一层为什么是纯客户端、且不碰平衡 ──────────────────────
 *
 * 引擎里**没有弹丸实体**：伤害是「射程内按 DPS 连续扣血」
 * （`stasis._combat` ③）。把弹丸做成真实体会让 DPS 开始依赖飞行时间 ——
 * 那是**平衡改动**，不该混在美术轮里做（`增强调研.md` §10.3）。
 *
 * 所以服务端只多给一份「谁在开火、打谁」（`stasis_tick.shots`），
 * 这一层据此**自己定开火节奏**、画三样东西。炮台没开火时一个元素都不画。
 *
 * ── 性能约定（这一屏的帧率是踩过坑的）──────────────────────
 *
 * ① **React 重渲染只在「开火集合」变化时发生**（`firing` 这个 prop）。
 *    静默体每 tick 都在动、`shots` 每 tick 都是新数组，若把它塞进 state，
 *    25 Hz 重建整个特效层 —— 同「`setGame({...prev})` 即使内容没变也是新对象」
 *    那条教训。
 * ② **弹丸位置一律走 `Animated.setValue`**，不进 React state（同主角/队友位置）。
 * ③ 没有炮台开火时**定时器根本不启动**（`firing.length === 0` 直接不挂 interval）。
 *
 * ── 时长取值（VFX 惯例，见 `增强调研.md` §10.3）──────────────
 *
 * 枪口焰 90ms（惯例 60-100ms：「长到能看见，短到来不及看清」）、
 * 弹丸飞行 150ms、冲击环 220ms。**开火间隔 420ms 是纯表现**，
 * 与 DPS 无关 —— 所以它不影响任何数值，只影响「看起来打得多凶」。
 */
import React, { memo, useEffect, useMemo, useRef } from 'react';
import { Animated, Easing, Platform, StyleSheet, View } from 'react-native';
import { BulletSprite, ImpactRing, MuzzleFlash } from './stasisArt';
import { TILE } from './stasisMap';

/** 每座塔的开火间隔（纯表现）。 */
const FIRE_MS = 420;
/** 弹丸飞行时间。 */
const FLIGHT_MS = 150;
/** 枪口焰存活时间。 */
const MUZZLE_MS = 90;
/** 冲击环存活时间。 */
const IMPACT_MS = 220;
/** 弹丸与冲击环的对象池大小。超出的这一帧就不画（宁缺毋滥，不动态扩容）。 */
const POOL_BULLET = 12;
const POOL_IMPACT = 6;

const USE_NATIVE = Platform.OS !== 'web';

interface BulletSlot {
  xy: Animated.ValueXY;
  /** 朝向，**存角度数**（`Animated.Value` 只吃数字），渲染时插值成 `deg` 字符串。 */
  rot: Animated.Value;
  rotDeg: Animated.AnimatedInterpolation<string>;
  op: Animated.Value;
  from: [number, number];
  t0: number;
  active: boolean;
}

interface ImpactSlot {
  xy: Animated.ValueXY;
  scale: Animated.Value;
  op: Animated.Value;
  t0: number;
  active: boolean;
}

function cellCenter(key: string): [number, number] {
  const [x, y] = key.split(',').map(Number);
  return [(x + 0.5) * TILE, (y + 0.5) * TILE];
}

/**
 * `firing`：正在开火的炮台格键（`"x,y"`，**顺序稳定**，由父层去重排序后传入，
 * 否则每次 tick 都是新数组、`memo` 白写）。
 * `targetRef`：静默体当前位置（像素），走 ref 读 —— 每 tick 都变，进 prop 就是
 * 每 tick 重渲染。
 */
const ShotLayer = memo(function ShotLayer({ firing, targetRef, reduceMotion }: {
  firing: string[];
  targetRef: React.MutableRefObject<[number, number]>;
  reduceMotion: boolean;
}) {
  const bullets = useRef<BulletSlot[]>([]);
  const impacts = useRef<ImpactSlot[]>([]);
  const lastFire = useRef<Map<string, number>>(new Map());
  const muzzle = useRef<Map<string, Animated.Value>>(new Map());

  if (bullets.current.length === 0) {
    bullets.current = Array.from({ length: POOL_BULLET }, () => {
      const rot = new Animated.Value(0);
      return {
        xy: new Animated.ValueXY({ x: 0, y: 0 }),
        rot,
        rotDeg: rot.interpolate({
          inputRange: [-180, 180], outputRange: ['-180deg', '180deg'],
        }),
        op: new Animated.Value(0),
        from: [0, 0] as [number, number],
        t0: 0,
        active: false,
      };
    });
    impacts.current = Array.from({ length: POOL_IMPACT }, () => ({
      xy: new Animated.ValueXY({ x: 0, y: 0 }),
      scale: new Animated.Value(0.4),
      op: new Animated.Value(0),
      t0: 0,
      active: false,
    }));
  }

  // 枪口焰的 Animated 值按格缓存（**懒建、不删**）：格子键是 `"x,y"`，
  // 一局里炮台只会被造/拆，集合很小，留着比反复建划算。
  // `firing` 由父层保证「集合不变则引用不变」，所以这里不需要再 memo 一层。
  firing.forEach((k) => {
    if (!muzzle.current.has(k)) muzzle.current.set(k, new Animated.Value(0));
  });

  useEffect(() => {
    if (firing.length === 0) return;
    // **走 `requestAnimationFrame` 而不是 `setInterval(40)`**（v8）：40ms 的定时器
    // 把弹丸钉在 25 Hz，而屏幕是 60 Hz —— 弹丸一帧跳一大截，看着像掉帧。
    // 开火节奏由 `FIRE_MS`（420ms）单独控，与循环频率无关，所以换掉它不改平衡。
    let raf = 0;
    const step = () => {
      raf = requestAnimationFrame(step);
      const now = Date.now();
      const [tx, ty] = targetRef.current;

      // ① 开火：到点的塔生成一发弹丸
      firing.forEach((key) => {
        const last = lastFire.current.get(key) ?? 0;
        if (now - last < FIRE_MS) return;
        lastFire.current.set(key, now);
        const slot = bullets.current.find((b) => !b.active);
        if (slot) {
          slot.from = cellCenter(key);
          slot.t0 = now;
          slot.active = true;
          slot.op.setValue(1);
          slot.xy.setValue({ x: slot.from[0], y: slot.from[1] });
        }
        // 枪口焰：一次 90ms 的闪现（不是循环脉冲，所以不受 WCAG 2.2.2 约束）
        const m = muzzle.current.get(key);
        if (m && !reduceMotion) {
          m.setValue(1);
          Animated.timing(m, {
            toValue: 0, duration: MUZZLE_MS, easing: Easing.out(Easing.quad),
            useNativeDriver: USE_NATIVE,
          }).start();
        }
      });

      // ② 步进弹丸：**每帧重新瞄准目标**（制导拖尾）—— 目标是活的，
      //    只朝「开火那一刻的位置」飞会在它走开时打空，看起来像脱靶。
      bullets.current.forEach((b) => {
        if (!b.active) return;
        const t = (now - b.t0) / FLIGHT_MS;
        if (t >= 1) {
          b.active = false;
          b.op.setValue(0);
          // 命中：在目标处放一圈扩散环（它同时是「这一发打中了」的确认）
          const imp = impacts.current.find((i) => !i.active);
          if (imp) {
            imp.active = true;
            imp.t0 = now;
            imp.xy.setValue({ x: tx, y: ty });
            imp.op.setValue(1);
            imp.scale.setValue(0.4);
          }
          return;
        }
        const x = b.from[0] + (tx - b.from[0]) * t;
        const y = b.from[1] + (ty - b.from[1]) * t;
        b.xy.setValue({ x, y });
        b.rot.setValue((Math.atan2(ty - b.from[1], tx - b.from[0]) * 180) / Math.PI);
      });

      // ③ 步进冲击环
      impacts.current.forEach((i) => {
        if (!i.active) return;
        const t = (now - i.t0) / IMPACT_MS;
        if (t >= 1) { i.active = false; i.op.setValue(0); return; }
        i.scale.setValue(0.4 + t * 1.1);
        i.op.setValue(1 - t);
      });
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [firing, targetRef, reduceMotion]);

  // 组件卸载时把还亮着的元素收掉，避免复用时残留（热重载下也干净）
  useEffect(() => () => {
    bullets.current.forEach((b) => { b.active = false; b.op.setValue(0); });
    impacts.current.forEach((i) => { i.active = false; i.op.setValue(0); });
  }, []);

  if (reduceMotion) return null;

  return (
    <>
      {/* 枪口焰：跟着「正在开火」的塔走 */}
      {firing.map((key) => {
        const [cx, cy] = cellCenter(key);
        const op = muzzle.current.get(key);
        if (!op) return null;
        return (
          <Animated.View
            key={`mz${key}`}
            testID="stasis-muzzle"
            pointerEvents="none"
            style={[styles.fx, { left: cx - 13, top: cy - 13, opacity: op }]}
          >
            <MuzzleFlash size={26} />
          </Animated.View>
        );
      })}
      {/* 冲击环（池化，常驻挂载、不活跃时 opacity 0） */}
      {impacts.current.map((i, idx) => (
        <Animated.View
          key={`im${idx}`}
          testID="stasis-impact"
          pointerEvents="none"
          style={[styles.fx, {
            left: -15, top: -15,
            opacity: i.op,
            transform: [
              { translateX: i.xy.x }, { translateY: i.xy.y }, { scale: i.scale },
            ],
          }]}
        >
          <ImpactRing size={30} />
        </Animated.View>
      ))}
      {/* 弹丸 */}
      {bullets.current.map((b, idx) => (
        <Animated.View
          key={`bl${idx}`}
          testID="stasis-bullet"
          pointerEvents="none"
          style={[styles.fx, {
            left: -7, top: -4,
            opacity: b.op,
            transform: [
              { translateX: b.xy.x }, { translateY: b.xy.y }, { rotate: b.rotDeg },
            ],
          }]}
        >
          <BulletSprite size={14} />
        </Animated.View>
      ))}
    </>
  );
});

export default ShotLayer;

const styles = StyleSheet.create({
  fx: { position: 'absolute', zIndex: 9 },
});
