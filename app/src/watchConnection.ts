/** Spectator-only transport: it can send heartbeats, never gameplay actions. */
import { WS_BASE } from './config';
import type { QuickMessage, WatchGrant, WatchState } from './social';

export default class WatchConnection {
  private socket: WebSocket | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private stopped = false;
  private attempts = 0;
  private receivedAt = Date.now();
  constructor(private token: string, private grant: WatchGrant,
    private onState: (value: WatchState) => void,
    private onConnection: (value: boolean) => void,
    private onError: (value: string) => void,
    private onPhrase: (value: QuickMessage) => void) {}
  connect() {
    if (this.stopped) return;
    const socket = new WebSocket(`${WS_BASE}${this.grant.ws_path}?token=${encodeURIComponent(this.token)}&grant=${encodeURIComponent(this.grant.grant)}`);
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket || this.stopped) return;
      this.attempts = 0; this.receivedAt = Date.now();
      this.heartbeat = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          if (Date.now() - this.receivedAt > 60000) socket.close();
          else socket.send(JSON.stringify({ type: 'ping' }));
        }
      }, 15000);
    };
    socket.onmessage = event => {
      if (this.socket !== socket || this.stopped) return;
      this.receivedAt = Date.now();
      let message;
      try { message = JSON.parse(String(event.data)); }
      catch { this.onError('观战状态无法解析，请重新连接'); socket.close(); return; }
      if ((message.type === 'watch_sync' || message.type === 'watch_state') && message.read_only === true) {
        this.onState(message as WatchState); this.onConnection(true);
      } else if (message.type === 'quick_chat') this.onPhrase(message as QuickMessage);
      else if (message.type === 'error') this.onError(String(message.message));
    };
    socket.onclose = event => {
      if (this.socket !== socket || this.stopped) return;
      this.onConnection(false);
      if (this.heartbeat) clearInterval(this.heartbeat);
      if ([4001, 4003, 4004].includes(event.code)) {
        this.onError(event.reason || '观战许可已失效，请重新进入'); return;
      }
      this.retry = setTimeout(() => this.connect(), Math.min(1000 * 2 ** Math.min(this.attempts++, 5), 30000));
    };
    socket.onerror = () => { if (this.socket === socket) this.onConnection(false); };
  }
  close() {
    this.stopped = true;
    if (this.retry) clearTimeout(this.retry);
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.socket?.close(); this.socket = null;
  }
}
