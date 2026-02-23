"""
Tslz Dashboard

Main Reflex application entry point.

Run with:
    cd dashboard
    reflex run
"""

import reflex as rx
from .state import AppState
from .components import navbar
from .pages import home_page, chat_page, graph_page, journal_page, memory_page, login_page, debug_page, settings_page


def _dashboard_content() -> rx.Component:
    """Authenticated dashboard content."""
    return rx.box(
        # Global animation keyframes
        rx.el.style("""
            html, body, #__reflex, #root {
                background: #0a0a0a;
            }
            .nav-btn {
                position: relative;
                overflow: hidden;
                transform: translateY(0);
            }
            .nav-btn::after {
                content: "";
                position: absolute;
                left: -120%;
                top: 0;
                width: 120%;
                height: 100%;
                background: linear-gradient(120deg, transparent 0%, rgba(99,102,241,0.15) 50%, transparent 100%);
                transition: left 0.4s ease;
                pointer-events: none;
            }
            .nav-btn:hover {
                transform: translateY(-1px);
                box-shadow: 0 6px 16px rgba(0,0,0,0.35);
            }
            .nav-btn:hover::after {
                left: 100%;
            }
            .nav-btn:active {
                transform: translateY(0) scale(0.98);
            }
            .nav-btn[data-active="true"] {
                box-shadow: inset 0 0 0 1px rgba(99,102,241,0.35), 0 8px 18px rgba(0,0,0,0.35);
            }
            .electric-btn {
                position: relative;
                overflow: hidden;
            }
            .electric-btn::before {
                content: "";
                position: absolute;
                inset: -2px;
                border-radius: 12px;
                padding: 1px;
                background: conic-gradient(from 0deg, #60a5fa, #3b82f6, #8b5cf6, #60a5fa);
                -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
                -webkit-mask-composite: xor;
                mask-composite: exclude;
                opacity: 0;
                transition: opacity 0.2s ease;
                filter: blur(0.2px);
            }
            .electric-btn::after {
                content: "";
                position: absolute;
                inset: -6px;
                border-radius: 14px;
                border: 1px solid rgba(99,102,241,0.2);
                opacity: 0;
                transition: opacity 0.2s ease;
                box-shadow: 0 0 18px rgba(59,130,246,0.6);
                pointer-events: none;
            }
            .electric-btn:hover::before,
            .electric-btn:hover::after {
                opacity: 1;
            }
            .electric-btn:hover {
                box-shadow: 0 0 22px rgba(59,130,246,0.35);
            }
            /* Dock styles removed (reverted to top nav) */
            @keyframes pnl-pulse-green {
                0% { text-shadow: 0 0 0 rgba(74,222,128,0); }
                50% { text-shadow: 0 0 12px rgba(74,222,128,0.4); }
                100% { text-shadow: 0 0 0 rgba(74,222,128,0); }
            }
            @keyframes pnl-pulse-red {
                0% { text-shadow: 0 0 0 rgba(248,113,113,0); }
                50% { text-shadow: 0 0 12px rgba(248,113,113,0.4); }
                100% { text-shadow: 0 0 0 rgba(248,113,113,0); }
            }
            @keyframes radar-sweep {
                0% { opacity: 1; }
                50% { opacity: 0.4; }
                100% { opacity: 1; }
            }
            @keyframes unread-pulse {
                0%, 100% { transform: scale(1); opacity: 1; }
                50% { transform: scale(1.3); opacity: 0.7; }
            }
            @keyframes fade-slide-in {
                0% { opacity: 0; transform: translateX(-24px); }
                100% { opacity: 1; transform: translateX(0); }
            }
            .page-fade-in {
                animation: fade-slide-in 0.7s ease-out both;
            }
            .pnl-pulse-green { animation: pnl-pulse-green 0.8s ease-out; }
            .pnl-pulse-red { animation: pnl-pulse-red 0.8s ease-out; }
            .market-radar-active { animation: radar-sweep 2s ease-in-out infinite; }
            .unread-dot { animation: unread-pulse 2s ease-in-out infinite; }
            /* Move Reflex watermark off-screen — blocks bottom-pinned UI */
            a[href*="reflex.dev"] {
                position: fixed !important;
                bottom: -200px !important;
                pointer-events: none !important;
                opacity: 0 !important;
            }
        """),

        # Live clock + animated number counter
        rx.script("""
            (function() {
                // Live clock
                setInterval(function() {
                    var el = document.getElementById('live-clock');
                    if (el) {
                        var now = new Date();
                        var h = now.getHours(), m = now.getMinutes(), s = now.getSeconds();
                        var ampm = h >= 12 ? 'PM' : 'AM';
                        h = h % 12 || 12;
                        el.textContent = h + ':' + (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s + ' ' + ampm;
                    }
                }, 1000);

                // Tick counter — animates number changes
                var prev = {};
                setInterval(function() {
                    document.querySelectorAll('[data-tick-target]').forEach(function(el) {
                        var id = el.getAttribute('data-tick-target');
                        var raw = el.getAttribute('data-tick-value');
                        if (!raw) return;
                        var target = parseFloat(raw);
                        if (isNaN(target)) return;
                        var current = prev[id];
                        if (current === undefined) { prev[id] = target; return; }
                        if (Math.abs(current - target) < 0.005) return;
                        var start = current, startTime = Date.now(), duration = 600;
                        var decimals = raw.includes('.') ? raw.split('.')[1].length : 0;
                        function step() {
                            var elapsed = Date.now() - startTime;
                            var t = Math.min(elapsed / duration, 1);
                            t = 1 - Math.pow(1 - t, 3);
                            var v = start + (target - start) * t;
                            el.textContent = el.getAttribute('data-tick-prefix') + v.toFixed(decimals) + el.getAttribute('data-tick-suffix');
                            if (t < 1) requestAnimationFrame(step);
                            else prev[id] = target;
                        }
                        prev[id] = target;
                        el.classList.remove('pnl-pulse-green', 'pnl-pulse-red');
                        if (target > current) el.classList.add('pnl-pulse-green');
                        else el.classList.add('pnl-pulse-red');
                        setTimeout(function() { el.classList.remove('pnl-pulse-green', 'pnl-pulse-red'); }, 800);
                        requestAnimationFrame(step);
                    });
                }, 1000);
            })();
        """),

        # Smart auto-scroll — ChatGPT-style sticky bottom.
        rx.script("""
            (function() {
                var sticky = {};

                function setup(id) {
                    var el = document.getElementById(id);
                    if (!el || el._hs) return;
                    el._hs = true;
                    sticky[id] = true;
                    var timer = null;

                    // Break sticky instantly on scroll up
                    el.addEventListener('wheel', function(e) {
                        if (e.deltaY < 0) {
                            sticky[id] = false;
                            clearTimeout(timer);
                        }
                    }, { passive: true });

                    // Re-enable only after scroll settles at the very bottom
                    el.addEventListener('scroll', function() {
                        clearTimeout(timer);
                        timer = setTimeout(function() {
                            if (el.scrollHeight - el.scrollTop - el.clientHeight < 10) {
                                sticky[id] = true;
                            }
                        }, 150);
                    }, { passive: true });

                    // Auto-scroll on content changes when sticky
                    new MutationObserver(function() {
                        if (sticky[id]) el.scrollTop = el.scrollHeight;
                    }).observe(el, { childList: true, subtree: true, characterData: true });

                    el.scrollTop = el.scrollHeight;
                }

                setInterval(function() {
                    setup('messages-container');
                    setup('quick-chat-messages');
                }, 300);
            })();
        """),

        # Navigation - fixed at top, never scrolls
        rx.box(
            navbar(
                current_page=AppState.current_page,
                on_home=AppState.go_to_home,
                on_chat=AppState.go_to_chat,
                on_journal=AppState.go_to_journal,
                on_memory=AppState.go_to_memory,
                on_settings=AppState.go_to_settings,
                on_debug=AppState.go_to_debug,
                on_logout=AppState.logout,
            ),
            position="fixed",
            top="0",
            left="0",
            right="0",
            z_index="100",
            background="#0a0a0a",
        ),

        # Page content — only the active page is mounted.
        rx.box(
            rx.cond(
                AppState.current_page == "home",
                home_page(),
                rx.cond(
                    AppState.current_page == "chat",
                    chat_page(),
                    rx.cond(
                    AppState.current_page == "journal",
                    journal_page(),
                    rx.cond(
                        AppState.current_page == "settings",
                        settings_page(),
                        rx.cond(
                            AppState.current_page == "debug",
                            debug_page(),
                            memory_page(),
                        ),
                    ),
                ),
            ),
        ),
            flex="1",
            width="100%",
            height="calc(100vh - 56px)",
            margin_top="56px",
            overflow="hidden",
        ),

        # Global styles
        display="flex",
        flex_direction="column",
        background="#0a0a0a",
        height="100vh",
        color="#fafafa",
        font_family="Inter, system-ui, sans-serif",
        overflow="hidden",
        overscroll_behavior="none",
        class_name="page-fade-in",
    )


def index() -> rx.Component:
    """Main entry point."""
    return rx.cond(AppState.show_splash, login_page(), _dashboard_content())


# Create app
app = rx.App(
    theme=rx.theme(
        appearance="dark",
        accent_color="iris",       # Purple/indigo accent (#6366f1 family)
        gray_color="sand",
        radius="medium",
        scaling="100%",
    ),
    style={
        "background": "#0a0a0a",
        "color": "#fafafa",
        "font_family": "Inter, system-ui, sans-serif",
    },
    stylesheets=[
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
        "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap",
    ],
)

app.add_page(index, route="/", title="Tslz", on_load=AppState.load_page)


# Proxy Nous API through the Reflex backend so the browser doesn't need
# direct access to port 3100 (blocked by UFW).
async def _nous_proxy(request):
    """Proxy /api/nous/* → localhost:3100/v1/*"""
    import httpx
    from starlette.responses import JSONResponse
    path = request.path_params.get("path", "graph")
    qs = str(request.query_params)
    url = f"http://localhost:3100/v1/{path}"
    if qs:
        url += f"?{qs}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            return JSONResponse(resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


app._api.add_route("/api/nous/{path:path}", _nous_proxy)


async def _live_ohlcv(request):
    """Return OHLCV candles + computed technical indicators for the live chart.

    GET /api/live-ohlcv/{symbol}?tf=15m
    Returns JSON with candles[] and indicators{}.
    """
    import time as _time
    from starlette.responses import JSONResponse

    symbol = request.path_params.get("symbol", "BTC").upper()
    tf     = request.query_params.get("tf", "15m")

    tf_to_ms = {
        "1m": 60_000, "3m": 180_000, "5m": 300_000,
        "15m": 900_000, "30m": 1_800_000,
        "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
    }
    tf_ms   = tf_to_ms.get(tf, 900_000)
    end_ms  = int(_time.time() * 1000)
    start_ms = end_ms - 300 * tf_ms

    try:
        import re
        from hynous.core.config import load_config
        from hynous.data.providers import get_market_provider
        config = load_config()
        provider = get_market_provider(config)
        fetch_symbol = symbol
        # If OCC option symbol, use underlying for chart candles.
        m = re.match(r'^([A-Z]{1,6})(\d{6})([CP])(\d{8})$', symbol)
        if m:
            fetch_symbol = m.group(1)
        candles  = provider.get_candles(fetch_symbol, tf, start_ms, end_ms)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    if not candles:
        return JSONResponse({"error": f"No candles for {symbol}"}, status_code=404)

    closes  = [c["c"] for c in candles]
    volumes = [c["v"] for c in candles]

    def _sma(values, period):
        result = [None] * len(values)
        for i in range(period - 1, len(values)):
            result[i] = sum(values[i - period + 1 : i + 1]) / period
        return result

    def _ema(values, period):
        result = [None] * len(values)
        if len(values) < period:
            return result
        k = 2.0 / (period + 1)
        result[period - 1] = sum(values[:period]) / period
        for i in range(period, len(values)):
            result[i] = values[i] * k + result[i - 1] * (1 - k)
        return result

    def _rsi(values, period=14):
        result = [None] * len(values)
        if len(values) < period + 1:
            return result
        gains, losses = [], []
        for i in range(1, period + 1):
            d = values[i] - values[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        avg_g = sum(gains) / period
        avg_l = sum(losses) / period
        result[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
        for i in range(period + 1, len(values)):
            d = values[i] - values[i - 1]
            avg_g = (avg_g * (period - 1) + max(d, 0))  / period
            avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
            result[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
        return result

    def _macd(values, fast=12, slow=26, sig=9):
        fe = _ema(values, fast)
        se = _ema(values, slow)
        ml = [None if fe[i] is None or se[i] is None else fe[i] - se[i] for i in range(len(values))]
        valid = [(i, v) for i, v in enumerate(ml) if v is not None]
        sl = [None] * len(values)
        hl = [None] * len(values)
        if len(valid) >= sig:
            ema_sig = _ema([v for _, v in valid], sig)
            for idx, (i, _) in enumerate(valid):
                if idx >= sig - 1:
                    sl[i] = ema_sig[idx]
        for i in range(len(values)):
            if ml[i] is not None and sl[i] is not None:
                hl[i] = ml[i] - sl[i]
        return ml, sl, hl

    def _bollinger(values, period=20, k=2.0):
        mid   = _sma(values, period)
        upper = [None] * len(values)
        lower = [None] * len(values)
        for i in range(period - 1, len(values)):
            w   = values[i - period + 1 : i + 1]
            avg = mid[i]
            std = (sum((x - avg) ** 2 for x in w) / period) ** 0.5
            upper[i] = avg + k * std
            lower[i] = avg - k * std
        return upper, mid, lower

    sma50          = _sma(closes, 50)
    ema12          = _ema(closes, 12)
    ema26          = _ema(closes, 26)
    bb_upper, bb_mid, bb_lower = _bollinger(closes)
    rsi14          = _rsi(closes, 14)
    macd_l, macd_s, macd_h    = _macd(closes)
    vol_sma        = _sma(volumes, 20)

    return JSONResponse({
        "display_symbol": symbol,
        "fetch_symbol": fetch_symbol,
        "candles": candles,
        "indicators": {
            "sma50":       sma50,
            "ema12":       ema12,
            "ema26":       ema26,
            "bb_upper":    bb_upper,
            "bb_mid":      bb_mid,
            "bb_lower":    bb_lower,
            "rsi":         rsi14,
            "macd":        macd_l,
            "macd_signal": macd_s,
            "macd_hist":   macd_h,
            "vol_sma":     vol_sma,
        },
    })


app._api.add_route("/api/live-ohlcv/{symbol}", _live_ohlcv)

def _compute_indicators(candles):
    if not candles:
        return {}

    closes  = [c["c"] for c in candles]
    volumes = [c["v"] for c in candles]

    def _sma(values, period):
        result = [None] * len(values)
        for i in range(period - 1, len(values)):
            result[i] = sum(values[i - period + 1 : i + 1]) / period
        return result

    def _ema(values, period):
        result = [None] * len(values)
        if len(values) < period:
            return result
        k = 2.0 / (period + 1)
        result[period - 1] = sum(values[:period]) / period
        for i in range(period, len(values)):
            result[i] = values[i] * k + result[i - 1] * (1 - k)
        return result

    def _rsi(values, period=14):
        result = [None] * len(values)
        if len(values) < period + 1:
            return result
        gains, losses = [], []
        for i in range(1, period + 1):
            d = values[i] - values[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        avg_g = sum(gains) / period
        avg_l = sum(losses) / period
        result[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
        for i in range(period + 1, len(values)):
            d = values[i] - values[i - 1]
            avg_g = (avg_g * (period - 1) + max(d, 0))  / period
            avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
            result[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
        return result

    def _macd(values, fast=12, slow=26, sig=9):
        fe = _ema(values, fast)
        se = _ema(values, slow)
        ml = [None if fe[i] is None or se[i] is None else fe[i] - se[i] for i in range(len(values))]
        valid = [(i, v) for i, v in enumerate(ml) if v is not None]
        sl = [None] * len(values)
        hl = [None] * len(values)
        if len(valid) >= sig:
            ema_sig = _ema([v for _, v in valid], sig)
            for idx, (i, _) in enumerate(valid):
                if idx >= sig - 1:
                    sl[i] = ema_sig[idx]
        for i in range(len(values)):
            if ml[i] is not None and sl[i] is not None:
                hl[i] = ml[i] - sl[i]
        return ml, sl, hl

    def _bollinger(values, period=20, k=2.0):
        mid   = _sma(values, period)
        upper = [None] * len(values)
        lower = [None] * len(values)
        for i in range(period - 1, len(values)):
            w   = values[i - period + 1 : i + 1]
            avg = mid[i]
            std = (sum((x - avg) ** 2 for x in w) / period) ** 0.5
            upper[i] = avg + k * std
            lower[i] = avg - k * std
        return upper, mid, lower

    sma50          = _sma(closes, 50)
    ema12          = _ema(closes, 12)
    ema26          = _ema(closes, 26)
    bb_upper, bb_mid, bb_lower = _bollinger(closes)
    rsi14          = _rsi(closes, 14)
    macd_l, macd_s, macd_h    = _macd(closes)
    vol_sma        = _sma(volumes, 20)

    return {
        "sma50":       sma50,
        "ema12":       ema12,
        "ema26":       ema26,
        "bb_upper":    bb_upper,
        "bb_mid":      bb_mid,
        "bb_lower":    bb_lower,
        "rsi":         rsi14,
        "macd":        macd_l,
        "macd_signal": macd_s,
        "macd_hist":   macd_h,
        "vol_sma":     vol_sma,
    }


async def _trading_candles(request):
    """Return OHLCV candles for the trading chart.

    GET /api/trading-candles/{symbol}?tf=15m&limit=300
    Uses yfinance for equities.
    Returns: { candles: [...], indicators: {...}, source: "yf", symbol }
    """
    import time as _time
    from starlette.responses import JSONResponse

    raw_symbol = request.path_params.get("symbol", "BTC").upper()
    tf         = request.query_params.get("tf", "15m")
    limit_q    = request.query_params.get("limit", "")

    # Strip OCC option suffix → underlying ticker
    import re as _re
    m = _re.match(r'^([A-Z]{1,6})\d{6}[CP]\d{8}$', raw_symbol)
    symbol = m.group(1) if m else raw_symbol

    tf_ms = {
        "1m": 60_000, "3m": 180_000, "5m": 300_000,
        "15m": 900_000, "30m": 1_800_000,
        "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
    }
    interval_ms = tf_ms.get(tf, 900_000)
    try:
        limit = int(limit_q) if limit_q else 300
    except Exception:
        limit = 300
    if limit < 50:
        limit = 50
    if limit > 2000:
        limit = 2000
    end_ms      = int(_time.time() * 1000)
    start_ms    = end_ms - limit * interval_ms

    # ── yfinance (stocks) ────────────────────────────────────────────────
    try:
        import yfinance as yf

        yf_tf_map = {
            "1m": "1m", "3m": "2m", "5m": "5m", "15m": "15m",
            "30m": "30m", "1h": "60m", "4h": "1h", "1d": "1d",
        }
        yf_period_map = {
            "1m": "1d", "3m": "5d", "5m": "5d", "15m": "60d",
            "30m": "60d", "1h": "730d", "4h": "730d", "1d": "max",
        }
        yf_interval = yf_tf_map.get(tf, "15m")
        yf_period   = yf_period_map.get(tf, "60d")

        ticker = yf.Ticker(symbol)
        df = ticker.history(interval=yf_interval, period=yf_period)
        if df is None or df.empty:
            return JSONResponse({"error": f"No data for {symbol}"}, status_code=404)

        df = df.tail(limit)
        candles = []
        for ts, row in df.iterrows():
            t_ms = int(ts.timestamp() * 1000)
            candles.append({
                "t": t_ms,
                "o": float(row["Open"]),
                "h": float(row["High"]),
                "l": float(row["Low"]),
                "c": float(row["Close"]),
                "v": float(row["Volume"]),
            })
        indicators = _compute_indicators(candles)
        return JSONResponse({"candles": candles, "indicators": indicators,
                             "source": "yf", "symbol": symbol})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


app._api.add_route("/api/trading-candles/{symbol}", _trading_candles)


# Eagerly start agent + daemon when the ASGI backend starts.
# This runs via Reflex's lifespan task system (Starlette lifespan protocol).
async def _eager_agent_start():
    import asyncio, sys
    await asyncio.sleep(3)
    try:
        from .state import _get_agent
        agent = await asyncio.to_thread(_get_agent)
        if agent:
            print("[hynous] Agent + daemon started eagerly on boot", file=sys.stderr, flush=True)
        else:
            print("[hynous] Agent failed to start on boot", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[hynous] Eager start error: {e}", file=sys.stderr, flush=True)

app.register_lifespan_task(_eager_agent_start)
