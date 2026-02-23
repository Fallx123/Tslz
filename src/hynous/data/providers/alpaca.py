"""
Alpaca Trading Provider (Stocks)

Paper/live trading via Alpaca Trading API.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
)

logger = logging.getLogger(__name__)

_provider: Optional["AlpacaProvider"] = None


def get_provider(config=None) -> "AlpacaProvider":
    """Get or create AlpacaProvider singleton."""
    global _provider
    if _provider is None:
        api_key = os.environ.get("ALPACA_API_KEY_ID", "")
        api_secret = os.environ.get("ALPACA_API_SECRET_KEY", "")
        paper = True
        if config and hasattr(config, "alpaca"):
            paper = bool(getattr(config.alpaca, "paper", True))
        if config:
            api_key = getattr(config, "alpaca_api_key", "") or api_key
            api_secret = getattr(config, "alpaca_api_secret", "") or api_secret
        _provider = AlpacaProvider(api_key, api_secret, paper=paper)
    return _provider


class AlpacaProvider:
    """Synchronous wrapper around Alpaca Trading API."""

    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self._api_key = api_key
        self._api_secret = api_secret
        self._trading = TradingClient(api_key, api_secret, paper=paper)
        self._paper = paper
        if not api_key or not api_secret:
            logger.warning("Alpaca API keys not set. Trading will be unavailable.")

    @property
    def can_trade(self) -> bool:
        return True

    def get_account(self):
        return self._trading.get_account()

    def is_market_open(self) -> bool:
        """Return whether US equities market is currently open."""
        try:
            clock = self._trading.get_clock()
            return bool(getattr(clock, "is_open", False))
        except Exception:
            return False

    def get_user_state(self) -> dict:
        """Return account + positions in a normalized trading shape."""
        acct = self._trading.get_account()
        positions = self._trading.get_all_positions()

        parsed_positions = []
        unrealized_total = 0.0
        for p in positions:
            qty = float(p.qty or 0)
            side = "long" if qty >= 0 else "short"
            unrealized = float(p.unrealized_pl or 0)
            unrealized_total += unrealized
            parsed_positions.append({
                "coin": p.symbol,
                "side": side,
                "size": abs(qty),
                "size_usd": float(p.market_value or 0),
                "entry_px": float(p.avg_entry_price or 0),
                "mark_px": float(p.current_price or 0),
                "unrealized_pnl": unrealized,
                "return_pct": float(p.unrealized_plpc or 0) * 100,
                "leverage": 1,
                "liquidation_px": None,
                "margin_used": 0,
            })

        return {
            "account_value": float(acct.equity or 0),
            "total_margin": float(getattr(acct, "margin_used", 0) or 0),
            "withdrawable": float(acct.buying_power or 0),
            "unrealized_pnl": unrealized_total,
            "positions": parsed_positions,
        }

    def get_open_orders(self, symbol: Optional[str] = None):
        orders = self._trading.get_orders()
        if symbol:
            symbol = symbol.upper()
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def close_position(self, symbol: str):
        return self._trading.close_position(symbol)

    def submit_market_bracket(
        self,
        symbol: str,
        qty: float,
        side: str,
        stop_loss: float,
        take_profit: float,
    ):
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "long" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=take_profit),
            stop_loss=StopLossRequest(stop_price=stop_loss),
        )
        return self._trading.submit_order(order)

    def submit_limit_bracket(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: float,
        stop_loss: float,
        take_profit: float,
    ):
        order = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "long" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=take_profit),
            stop_loss=StopLossRequest(stop_price=stop_loss),
        )
        return self._trading.submit_order(order)

    def submit_market_order(self, symbol: str, qty: float, side: str):
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "long" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        return self._trading.submit_order(order)

    def submit_limit_order(self, symbol: str, qty: float, side: str, limit_price: float):
        order = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "long" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
        )
        return self._trading.submit_order(order)

    def submit_oco_exit(
        self,
        symbol: str,
        qty: float,
        side: str,
        stop_loss: float,
        take_profit: float,
    ):
        """Submit an OCO exit order (take profit + stop loss)."""
        order = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "long" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            limit_price=take_profit,
            order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=take_profit),
            stop_loss=StopLossRequest(stop_price=stop_loss),
        )
        return self._trading.submit_order(order)

    def submit_option_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str,
        limit_price: Optional[float] = None,
    ) -> dict:
        """Place an option order (buy or sell to close).

        Args:
            symbol: OCC-format option symbol (e.g. AAPL250321C00200000)
            qty: Number of contracts
            side: "buy" or "sell"
            order_type: "market" or "limit"
            limit_price: Required for limit orders

        Returns:
            Order dict from Alpaca
        """
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        if order_type == "limit":
            if limit_price is None:
                raise ValueError("limit_price required for limit option orders")
            order = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=round(float(limit_price), 2),
            )
        else:
            order = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )
        result = self._trading.submit_order(order)
        return result

    def close_option_position(self, symbol: str) -> dict:
        """Sell-to-close an option position at market.

        Looks up the current position quantity and submits a market sell order.

        Args:
            symbol: OCC-format option symbol

        Returns:
            Order dict from Alpaca
        """
        positions = self._trading.get_all_positions()
        pos = next((p for p in positions if p.symbol == symbol), None)
        if pos is None:
            raise ValueError(f"No open option position for {symbol}")
        qty = int(abs(float(pos.qty or 1)))
        entry_price = float(getattr(pos, "avg_entry_price", 0) or 0)
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        result = self._trading.submit_order(order)
        return {"order": result, "entry_price": entry_price, "qty": qty}

    def get_option_position_info(self, symbol: str) -> dict:
        """Get qty and avg entry price for an option position."""
        positions = self._trading.get_all_positions()
        pos = next((p for p in positions if p.symbol == symbol), None)
        if pos is None:
            return {"qty": 0, "entry_price": 0.0}
        qty = int(abs(float(pos.qty or 1)))
        entry_price = float(getattr(pos, "avg_entry_price", 0) or 0)
        return {"qty": qty, "entry_price": entry_price}

    def get_stock_news(self, symbols: list[str], limit: int = 20) -> list[dict]:
        """Fetch recent stock news via Alpaca News API (Benzinga etc.).

        Returns list of dicts with same shape as CryptoCompare provider:
            id, title, body, source, published_on (unix), categories (tickers), url
        """
        import itertools
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest

        api_key = os.environ.get("ALPACA_API_KEY_ID", "")
        api_secret = os.environ.get("ALPACA_API_SECRET_KEY", "")
        client = NewsClient(api_key, api_secret)

        if symbols:
            req = NewsRequest(symbols=",".join(symbols), limit=limit)
        else:
            req = NewsRequest(limit=limit)
        result = client.get_news(req)

        # result.data is a dict keyed by ticker; values are lists of News objects
        all_news = list(itertools.chain.from_iterable(result.data.values()))

        # Deduplicate by id (same article can appear under multiple tickers)
        seen: set[str] = set()
        articles = []
        for n in all_news:
            aid = str(n.id)
            if aid in seen:
                continue
            seen.add(aid)
            articles.append({
                "id": aid,
                "title": n.headline or "",
                "body": (n.summary or "")[:200],
                "source": n.source or "",
                "published_on": int(n.created_at.timestamp()) if n.created_at else 0,
                "categories": ",".join(n.symbols or []),
                "url": n.url or "",
            })

        return articles

    def get_closed_orders(self, limit: int = 20) -> list:
        """Fetch recent closed/filled orders.

        Args:
            limit: Max number of orders to return

        Returns:
            List of Alpaca order objects
        """
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        try:
            req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=limit)
            return list(self._trading.get_orders(filter=req))
        except Exception as e:
            logger.warning("get_closed_orders failed: %s", e)
            return []

    def get_option_greeks(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch option greeks for OCC symbols keyed by symbol."""
        if not symbols:
            return {}
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
            from alpaca.data.requests import OptionSnapshotRequest
            from alpaca.data.enums import OptionsFeed

            client = OptionHistoricalDataClient(self._api_key, self._api_secret)
            req = OptionSnapshotRequest(symbol_or_symbols=symbols, feed=OptionsFeed.INDICATIVE)
            snapshots = client.get_option_snapshot(req)
            out: dict[str, dict] = {}
            for sym, snap in (snapshots or {}).items():
                greeks = getattr(snap, "greeks", None)
                out[sym] = {
                    "delta": (None if greeks is None else getattr(greeks, "delta", None)),
                    "theta": (None if greeks is None else getattr(greeks, "theta", None)),
                    "iv": getattr(snap, "implied_volatility", None),
                }
            return out
        except Exception as e:
            logger.debug("get_option_greeks failed: %s", e)
            return {}
