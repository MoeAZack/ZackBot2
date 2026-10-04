"""ZackBot two-way Telegram control: read status / positions / profit and pause, resume, flatten or close from Telegram.

Long-polls the Bot API (getUpdates, 25 s) in one background thread. Only the configured chat (settings TELEGRAM_CHAT) is
obeyed; everyone else is ignored silently (counted, and their chat id written once to the local log so a user can find
their own id). Dangerous commands (/flatten, /close) need a 6-character confirmation code sent back within 60 s, plus the
PIN when one is set. There is deliberately no remote "stop bot" command.

Settings (in engine.S / settings.json, not in GLOBAL_DEFAULTS - read with .get and these defaults):
  TELEGRAM_CONTROL  bool, default False    two-way control on/off (independent of the TELEGRAM_ON alerts switch)
  TELEGRAM_PIN      str,  default ''       '' = no PIN; otherwise the stored HASH 'pbkdf2_sha256$iters$salt$hex' of a
                                           4-8 digit PIN (never the PIN itself). Build it with clean_setting().
The bot token is engine.cfg['TELEGRAM_TOKEN'] (encrypted config); the chat is engine.S['TELEGRAM_CHAT'].

INTEGRATION (app.py - lead engineer):
  1. import:            from telegram_ctl import TelegramControl, clean_setting as tg_clean_setting
  2. App.__init__, after self.start_engine():
         self.tg = TelegramControl(lambda: self.engine, lambda: self)
         self.tg.start()
     (start_engine() needs nothing: the thread calls get_engine() on every poll, so an engine restart, a new token or a
      changed chat id / TELEGRAM_CONTROL switch is picked up on the next poll - at most ~25 s later. Call
      APP.tg.restart() after a settings save if you want it to apply immediately.)
  3. handle('/api/settings') - add a branch before the final `else: raise ValueError('unknown setting ...')`:
         elif k in ('TELEGRAM_CONTROL', 'TELEGRAM_PIN'):
             v = tg_clean_setting(k, v, e.S.get(k))
             if v is not None: e.S[k] = v
     and after the `with e.lock:` block (where it logs 'settings changed'):
         if any(k.startswith('TELEGRAM') for k in b): APP.tg.restart()
     Validation done by clean_setting: TELEGRAM_CONTROL -> bool; TELEGRAM_PIN: '' or None clears it, 4-8 digits are
     hashed, an already-stored hash or a masked value ('••••') keeps the current one, anything else raises ValueError.
  4. snapshot(): never ship the PIN hash to the panel:
         settings = {k: v for k, v in e.S.items() if k not in ('TELEGRAM_TOKEN', 'TELEGRAM_PIN')}
         settings['TELEGRAM_PIN_SET'] = bool(e.S.get('TELEGRAM_PIN'))
     and add to the returned dict:   telegram=self.tg.status() if getattr(self, 'tg', None) else None
  5. Optional: on quit (os._exit path) call APP.tg.stop() - not required, the thread is a daemon.
"""
import hashlib, hmac, json, logging, os, re, secrets, threading, time, collections
from datetime import datetime, timezone, timedelta

log = logging.getLogger('zackbot')

API = 'https://api.telegram.org/bot{tok}/{method}'
POLL_TIMEOUT = 25            # seconds Telegram holds a getUpdates request open
STALE_AT_START = 120         # updates older than this are dropped on the first poll after a start
CONFIRM_TTL = 60             # seconds a /flatten or /close confirmation code stays valid
RATE_N, RATE_WIN = 20, 60    # max commands per chat per window (seconds)
MAX_REPLY = 3500
BACKOFF_MAX = 60
PIN_MAX_FAILS, PIN_LOCK_S = 5, 900
PIN_ITERS = 100_000
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'     # no 0/O, 1/I lookalikes
OFFSET_FILE = 'telegram_offset.json'

HELP = ("ZackBot commands\n"
        "/status - mode, health, capital, today, open P&L, entries\n"
        "/positions - open trades\n"
        "/profit [days] - closed trades (default 7 days)\n"
        "/daily - today vs day start\n"
        "/risk - daily loss, drawdown, open risk, leverage\n"
        "/signals - active entry signals\n"
        "/pause - stop new entries (open trades keep running)\n"
        "/resume - allow new entries again{pin}\n"
        "/flatten - close ALL positions (asks for a code)\n"
        "/close COIN [LONG|SHORT] - close one coin (asks for a code)\n"
        "/start - buttons")
SHORT_HELP = 'Unknown command. Try /status, /positions, /profit, /pause, /resume, /flatten, /close COIN, or /help.'


# ------------------------------------------------------------------ PIN / settings helpers (used by app.py too)
def hash_pin(pin, salt=None, iters=PIN_ITERS):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac('sha256', str(pin).encode(), salt.encode(), iters).hex()
    return f'pbkdf2_sha256${iters}${salt}${h}'


def check_pin(stored, pin):
    try:
        algo, iters, salt, h = str(stored).split('$')
        if algo != 'pbkdf2_sha256' or not re.fullmatch(r'\d{4,8}', str(pin or '')): return False
        return hmac.compare_digest(hash_pin(pin, salt, int(iters)).split('$')[3], h)
    except Exception:
        return False


def _is_hash(v):
    return bool(re.fullmatch(r'pbkdf2_sha256\$\d{3,7}\$[0-9a-f]{8,64}\$[0-9a-f]{64}', str(v or '')))


def clean_setting(key, value, current=None):
    """Validate a settings change coming from the panel. Returns the value to store (None = leave unchanged)."""
    if key == 'TELEGRAM_CONTROL':
        return bool(value)
    if key == 'TELEGRAM_PIN':
        v = '' if value is None else str(value).strip()
        if v == '': return ''
        if set(v) == {'•'} or _is_hash(v): return current        # panel echoed the masked / stored value
        if not re.fullmatch(r'\d{4,8}', v): raise ValueError('the Telegram PIN must be 4-8 digits (or empty for none)')
        return hash_pin(v)
    raise ValueError(f'not a Telegram setting: {str(key)[:30]}')


def _f(x, d=2):
    return '-' if x is None else f'{x:,.{d}f}'


def _p(x):
    return '-' if x is None else f'{x:.6g}'


def _sgn(x, d=2):
    return '-' if x is None else f'{x:+,.{d}f}'


def _parse_ts(s):
    try:
        t = datetime.fromisoformat(str(s))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ------------------------------------------------------------------ the controller
class TelegramControl:
    def __init__(self, get_engine, get_app=None, http=None, clock=time.time):
        self.get_engine, self.get_app = get_engine, get_app or (lambda: None)
        if http is None:
            import requests
            http = requests
        self.http, self.clock = http, clock
        self._stop = threading.Event()
        self._thread = None
        self._fresh = True                     # first poll after start: drop stale updates
        self.last_poll = self.last_error = None
        self.ignored = 0
        self.last_ignored_chat = None
        self._logged_ignored = set()
        self.commands_today, self._cmd_day = 0, None
        self._rate = collections.defaultdict(collections.deque)
        self._rate_warned = {}
        self._pending = {}                     # (chat, kind) -> dict(code, expires, keys, label)
        self._pin_fails, self._pin_lock_until = 0, 0.0
        self.backoff = 0.0

    # ---------------- lifecycle
    def start(self):
        if self._thread and self._thread.is_alive() and not self._stop.is_set(): return
        self._stop = threading.Event(); self._fresh = True       # a fresh event: an old thread keeps its own (set) one and exits
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name='telegram-control', daemon=True)
        self._thread.start()

    def stop(self, wait=1.0):
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread(): t.join(wait)

    def restart(self):
        """Apply a settings change now (the old thread may still finish its current long-poll; it then exits)."""
        self.stop(0.1); self._thread = None; self.start()

    def status(self):
        e = self._engine()
        return dict(running=bool(self._thread and self._thread.is_alive() and not self._stop.is_set()),
                    enabled=self.enabled(e), last_poll=self.last_poll, last_error=self.last_error, ignored=self.ignored,
                    last_ignored_chat=self.last_ignored_chat, commands_today=self._today_count(),
                    pin_set=bool(e and e.S.get('TELEGRAM_PIN')))

    def _engine(self):
        try: return self.get_engine()
        except Exception: return None

    @staticmethod
    def _token(e):
        return (e.cfg.get('TELEGRAM_TOKEN') or e.S.get('TELEGRAM_TOKEN') or '') if e else ''

    def enabled(self, e=None):
        e = e or self._engine()
        return bool(e and e.S.get('TELEGRAM_CONTROL') and self._token(e) and str(e.S.get('TELEGRAM_CHAT') or '').strip())

    def _run(self, stop):
        while not stop.is_set():
            try:
                if not self.enabled():
                    stop.wait(5); continue
                self.poll_once(stop=stop)
                self.backoff = 0.0
            except Exception as ex:                    # never crash, never log the token (exception text can hold the URL)
                self.last_error = self.last_error if isinstance(ex, _ApiError) else type(ex).__name__
                self.backoff = min(BACKOFF_MAX, max(2.0, self.backoff * 2))
                log.warning(f'telegram control: poll failed ({self.last_error}) - retry in {self.backoff:.0f}s')
                stop.wait(self.backoff)

    # ---------------- Bot API
    def _call(self, method, payload, timeout=15):
        e = self._engine(); tok = self._token(e)
        if not tok: raise _ApiError('no token')
        r = self.http.post(API.format(tok=tok, method=method), json=payload, timeout=timeout)
        try: d = r.json()
        except Exception: d = {}
        if not d.get('ok'):
            code = d.get('error_code') or getattr(r, 'status_code', '?')
            desc = str(d.get('description') or '')[:120].replace(tok, '***')
            hint = {401: 'bot token rejected', 409: 'another program or a webhook is reading this bot',
                    403: 'Telegram refused (is TELEGRAM_CHAT your own chat id?)'}.get(code, '')
            self.last_error = f'{method}: {code} {hint or desc}'.strip()
            raise _ApiError(self.last_error)
        return d.get('result')

    def send(self, chat_id, text, keyboard=None):
        text = str(text)
        if len(text) > MAX_REPLY: text = text[:MAX_REPLY - 20] + '\n...(truncated)'
        p = dict(chat_id=chat_id, text=text, disable_web_page_preview=True)
        if keyboard: p['reply_markup'] = {'inline_keyboard': keyboard}
        try:
            self._call('sendMessage', p)
            return True
        except Exception as ex:
            log.warning(f'telegram control: reply failed ({self.last_error if isinstance(ex, _ApiError) else type(ex).__name__})')
            return False

    # ---------------- offset persistence (per bot id, so a new token never inherits an old offset)
    def _offset_path(self, e):
        return os.path.join(e.dir, OFFSET_FILE)

    def _bot_id(self, e):
        return self._token(e).split(':')[0]

    def load_offset(self, e):
        try:
            with open(self._offset_path(e)) as f: return int(json.load(f).get(self._bot_id(e)) or 0)
        except Exception:
            return 0

    def save_offset(self, e, off):
        p = self._offset_path(e)
        try:
            try:
                with open(p) as f: d = json.load(f)
            except Exception: d = {}
            d[self._bot_id(e)] = int(off)
            tmp = p + '.tmp'
            with open(tmp, 'w') as f: json.dump(d, f)
            os.replace(tmp, p)
        except Exception as ex:
            log.warning(f'telegram control: offset not saved ({type(ex).__name__})')

    # ---------------- one long-poll round (public so tests can drive it without a thread)
    def poll_once(self, timeout=POLL_TIMEOUT, stop=None):
        e = self._engine()
        if not self.enabled(e): return 0
        off = self.load_offset(e)
        p = dict(timeout=timeout, allowed_updates=['message', 'channel_post', 'callback_query'])
        if off: p['offset'] = off
        ups = self._call('getUpdates', p, timeout=timeout + 10) or []
        if stop is not None and stop.is_set(): return 0      # stopped/restarted meanwhile: the new poller fetches these again
        self.last_poll = datetime.now(timezone.utc).isoformat(timespec='seconds')
        self.last_error = None
        fresh, self._fresh = self._fresh, False
        n = 0
        for u in ups:
            uid = int(u.get('update_id', 0))
            if uid >= off:
                off = uid + 1
                self.save_offset(e, off)             # saved BEFORE acting: a crash never replays a command
            if not self.enabled(): continue          # switched off during the long poll: drop
            msg = u.get('message') or u.get('channel_post') or (u.get('callback_query') or {}).get('message')
            if fresh and msg and msg.get('date') and self.clock() - float(msg['date']) > STALE_AT_START and not u.get('callback_query'):
                continue                             # sent while the bot was off: never act on old commands
            try:
                if self.handle_update(u): n += 1
            except Exception as ex:
                log.warning(f'telegram control: command failed ({type(ex).__name__})')
        return n

    # ---------------- auth
    def _authorized(self, e, chat):
        want = str(e.S.get('TELEGRAM_CHAT') or '').strip()
        if not want or not chat: return False
        if want.startswith('@'):
            un = chat.get('username')
            return bool(un) and un.lower() == want[1:].lower()
        return str(chat.get('id')) == want

    def handle_update(self, u):
        e = self._engine()
        cq = u.get('callback_query')
        msg = (cq or {}).get('message') if cq else (u.get('message') or u.get('channel_post'))
        if not msg or e is None: return False
        chat = msg.get('chat') or {}
        if not self._authorized(e, chat):
            self.ignored += 1
            cid = chat.get('id')
            self.last_ignored_chat = cid
            if cid not in self._logged_ignored and len(self._logged_ignored) < 50:
                self._logged_ignored.add(cid)
                log.info(f'telegram control: ignored a message from chat id {cid} (not the configured chat)')
            return False
        if cq:
            try: self._call('answerCallbackQuery', dict(callback_query_id=cq.get('id')))
            except Exception: pass
            text = str(cq.get('data') or '')
        else:
            text = str(msg.get('text') or '')
        cid = chat.get('id')
        if not self._rate_ok(cid):
            return False
        self._count()
        reply, kb = self.command(text, cid, msg_id=None if cq else msg.get('message_id'))
        if reply: self.send(cid, reply, kb)
        return True

    def _rate_ok(self, cid):
        now = self.clock(); q = self._rate[cid]
        while q and now - q[0] > RATE_WIN: q.popleft()
        if len(q) >= RATE_N:
            if now - self._rate_warned.get(cid, 0) > RATE_WIN:
                self._rate_warned[cid] = now
                self.send(cid, f'Too many commands - max {RATE_N} per minute. Wait a moment.')
            return False
        q.append(now)
        return True

    def _count(self):
        d = datetime.now(timezone.utc).date().isoformat()
        if d != self._cmd_day: self._cmd_day, self.commands_today = d, 0
        self.commands_today += 1

    def _today_count(self):
        return self.commands_today if self._cmd_day == datetime.now(timezone.utc).date().isoformat() else 0

    # ---------------- command router
    def command(self, text, chat_id, msg_id=None):
        """Returns (reply_text, inline_keyboard_or_None)."""
        e = self._engine()
        parts = text.strip().split()
        if not parts or not parts[0].startswith('/'): return SHORT_HELP, None
        cmd = parts[0].split('@')[0].lower(); args = parts[1:]
        fn = {'/help': self.c_help, '/start': self.c_start, '/status': self.c_status, '/positions': self.c_positions,
              '/profit': self.c_profit, '/daily': self.c_daily, '/risk': self.c_risk, '/signals': self.c_signals,
              '/pause': self.c_pause, '/resume': self.c_resume, '/flatten': self.c_flatten, '/close': self.c_close}.get(cmd)
        if fn is None: return SHORT_HELP, None
        if cmd in ('/flatten', '/close', '/resume') and args and msg_id and e.S.get('TELEGRAM_PIN'):
            self._delete(chat_id, msg_id)                 # best effort: don't leave the PIN in the chat
        r = fn(e, args, chat_id)
        return r if isinstance(r, tuple) else (r, None)

    def _delete(self, chat_id, msg_id):
        try: self._call('deleteMessage', dict(chat_id=chat_id, message_id=msg_id))
        except Exception: pass

    def c_help(self, e, args, cid):
        return HELP.format(pin=' (needs your PIN: /resume 1234)' if e.S.get('TELEGRAM_PIN') else '')

    def c_start(self, e, args, cid):
        kb = [[dict(text='Status', callback_data='/status'), dict(text='Positions', callback_data='/positions')],
              [dict(text='Profit 7d', callback_data='/profit'), dict(text='Risk', callback_data='/risk')],
              [dict(text='Pause entries', callback_data='/pause'), dict(text='Resume entries', callback_data='/resume')]]
        return f'ZackBot control ({self._mode(e)}). Pick one, or /help for all commands.', kb

    # ---------------- read models
    @staticmethod
    def _mode(e):
        return 'LIVE' if e.live else 'TESTNET'

    def _lots(self, e):
        marks = dict(e.marks or {})
        with e.lock: items = [(k, dict(l)) for k, l in e.state['lots'].items()]
        out = []
        for k, l in items:
            m = marks.get(l['symbol']); sd = 1 if l['side'] == 'LONG' else -1
            pnl = sd * (m - l['avg']) * l['qty'] if m else None
            out.append(dict(key=k, symbol=l['symbol'], side=l['side'], sleeve=l.get('sleeve'), qty=l.get('qty'), avg=l.get('avg'),
                            stop=l.get('stop'), mark=m, pnl=pnl, manual=l.get('manual'),
                            r=pnl / l['risk_usd'] if pnl is not None and l.get('risk_usd') else None,
                            protected=bool(l.get('stop_id')) and not l.get('stop_dirty'),
                            risk_to_stop=sd * ((m or l['avg']) - l['stop']) * l['qty'], notional=(m or l['avg']) * l['qty']))
        return out

    def _health(self, e, lots):
        app = None
        try: app = self.get_app()
        except Exception: pass
        if app is not None and hasattr(app, 'health'):
            try: return app.health(e, lots)['engine']
            except Exception: pass
        h = e.health
        if e.error: return 'error'
        if h.get('manage_fail_streak', 0) >= 3 or any(not l['protected'] for l in lots) or e.untracked or e.state.get('orphans'):
            return 'degraded'
        return 'ok'

    @staticmethod
    def _entries(e):
        return 'HALTED (daily loss limit)' if e.state.get('halted') else 'PAUSED' if e.S.get('ENTRIES_PAUSED') else 'open'

    @staticmethod
    def _today_pct(e):
        g, d = e.guard_eq, e.state.get('day_start_equity')
        return (g / d - 1) * 100 if g and d else None

    def _next_candle(self, e):
        nc = getattr(e, 'next_cycle', None) or {}
        ts = [(tf, _parse_ts(v)) for tf, v in nc.items()]
        ts = [x for x in ts if x[1]]
        if not ts: return '-'
        tf, t = min(ts, key=lambda x: x[1])
        mins = max(0, int((t - datetime.now(timezone.utc)).total_seconds() // 60))
        return f'{tf} close {t.strftime("%H:%M")} UTC (in {mins // 60}h {mins % 60:02d}m)'

    def c_status(self, e, args, cid):
        lots = self._lots(e)
        with e.lock:
            cap, entries, today = e.guard_eq, self._entries(e), self._today_pct(e)
            err = e.error
        open_pnl = sum(l['pnl'] or 0 for l in lots)
        lines = [f'ZackBot {self._mode(e)}',
                 f'Engine: {self._health(e, lots)}' + (f' - {str(err)[:120]}' if err else ''),
                 f'Bot capital: {_f(cap)} USDT',
                 f'Today: {_sgn(today)}%',
                 f'Open positions: {len(lots)} (open P&L {_sgn(open_pnl)} USDT)',
                 f'Entries: {entries}',
                 f'Next candle: {self._next_candle(e)}']
        if e.untracked: lines.append(f'WARNING untracked on Binance: {", ".join(e.untracked)}')
        unprot = [l['symbol'] for l in lots if not l['protected']]
        if unprot: lines.append(f'WARNING no confirmed stop: {", ".join(unprot)}')
        return '\n'.join(lines)

    def c_positions(self, e, args, cid):
        lots = self._lots(e)
        if not lots: return 'No open positions.'
        out = [f'Open positions ({len(lots)}):']
        for l in sorted(lots, key=lambda x: (x['symbol'], x['side'])):
            out.append(f"{l['symbol'].replace('USDT', '')} {l['side']} [{l['sleeve']}] qty {_p(l['qty'])}\n"
                       f"  entry {_p(l['avg'])}  mark {_p(l['mark'])}  P&L {_sgn(l['pnl'])} ({_sgn(l['r'])}R)  stop {_p(l['stop'])}"
                       + ('' if l['protected'] else '  (stop NOT confirmed)'))
        out.append(f"Total open P&L: {_sgn(sum(l['pnl'] or 0 for l in lots))} USDT")
        return '\n'.join(out)

    def c_profit(self, e, args, cid):
        try: days = int(args[0]) if args else 7
        except ValueError: return 'Usage: /profit [days]  e.g. /profit 30'
        days = max(1, min(days, 3650))
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with e.lock: H = list(e.history)
        H = [h for h in H if (_parse_ts(h.get('closed')) or since - timedelta(1)) >= since]
        if not H: return f'No closed trades in the last {days} day(s).'
        pn = [h.get('pnl') or 0 for h in H]
        w = sum(1 for x in pn if x > 0)
        best = max(H, key=lambda h: h.get('pnl') or 0); worst = min(H, key=lambda h: h.get('pnl') or 0)
        nm = lambda h: f"{h.get('symbol', '?').replace('USDT', '')} {h.get('side', '')} {_sgn(h.get('pnl'))}"
        return '\n'.join([f'Last {days} day(s): {len(H)} closed trade(s)',
                          f'Net P&L: {_sgn(sum(pn))} USDT (after est. fees)',
                          f'Win rate: {w / len(H) * 100:.0f}% ({w} won, {len(H) - w} lost)',
                          f'Best: {nm(best)}', f'Worst: {nm(worst)}'])

    def c_daily(self, e, args, cid):
        import engine as E
        with e.lock:
            d0, g, day = e.state.get('day_start_equity'), e.guard_eq, e.state.get('day')
            H = list(e.history)
        try: start = _parse_ts(E.next_reset_utc()) - timedelta(days=1)
        except Exception: start = datetime.now(timezone.utc) - timedelta(days=1)
        T = [h for h in H if _parse_ts(h.get('closed')) and _parse_ts(h['closed']) >= start]
        net = sum(h.get('pnl') or 0 for h in T)
        chg = (g - d0) if g is not None and d0 else None
        return '\n'.join([f'Today ({day or E.trading_day()}, Cairo day)',
                          f'Day start: {_f(d0)} USDT', f'Now: {_f(g)} USDT',
                          f'Change: {_sgn(chg)} USDT ({_sgn(self._today_pct(e))}%)',
                          f'Closed today: {len(T)} trade(s), net {_sgn(net)} USDT',
                          f'Entries: {self._entries(e)}'])

    def c_risk(self, e, args, cid):
        lots = self._lots(e)
        with e.lock:
            S, st, g = e.S, e.state, e.guard_eq
            d0, peak = st.get('day_start_equity'), st.get('peak_equity')
            halt, ddf, mlev = S.get('DAILY_LOSS_HALT', 0), S.get('PEAK_DD_FLATTEN', 0), S.get('MAX_LEVERAGE')
        day = (g / d0 - 1) * 100 if g and d0 else None
        dd = (g / peak - 1) * 100 if g and peak else None
        risk = sum(max(0.0, l['risk_to_stop'] or 0) for l in lots)
        gross = sum(l['notional'] or 0 for l in lots)
        return '\n'.join([f'Daily loss: {_sgn(day)}% (halt at -{halt * 100:.1f}%)' + (' - HALTED' if st.get('halted') else ''),
                          f'Drawdown from peak: {_sgn(dd)}% ' + (f'(flatten at -{ddf * 100:.1f}%)' if ddf else '(flatten limit off)'),
                          f'Open risk to stops: {_f(risk)} USDT' + (f' ({risk / g * 100:.1f}% of capital)' if g else ''),
                          f'Exposure: {_f(gross)} USDT = {gross / g:.2f}x of bot capital' if g else f'Exposure: {_f(gross)} USDT',
                          f'Max leverage setting: {mlev}x'])

    def c_signals(self, e, args, cid):
        with e.lock: sig = dict(e.signals or {}); t = e.signals_time
        act = []
        for k, s in sorted(sig.items()):
            if s.get('le') or s.get('se'):
                sl, sym = (k.split('|', 1) + [''])[:2]
                act.append(f"{sym.replace('USDT', '')} {'LONG' if s.get('le') else 'SHORT'} [{sl}] close {_p(s.get('close'))} (candle {str(s.get('time', ''))[:16]})")
        if not act: return 'No active entry signals right now.' + (f' (checked {str(t)[:16]} UTC)' if t else '')
        return f'Active entry signals ({len(act)}):\n' + '\n'.join(act[:40]) + (f'\nchecked {str(t)[:16]} UTC' if t else '')

    # ---------------- control
    def c_pause(self, e, args, cid):
        with e.lock:
            was = e.S.get('ENTRIES_PAUSED')
            e.S['ENTRIES_PAUSED'] = True; e.save_settings()
        log.info('telegram control: entries paused')
        return 'Entries were already paused.' if was else 'Entries PAUSED. Open trades keep their stops and management. /resume to undo.'

    def c_resume(self, e, args, cid):
        ok, why = self._pin_ok(e, args[-1] if args else None)
        if not ok: return why + ' Usage: /resume <PIN>'
        with e.lock:
            if e.state.get('halted'):
                return 'Cannot resume: the daily loss halt is active until midnight Cairo time.'
            was = e.S.get('ENTRIES_PAUSED')
            e.S['ENTRIES_PAUSED'] = False; e.save_settings()
        log.info('telegram control: entries resumed')
        return 'Entries RESUMED - new signals will be traded.' if was else 'Entries were already open.'

    def _pin_ok(self, e, pin):
        stored = e.S.get('TELEGRAM_PIN')
        if not stored: return True, ''
        now = self.clock()
        if now < self._pin_lock_until:
            return False, f'Too many wrong PINs - locked for {int(self._pin_lock_until - now) // 60 + 1} more minute(s).'
        if pin and check_pin(stored, pin):
            self._pin_fails = 0; return True, ''
        if pin:
            self._pin_fails += 1
            if self._pin_fails >= PIN_MAX_FAILS:
                self._pin_fails, self._pin_lock_until = 0, now + PIN_LOCK_S
                log.warning('telegram control: too many wrong PINs - dangerous commands locked for 15 min')
                return False, 'Wrong PIN. Too many attempts - locked for 15 minutes.'
            return False, 'Wrong PIN - nothing done.'
        return False, 'PIN required.'

    def _new_code(self, cid, kind, **data):
        code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        self._pending[(cid, kind)] = dict(code=code, expires=self.clock() + CONFIRM_TTL, **data)
        return code

    def _confirm(self, e, cid, kind, args):
        """Checks '<code> [PIN]'. Returns (pending_dict, None) or (None, reply). Any failed attempt burns the code."""
        p = self._pending.pop((cid, kind), None)
        if p is None: return None, f'No {kind} waiting for confirmation. Send /{kind} first.'
        if self.clock() > p['expires']: return None, f'That code expired (60 s) - nothing done. Send /{kind} again.'
        if args[0].upper() != p['code']: return None, f'Wrong code - nothing done. Send /{kind} again for a new code.'
        ok, why = self._pin_ok(e, args[1] if len(args) > 1 else None)
        if not ok: return None, why + f' Send /{kind} again for a new code.'
        return p, None

    def _pin_hint(self, e):
        return ' <PIN>' if e.S.get('TELEGRAM_PIN') else ''

    def c_flatten(self, e, args, cid):
        if not args:
            lots = self._lots(e)
            code = self._new_code(cid, 'flatten')
            return (f'CLOSE ALL {len(lots)} position(s) at market and pause entries?\n'
                    f'To confirm within 60 s send:\n/flatten {code}{self._pin_hint(e)}')
        p, why = self._confirm(e, cid, 'flatten', args)
        if not p: return why
        log.warning('telegram control: FLATTEN confirmed from Telegram')
        try:
            r = e.flatten()
        except Exception as ex:
            return f'Flatten FAILED to run ({type(ex).__name__}: {str(ex)[:150]}). Check the app.'
        out = [f"Flatten: {len(r['closed'])} closed, {len(r['failed'])} FAILED. Entries paused."]
        for k, x in r['failed']:
            out.append(f"FAILED {'/'.join(k.split('|')[1:3])}: {str(x)[:120]} - its stop is still on Binance")
        if r.get('still_open'):
            out.append('Binance still shows: ' + ', '.join(f'{k} {v}' for k, v in r['still_open'].items()))
        elif not r['failed']:
            out.append('Binance shows no remaining position on those coins.' if r['closed'] else 'There was nothing to close.')
        return '\n'.join(out)

    def c_close(self, e, args, cid):
        lots_now = self._lots(e)
        held = {l['symbol'] for l in lots_now}
        if args and (cid, 'close') in self._pending and len(args[0]) == 6 and self._coin(args[0]) not in held:
            p, why = self._confirm(e, cid, 'close', args)
            if not p: return why
            out = []
            for k in p['keys']:
                with e.lock:
                    lot = e.state['lots'].get(k)
                    if not lot: out.append(f'{k.split("|")[1]}: no longer open'); continue
                    name = f"{lot['symbol']} {lot['side']} [{lot['sleeve']}]"
                    try:
                        e.close_lot(k, 'closed_from_telegram', (e.marks or {}).get(lot['symbol']))
                        h = e.history[-1] if e.history and e.history[-1].get('id') == k else {}
                        out.append(f"Closed {name}" + (f" P&L {_sgn(h.get('pnl'))} USDT" if h else ''))
                    except Exception as ex:
                        out.append(f"FAILED {name}: {type(ex).__name__} {str(ex)[:120]} - its stop is still on Binance")
            log.warning('telegram control: close confirmed: ' + '; '.join(out)[:300])
            return '\n'.join(out)
        if not args: return 'Usage: /close COIN [LONG|SHORT]   e.g. /close BTC or /close ETH SHORT'
        sym = self._coin(args[0])
        side = args[1].upper() if len(args) > 1 else None
        if side and side not in ('LONG', 'SHORT'): return 'Side must be LONG or SHORT.'
        match = [l for l in lots_now if l['symbol'] == sym and (side is None or l['side'] == side)]
        if not match: return f'No open {sym}{" " + side if side else ""} position.'
        if side is None and len({l['side'] for l in match}) > 1:
            return f'{sym} has both LONG and SHORT open - say which: /close {sym.replace("USDT", "")} LONG'
        code = self._new_code(cid, 'close', keys=[l['key'] for l in match])
        desc = '\n'.join(f"  {l['side']} [{l['sleeve']}] qty {_p(l['qty'])} P&L {_sgn(l['pnl'])}" for l in match)
        return f'Close {sym} at market?\n{desc}\nTo confirm within 60 s send:\n/close {code}{self._pin_hint(e)}'

    @staticmethod
    def _coin(s):
        s = re.sub(r'[^A-Z0-9]', '', str(s).upper())
        return s if s.endswith('USDT') else s + 'USDT'


class _ApiError(Exception):
    pass
