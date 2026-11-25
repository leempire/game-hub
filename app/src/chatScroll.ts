/** Distinguish content reflow from a user leaving the bottom of a conversation. */
export class BottomAnchor {
  following = true;
  private pendingLayout = false;
  private contentHeight = 0;
  private viewportHeight = 0;
  private dragging = false;
  follow() { this.following = true; this.pendingLayout = true; }
  pause() { this.following = false; this.pendingLayout = false; }
  beginDrag() { this.dragging = true; this.pause(); }
  endDrag() { this.dragging = false; }
  resized(height: number) {
    if (height > this.contentHeight + 1 && this.following && !this.dragging) this.pendingLayout = true;
    this.contentHeight = height;
    return this.following && !this.dragging;
  }
  observe(offset: number, height: number, viewport: number) {
    const distance = height - offset - viewport;
    if ((height > this.contentHeight + 1 || Math.abs(viewport - this.viewportHeight) > 1)
        && this.following && !this.dragging) this.pendingLayout = true;
    this.contentHeight = height; this.viewportHeight = viewport;
    if (this.pendingLayout && this.following && !this.dragging && distance > 80)
      return { bottom: true, snap: true };
    const bottom = distance <= 80;
    this.following = bottom;
    if (bottom) this.pendingLayout = false;
    return { bottom, snap: false };
  }
}

interface ScrollList {
  scrollToEnd(options: { animated: boolean }): void;
  getNativeScrollRef?(): unknown;
}
/** Prefer the ScrollView's measured content extent, not FlatList's estimated last frame. */
export function scrollToLatest(list: ScrollList | null) {
  const scroll = list?.getNativeScrollRef?.() as { scrollToEnd?: (options: { animated: boolean }) => void } | null;
  if (typeof scroll?.scrollToEnd === 'function') scroll.scrollToEnd({ animated: false });
  else list?.scrollToEnd({ animated: false });
}
