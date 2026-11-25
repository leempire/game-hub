/** Durable-chat client state: ordered cursors, retry identity and foreground read boundaries. */
import { getChat, markRead, sendMessage } from './contacts';
import type { ChatPage, ContactProfile, DirectMessage } from './contacts';

export function mergeMessages(current: DirectMessage[], incoming: DirectMessage[]) {
  const byId = new Map(current.map(item => [item.id, item]));
  incoming.forEach(item => byId.set(item.id, item));
  return [...byId.values()].sort((a, b) => a.id - b.id);
}
export const incomingCursor = (messages: DirectMessage[], actor: number) =>
  messages.reduce((last, message) => message.sender_id === actor ? last : Math.max(last, message.id), 0);
export const newClientId = () => `chat_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}_${Math.random().toString(36).slice(2)}`;

export interface ChatState {
  messages: DirectMessage[]; peer: ContactProfile | null; draft: string;
  ready: boolean; loading: boolean; loadingOlder: boolean; sending: boolean;
  hasOlder: boolean; peerReadThrough: number; error: string; sendError: string;
}
interface Transport {
  getChat: typeof getChat; sendMessage: typeof sendMessage; markRead: typeof markRead;
}
const production: Transport = { getChat, sendMessage, markRead };
const initial = (): ChatState => ({ messages: [], peer: null, draft: '', ready: false, loading: true,
  loadingOlder: false, sending: false, hasOlder: false, peerReadThrough: 0, error: '', sendError: '' });

export class ChatTimeline {
  state = initial();
  private listeners = new Set<(value: ChatState) => void>();
  private active = false;
  private epoch = 0;
  private atBottom = true;
  private sendBusy = false;
  private olderBusy = false;
  private readBusy = false;
  private readThrough = 0;
  private pending: { clientId: string; body: string } | null = null;
  constructor(private token: string, private actor: number, private peer: number,
    private foreground: () => boolean, private api: Transport = production) {}
  subscribe(listener: (value: ChatState) => void) {
    this.listeners.add(listener); listener(this.state);
    return () => { this.listeners.delete(listener); };
  }
  private publish(update: Partial<ChatState>) {
    this.state = { ...this.state, ...update };
    if (this.active && this.foreground()) this.listeners.forEach(listener => listener(this.state));
  }
  setActive(value: boolean) { this.active = value; this.invalidate(); }
  invalidate() { this.epoch++; this.publish({}); }
  private guard(external: () => boolean = () => true) {
    const epoch = this.epoch;
    return () => this.active && this.foreground() && epoch === this.epoch && external();
  }
  fail(error: unknown) {
    this.publish({ loading: false, ready: false, error: error instanceof Error ? error.message : '连接失败，请重试' });
  }
  changeDraft(value: string) {
    if (this.sendBusy) return;
    if (this.pending?.body !== value.trim()) this.pending = null;
    this.publish({ draft: value, sendError: '' });
  }
  private reconcile(messages: DirectMessage[]) {
    const pending = this.pending;
    if (pending && messages.some(item => item.sender_id === this.actor && item.client_id === pending.clientId)) {
      this.pending = null;
      this.publish({ draft: this.state.draft.trim() === pending.body ? '' : this.state.draft, sendError: '' });
    }
  }
  private receive(page: ChatPage, first: boolean) {
    const messages = mergeMessages(this.state.messages, page.items);
    this.publish({ messages, peer: page.peer, loading: false, ready: true, error: '',
      peerReadThrough: Math.max(this.state.peerReadThrough, page.peer_read_through),
      ...(first ? { hasOlder: page.has_more } : {}) });
    this.reconcile(messages);
  }
  async poll(external: () => boolean) {
    const valid = this.guard(external);
    if (!valid()) return;
    const first = this.state.messages.length === 0;
    let cursor = this.state.messages.at(-1)?.id ?? 0;
    let page = await this.api.getChat(this.token, this.peer, first ? {} : { after: cursor, limit: 100 });
    if (!valid()) return;
    this.receive(page, first);
    while (!first && page.has_more && valid()) {
      const next = page.items.at(-1)?.id ?? cursor;
      if (next <= cursor) throw new Error('消息同步暂时中断，请重试');
      cursor = next;
      page = await this.api.getChat(this.token, this.peer, { after: cursor, limit: 100 });
      if (!valid()) return;
      this.receive(page, false);
    }
    if (valid()) await this.readVisible(valid);
  }
  async loadOlder() {
    if (this.olderBusy || !this.state.hasOlder || !this.state.messages.length) return;
    const valid = this.guard();
    if (!valid()) return;
    this.olderBusy = true; this.atBottom = false;
    this.publish({ loadingOlder: true });
    try {
      const page = await this.api.getChat(this.token, this.peer, { before: this.state.messages[0].id });
      if (!valid()) return;
      this.publish({ messages: mergeMessages(this.state.messages, page.items), hasOlder: page.has_more, error: '' });
    } catch (error) { if (valid()) this.fail(error); }
    finally { this.olderBusy = false; this.publish({ loadingOlder: false }); }
  }
  setAtBottom(value: boolean) {
    this.atBottom = value;
    if (value) { const valid = this.guard(); void this.readVisible(valid).catch(error => { if (valid()) this.fail(error); }); }
  }
  private async readVisible(valid: () => boolean) {
    if (!valid() || !this.state.ready || !this.atBottom || this.readBusy) return;
    const through = incomingCursor(this.state.messages, this.actor);
    if (through <= this.readThrough) return;
    this.readBusy = true;
    try {
      const value = await this.api.markRead(this.token, this.peer, through);
      if (valid()) this.readThrough = Math.max(this.readThrough, value.through_id);
    } finally { this.readBusy = false; }
  }
  async send() {
    const valid = this.guard();
    const body = this.state.draft.trim();
    if (!valid() || this.sendBusy || !this.state.ready || !body) return;
    this.sendBusy = true;
    const pending = this.pending?.body === body ? this.pending : { clientId: newClientId(), body };
    this.pending = pending;
    this.publish({ sending: true, sendError: '' });
    try {
      const sent = await this.api.sendMessage(this.token, this.peer, body, pending.clientId);
      if (!valid()) return;
      const messages = mergeMessages(this.state.messages, [sent]);
      this.publish({ messages }); this.reconcile(messages);
    } catch (error) {
      if (valid() && this.pending?.clientId === pending.clientId)
        this.publish({ sendError: (error instanceof Error ? error.message : '发送失败') + '；内容已保留，可重试' });
    } finally { this.sendBusy = false; this.publish({ sending: false }); }
  }
}
