// 应用内 Toast 提示：替代 alert/Alert.alert，三端统一的人性化交互。
// 模块级事件总线 + ToastHost 挂载一次即可，notify() 从任意位置调用。
import React, { useEffect, useRef, useState } from 'react';
import { Animated, Platform, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

interface ToastItem {
  id: number;
  title: string;
  message?: string;
}

type Listener = (item: ToastItem) => void;

let listener: Listener | null = null;
let nextId = 1;

// 推送一条 toast；ToastHost 未挂载时静默丢弃（避免崩溃）
export function pushToast(title: string, message?: string): void {
  if (listener) listener({ id: nextId++, title, message });
}

const DISPLAY_MS = 2600;

export function ToastHost(): React.JSX.Element | null {
  const insets = useSafeAreaInsets();
  const [item, setItem] = useState<ToastItem | null>(null);
  const fade = useRef(new Animated.Value(0)).current;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    listener = (next) => {
      setItem(next);
      fade.setValue(0);
      Animated.timing(fade, {
        toValue: 1,
        duration: 180,
        useNativeDriver: Platform.OS !== 'web' ? true : false,
      }).start();
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        Animated.timing(fade, {
          toValue: 0,
          duration: 220,
          useNativeDriver: Platform.OS !== 'web' ? true : false,
        }).start(() => setItem(null));
      }, DISPLAY_MS);
    };
    return () => {
      listener = null;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [fade]);

  if (!item) return null;
  return (
    <Animated.View
      pointerEvents="none"
      style={[
        styles.wrap,
        {
          top: insets.top + 12,
          opacity: fade,
          transform: [
            {
              translateY: fade.interpolate({
                inputRange: [0, 1],
                outputRange: [-16, 0],
              }),
            },
          ],
        },
      ]}
    >
      <View style={styles.card}>
        <Text style={styles.title}>{item.title}</Text>
        {item.message ? <Text style={styles.message}>{item.message}</Text> : null}
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: 'absolute',
    top: 48,
    left: 0,
    right: 0,
    alignItems: 'center',
    zIndex: 999,
    elevation: 999,
  },
  card: {
    maxWidth: 320,
    backgroundColor: 'rgba(17,24,39,0.92)',
    borderRadius: 12,
    paddingHorizontal: 18,
    paddingVertical: 12,
    alignItems: 'center',
  },
  title: { color: '#fff', fontSize: 14, fontWeight: '600', textAlign: 'center' },
  message: { color: 'rgba(255,255,255,0.75)', fontSize: 12, marginTop: 4, textAlign: 'center' },
});
