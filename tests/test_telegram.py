"""Telegram two-way control: auth, every command, confirm flows, PIN, persistence, rate limit, backoff, token hygiene.
No network: a fake Bot API serves scripted getUpdates and records every call."""
import io, json, logging, os, sys, time, threading
import pytest, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import mk_engine, opened, hist           # real Engine on FakeX
import engine as E
import telegram_ctl as T

TOKEN = '123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ'
CHAT = '555111222'


class R:
    def __init__(self, d, code=200): self._d, self.status_code = d, code
    def json(self): return self._d


class FakeTG:
    """Bot API stand-in. updates: list of batches (each a list, or an Exception to raise)."""
    def __init__(self):
        self.batches, self.sent, self.calls, self.next_id = [], [], [], 1000
    def post(self, url, json=None, timeout=None):
        assert TOKEN in url
        method = url.rsplit('/', 1)[1]; self.calls.append((method, json))
        if method == 'getUpdates':
            if not self.batches: return R({'ok': True, 'result': []})
            b = self.batches.pop(0)
            if isinstance(b, Exception): raise b
            if isinstance(b, R): return b
            return R({'ok': True, 'result': b})
        if method == 'sendMessage': self.sent.append(json)
        return R({'ok': True, 'result': True})
    def msg(self, text, chat=CHAT, username=None, age=0):
        self.next_id += 1
        return {'update_id': self.next_id, 'message': {'message_id': self.next_id, 'date': int(time.time() - age), 'text': text,
                                                       'chat': {'id': int(chat) if str(chat).lstrip('-').isdigit() else chat, 'username': username}}}
    def last(self): return self.sent[-1]['text']


class Clock:
    def __init__(self): self.t = time.time()
    def __call__(self): return self.t


def setup(**S):
    e, tmp = mk_engine(**S)
    e.cfg['TELEGRAM_TOKEN'] = TOKEN
    e.S.update(TELEGRAM_CONTROL=True, TELEGRAM_CHAT=CHAT)
    e.equity(); e.check_guards()
    http, clk = FakeTG(), Clock()
    tg = T.TelegramControl(lambda: e, http=http, clock=clk)
    return e, tg, http, clk


def say(tg, http, *texts, **kw):
    http.batches.append([http.msg(t, **kw) for t in texts])
    tg.poll_once()
    return http.last() if http.sent else None


# ------------------------------------------------------------------ auth
def test_other_chats_are_ignored_and_counted():
    e, tg, http, _ = setup()
    say(tg, http, '/status', chat='999')
    say(tg, http, '/flatten', chat='-100777')
    assert http.sent == [] and tg.ignored == 2 and tg.status()['last_ignored_chat'] == -100777
    assert say(tg, http, '/status').startswith('ZackBot TESTNET')


def test_channel_config_matches_username_only():
    e, tg, http, _ = setup(); e.S['TELEGRAM_CHAT'] = '@MyZackChan'
    say(tg, http, '/status', chat='-1001', username='someoneelse')
    assert http.sent == [] and tg.ignored == 1
    assert 'Bot capital' in say(tg, http, '/status', chat='-1001', username='myzackchan')


def test_control_off_does_nothing():
    e, tg, http, _ = setup(); e.S['TELEGRAM_CONTROL'] = False
    http.batches.append([http.msg('/pause')])
    assert tg.poll_once() == 0 and not http.calls and not e.S['ENTRIES_PAUSED']


# ------------------------------------------------------------------ read commands
def test_help_start_unknown():
    e, tg, http, _ = setup()
    assert '/flatten' in say(tg, http, '/help') and '/close COIN' in http.last()
    say(tg, http, '/start')
    kb = http.sent[-1]['reply_markup']['inline_keyboard']
    assert {b['callback_data'] for row in kb for b in row} >= {'/status', '/positions', '/profit', '/pause', '/resume'}
    assert say(tg, http, '/stopbot').startswith('Unknown command')
    assert say(tg, http, 'hello').startswith('Unknown command')
    assert 'Bot capital' in say(tg, http, '/status@ZackBot')


def test_inline_button_runs_command():
    e, tg, http, _ = setup()
    http.batches.append([{'update_id': 5, 'callback_query': {'id': 'cq1', 'data': '/pause',
                                                             'message': {'message_id': 1, 'date': int(time.time()), 'chat': {'id': int(CHAT)}}}}])
    tg.poll_once()
    assert e.S['ENTRIES_PAUSED'] and any(m == 'answerCallbackQuery' for m, _ in http.calls)


def test_status_and_positions():
    e, tg, http, _ = setup(); opened(e); opened(e, 'ETHUSDT')
    e.marks = {'BTCUSDT': 110.0, 'ETHUSDT': 50.0}
    e.next_cycle = {'4h': '2099-01-01T00:00:15+00:00'}
    s = say(tg, http, '/status')
    assert 'TESTNET' in s and 'Engine: ok' in s and 'Bot capital: 500.00' in s and 'Open positions: 2' in s
    assert 'Entries: open' in s and 'Next candle: 4h' in s and 'Today: +0.00%' in s
    p = say(tg, http, '/positions')
    assert 'BTC LONG [T]' in p and 'ETH LONG [T]' in p and 'entry 100' in p and 'mark 110' in p and 'stop' in p and 'R)' in p
    assert 'Total open P&L: +' in p
    e.S['ENTRIES_PAUSED'] = True; assert 'Entries: PAUSED' in say(tg, http, '/status')
    e.state['halted'] = True; assert 'HALTED' in say(tg, http, '/status')


def test_no_positions():
    e, tg, http, _ = setup()
    assert say(tg, http, '/positions') == 'No open positions.'


def test_profit_and_daily():
    e, tg, http, _ = setup()
    assert 'No closed trades' in say(tg, http, '/profit')
    hist(e, 30); hist(e, -10); hist(e, 5, E.now_utc() - E.timedelta(days=20))
    p = say(tg, http, '/profit')
    assert '2 closed' in p and 'Net P&L: +20.00' in p and 'Win rate: 50%' in p and 'Best: BTC LONG +30.00' in p and 'Worst: BTC LONG -10.00' in p
    assert '3 closed' in say(tg, http, '/profit 30') and 'Net P&L: +25.00' in http.last()
    assert 'Usage' in say(tg, http, '/profit abc')
    d = say(tg, http, '/daily')
    assert 'Day start: 500.00' in d and 'Closed today: 2 trade(s), net +20.00' in d


def test_risk_and_signals():
    e, tg, http, _ = setup(PEAK_DD_FLATTEN=0.2); opened(e)
    r = say(tg, http, '/risk')
    assert 'halt at -8.0%' in r and 'flatten at -20.0%' in r and 'Open risk to stops:' in r and 'Max leverage setting: 10x' in r
    assert 'No active entry signals' in say(tg, http, '/signals')
    e.signals = {'T|BTCUSDT': dict(le=True, se=False, close=100.0, time='2026-10-04 08:00:00'),
                 'T|ETHUSDT': dict(le=False, se=False, close=50.0, time='x')}
    s = say(tg, http, '/signals')
    assert 'BTC LONG [T]' in s and 'ETH' not in s


# ------------------------------------------------------------------ pause / resume
def test_pause_resume_persisted_and_halt_blocks_resume():
    e, tg, http, _ = setup()
    assert 'PAUSED' in say(tg, http, '/pause')
    assert json.load(open(e.F['settings']))['ENTRIES_PAUSED'] is True
    e.state['halted'] = True
    assert 'daily loss halt' in say(tg, http, '/resume') and e.S['ENTRIES_PAUSED']
    e.state['halted'] = False
    assert 'RESUMED' in say(tg, http, '/resume')
    assert json.load(open(e.F['settings']))['ENTRIES_PAUSED'] is False


def test_resume_needs_pin_when_set():
    e, tg, http, _ = setup(); e.S['TELEGRAM_PIN'] = T.hash_pin('4321'); e.S['ENTRIES_PAUSED'] = True
    assert 'PIN required' in say(tg, http, '/resume')
    assert 'Wrong PIN' in say(tg, http, '/resume 1111') and e.S['ENTRIES_PAUSED']
    assert 'RESUMED' in say(tg, http, '/resume 4321')
    assert any(m == 'deleteMessage' for m, _ in http.calls)          # PIN message removed from the chat


# ------------------------------------------------------------------ flatten / close confirm flows
def code_of(text, cmd):
    line = [l for l in text.splitlines() if l.startswith(cmd + ' ')][-1]
    return line.split()[1]


def test_flatten_wrong_expired_correct():
    e, tg, http, clk = setup(); opened(e); opened(e, 'ETHUSDT')
    assert 'No flatten waiting' in say(tg, http, '/flatten ABCDEF')
    c = code_of(say(tg, http, '/flatten'), '/flatten')
    assert 'CLOSE ALL 2' in http.last()
    assert 'Wrong code' in say(tg, http, '/flatten ZZZZZZ') and len(e.state['lots']) == 2
    assert 'No flatten waiting' in say(tg, http, f'/flatten {c}')           # a wrong attempt burns the code
    c = code_of(say(tg, http, '/flatten'), '/flatten')
    clk.t += 61
    assert 'expired' in say(tg, http, f'/flatten {c}') and len(e.state['lots']) == 2
    c = code_of(say(tg, http, '/flatten'), '/flatten')
    r = say(tg, http, f'/flatten {c.lower()}')
    assert '2 closed, 0 FAILED' in r and not e.state['lots'] and e.S['ENTRIES_PAUSED']


def test_flatten_reports_failures_honestly():
    e, tg, http, _ = setup(); opened(e)
    e.trade.fail.add('close')
    c = code_of(say(tg, http, '/flatten'), '/flatten')
    r = say(tg, http, f'/flatten {c}')
    assert '0 closed, 1 FAILED' in r and 'stop is still on Binance' in r and 'BTCUSDT' in r and e.state['lots']


def test_flatten_with_pin_and_lockout():
    e, tg, http, clk = setup(); opened(e); e.S['TELEGRAM_PIN'] = T.hash_pin('123456')
    c = code_of(say(tg, http, '/flatten'), '/flatten')
    assert '<PIN>' in http.last()
    assert 'PIN required' in say(tg, http, f'/flatten {c}') and e.state['lots']
    c = code_of(say(tg, http, '/flatten'), '/flatten')
    assert 'Wrong PIN' in say(tg, http, f'/flatten {c} 000000') and e.state['lots']
    c = code_of(say(tg, http, '/flatten'), '/flatten')
    assert '1 closed' in say(tg, http, f'/flatten {c} 123456') and not e.state['lots']
    for _ in range(5):                                                    # brute force -> 15 min lock
        c = code_of(say(tg, http, '/flatten'), '/flatten'); say(tg, http, f'/flatten {c} 999999')
    c = code_of(say(tg, http, '/flatten'), '/flatten')
    assert 'locked' in say(tg, http, f'/flatten {c} 123456')
    clk.t += 901
    c = code_of(say(tg, http, '/flatten'), '/flatten')
    assert 'closed' in say(tg, http, f'/flatten {c} 123456')


def test_close_one_coin():
    e, tg, http, clk = setup(); opened(e); opened(e, 'ETHUSDT')
    assert 'No open SOLUSDT' in say(tg, http, '/close SOL')
    assert 'Side must be' in say(tg, http, '/close BTC UP')
    c = code_of(say(tg, http, '/close btc'), '/close')
    assert 'Close BTCUSDT' in http.last() and 'LONG [T]' in http.last()
    assert 'Wrong code' in say(tg, http, '/close QQQQQQ') and len(e.state['lots']) == 2
    c = code_of(say(tg, http, '/close BTC LONG'), '/close'); clk.t += 61
    assert 'expired' in say(tg, http, f'/close {c}')
    c = code_of(say(tg, http, '/close BTCUSDT'), '/close')
    r = say(tg, http, f'/close {c}')
    assert r.startswith('Closed BTCUSDT LONG [T]') and 'P&L' in r
    assert [l['symbol'] for l in e.state['lots'].values()] == ['ETHUSDT']
    assert e.history[-1]['exit_reason'] == 'closed_from_telegram'


def test_close_both_sides_needs_side_and_failure_is_reported():
    from test_safety import SL, SG
    e, tg, http, _ = setup(); opened(e)
    assert e.open_lot(dict(SL, id='U'), 'BTCUSDT', 'SHORT', SG, None, e.equity()), e.last_skip
    k = next(k for k, l in e.state['lots'].items() if l['side'] == 'SHORT')
    assert 'both LONG and SHORT' in say(tg, http, '/close BTC')
    e.trade.fail.add('close')
    c = code_of(say(tg, http, '/close BTC SHORT'), '/close')
    r = say(tg, http, f'/close {c}')
    assert 'FAILED BTCUSDT SHORT' in r and 'stop is still on Binance' in r and k in e.state['lots']


# ------------------------------------------------------------------ rate limit, offset, stale updates, backoff
def test_rate_limit_20_per_minute():
    e, tg, http, clk = setup()
    http.batches.append([http.msg('/help') for _ in range(25)])
    tg.poll_once()
    assert sum(1 for m in http.sent if 'Too many commands' in m['text']) == 1
    assert sum(1 for m in http.sent if m['text'].startswith('ZackBot commands')) == 20
    clk.t += 61
    assert say(tg, http, '/help').startswith('ZackBot commands')


def test_offset_persisted_and_old_updates_dropped():
    e, tg, http, clk = setup()
    old = http.msg('/pause', age=600)
    http.batches.append([old, http.msg('/help')])
    tg.poll_once()
    assert not e.S['ENTRIES_PAUSED'] and len(http.sent) == 1               # stale command at start-up ignored
    saved = json.load(open(os.path.join(e.dir, T.OFFSET_FILE)))
    assert saved[TOKEN.split(':')[0]] == http.next_id + 1 and TOKEN.split(':')[1] not in json.dumps(saved)
    tg2 = T.TelegramControl(lambda: e, http=http, clock=clk)               # restart: resumes from the saved offset
    tg2.poll_once()
    assert http.calls[-1][1]['offset'] == http.next_id + 1


def test_backoff_on_network_errors_and_thread_survives(monkeypatch, caplog):
    e, tg, http, _ = setup()
    waits = []
    http.batches += [requests.ConnectionError(f'https://api.telegram.org/bot{TOKEN}/getUpdates failed')] * 7
    real_post = http.post
    stop_after = {'n': 0}
    def post(url, json=None, timeout=None):
        stop_after['n'] += 1
        if stop_after['n'] > 8: tg._stop.set()
        return real_post(url, json=json, timeout=timeout)
    http.post = post
    class Ev(threading.Event):
        def wait(self, t=None): waits.append(t); return self.is_set()
    tg._stop = Ev()
    with caplog.at_level(logging.INFO, logger='zackbot'):
        tg._run(tg._stop)
    assert waits == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert tg.backoff == 0.0 and tg.last_error is None and tg.last_poll        # recovered after the errors
    assert TOKEN not in caplog.text and 'AAHdq' not in caplog.text and 'ConnectionError' in caplog.text


def test_api_errors_are_explained_without_token(caplog):
    e, tg, http, _ = setup()
    http.batches.append(R({'ok': False, 'error_code': 409, 'description': f'Conflict bot{TOKEN}'}, 409))
    with caplog.at_level(logging.INFO, logger='zackbot'), pytest.raises(T._ApiError):
        tg.poll_once()
    assert '409' in tg.last_error and 'webhook' in tg.last_error and TOKEN not in tg.last_error


def test_token_never_logged(caplog):
    e, tg, http, _ = setup(); opened(e)
    def boom(url, json=None, timeout=None): raise requests.ConnectionError(url)
    with caplog.at_level(logging.DEBUG):
        say(tg, http, '/status', chat='424242')                            # ignored chat -> local log line
        c = code_of(say(tg, http, '/flatten'), '/flatten'); say(tg, http, f'/flatten {c}')
        http.post = boom; tg.send(CHAT, 'x')                                # failing reply
    assert '424242' in caplog.text
    assert TOKEN not in caplog.text and TOKEN.split(':')[1] not in caplog.text


def test_thread_start_stop_restart():
    e, tg, http, _ = setup(); e.S['TELEGRAM_CONTROL'] = False
    tg.start(); assert tg.status()['running']
    old = tg._thread; tg.restart()
    assert tg._thread is not old and tg.status()['running']
    tg.stop(); old.join(6); tg._thread.join(6)
    assert not old.is_alive() and not tg._thread.is_alive() and not tg.status()['running']
    st = tg.status()
    assert set(st) >= {'running', 'last_poll', 'last_error', 'ignored', 'commands_today'}


# ------------------------------------------------------------------ settings helpers
def test_clean_setting_and_pin_hash():
    assert T.clean_setting('TELEGRAM_CONTROL', 1) is True and T.clean_setting('TELEGRAM_CONTROL', 0) is False
    h = T.clean_setting('TELEGRAM_PIN', '2468')
    assert h.startswith('pbkdf2_sha256$') and '2468' not in h and T.check_pin(h, '2468') and not T.check_pin(h, '2469')
    assert T.clean_setting('TELEGRAM_PIN', h, current='keep') == 'keep'
    assert T.clean_setting('TELEGRAM_PIN', '••••', current=h) == h
    assert T.clean_setting('TELEGRAM_PIN', '') == ''
    for bad in ('123', '123456789', 'abcd', '12 34'):
        with pytest.raises(ValueError): T.clean_setting('TELEGRAM_PIN', bad)
    with pytest.raises(ValueError): T.clean_setting('TELEGRAM_CHAT', '1')
