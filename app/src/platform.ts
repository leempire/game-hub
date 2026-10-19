import { Platform } from 'react-native';

// Whether running on native Android/iOS (not web)
export const isNative = Platform.OS !== 'web';

// Whether running on mobile web browser (vs desktop)
export const isMobileWeb = (() => {
  if (Platform.OS !== 'web') return false;
  if (typeof navigator === 'undefined') return false;
  const ua = navigator.userAgent || '';
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(ua);
})();

// Whether running on PC web browser
export const isDesktopWeb = Platform.OS === 'web' && !isMobileWeb;
