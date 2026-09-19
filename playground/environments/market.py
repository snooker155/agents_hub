"""
Market environment — a continuous double auction with price-time priority.

The world is a program: orders are matched by an actual matching engine, cash
and shares are conserved, and you cannot sell what you do not own. That is the
whole point — if a model adjudicated trades you would get money from nowhere and
the same share sold twice, and no amount of prompt engineering fixes that.

Partial observation: every agent sees the public book and tape, but only its own
cash, holdings and orders. The private-value spread in ``env_params`` is what
makes agents disagree, and disagreement is what makes a market.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple

from playground.environments import register
from playground.environments.base import Environment
from playground.models import ActionResult


@register
class MarketEnvironment(Environment):
    env_id = "market"
    env_name = "Stock Exchange"
    description = (
        "A continuous double auction. Agents submit limit orders; a real "
        "matching engine crosses them by price-time priority. Cash and shares "
        "are conserved."
    )
    renderer = "market"

    PARAM_SCHEMA = [
        {"name": "ticker", "type": "string", "default": "ACME",
         "description": "Symbol being traded."},
        {"name": "starting_cash", "type": "number", "default": 10000.0,
         "description": "Cash each agent begins with."},
        {"name": "starting_shares", "type": "integer", "default": 100,
         "description": "Shares each agent begins with."},
        {"name": "opening_price", "type": "number", "default": 100.0,
         "description": "Reference price before any trade."},
        {"name": "fundamental_value", "type": "number", "default": 100.0,
         "description": "The 'true' value. Agents see a noisy private estimate of it."},
        {"name": "value_noise", "type": "number", "default": 8.0,
         "description": "Spread of private value estimates. Zero means everyone "
                        "agrees and nothing trades."},
        {"name": "max_order_size", "type": "integer", "default": 50,
         "description": "Largest quantity one order may carry."},
    ]

    ACTIONS = [
        {"name": "submit_order",
         "description": "Place a limit order. Buying requires cash; selling requires shares.",
         "args": [
             {"name": "side", "type": "string"},
             {"name": "price", "type": "number"},
             {"name": "qty", "type": "integer"},
         ]},
        {"name": "cancel_orders",
         "description": "Cancel all of your resting orders.",
         "args": []},
        {"name": "speak_to",
         "description": "Send a message to another agent (delivered next tick).",
         "args": [{"name": "agent", "type": "string"}, {"name": "text", "type": "string"}]},
        {"name": "hold", "description": "Do nothing this tick.", "args": []},
    ]

    OBJECTIVES = ["max_pnl", "max_shares", "max_cash"]
    IDLE_ACTIONS = ("hold",)

    def __init__(self, params: Optional[Dict[str, Any]] = None, seed: int = 42):
        super().__init__(params, seed=seed)
        self.rng = random.Random(self.seed)
        self.last_price: float = float(self.params["opening_price"])
        self.bids: List[Dict[str, Any]] = []     # resting buys,  best (highest) first
        self.asks: List[Dict[str, Any]] = []     # resting sells, best (lowest)  first
        self.tape: List[Dict[str, Any]] = []     # executed trades, newest last
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self._private_values: Dict[str, float] = {}
        self._order_seq = 0

    # ── Setup ────────────────────────────────────────────────────────────────

    def register_agents(self, agents: List[str]) -> None:
        """Open an account and draw a private value estimate per agent."""
        noise = float(self.params["value_noise"])
        fundamental = float(self.params["fundamental_value"])
        for name in agents:
            self.accounts[name] = {
                "cash": float(self.params["starting_cash"]),
                "shares": int(self.params["starting_shares"]),
                "start_equity": None,
            }
            self._private_values[name] = round(
                self.rng.gauss(fundamental, noise), 2
            ) if noise > 0 else fundamental
            self.accounts[name]["start_equity"] = self._equity(name)

    def _equity(self, agent: str) -> float:
        acct = self.accounts.get(agent) or {}
        return round(float(acct.get("cash", 0)) + int(acct.get("shares", 0)) * self.last_price, 2)

    # ── Observation ──────────────────────────────────────────────────────────

    def observe(self, agent: str) -> Dict[str, Any]:
        acct = self.accounts.get(agent) or {"cash": 0, "shares": 0}
        return {
            "tick": self.tick,
            "ticker": self.params["ticker"],
            "last_price": self.last_price,
            # Public: the top of book and the recent tape. Everyone sees these.
            "best_bid": self.bids[0]["price"] if self.bids else None,
            "best_ask": self.asks[0]["price"] if self.asks else None,
            "book_depth": {
                "bids": [{"price": o["price"], "qty": o["qty"]} for o in self.bids[:5]],
                "asks": [{"price": o["price"], "qty": o["qty"]} for o in self.asks[:5]],
            },
            "recent_trades": [
                {"price": t["price"], "qty": t["qty"]} for t in self.tape[-5:]
            ],
            # Private: only this agent's own position and estimate.
            "your_cash": round(float(acct["cash"]), 2),
            "your_shares": int(acct["shares"]),
            "your_equity": self._equity(agent),
            "your_resting_orders": [
                {"side": o["side"], "price": o["price"], "qty": o["qty"]}
                for o in self.bids + self.asks if o["agent"] == agent
            ],
            "your_private_value_estimate": self._private_values.get(agent),
            "messages": self.drain_inbox(agent),
        }

    # ── Actions ──────────────────────────────────────────────────────────────

    def apply(self, agent: str, action: str, args: Dict[str, Any]) -> ActionResult:
        if agent not in self.accounts:
            return ActionResult(agent, action, args, False, f"unknown agent {agent!r}")

        if action == "hold":
            return ActionResult(agent, action, args, True, "held")

        if action == "speak_to":
            target = str(args.get("agent") or "")
            if target not in self.accounts:
                return ActionResult(agent, action, args, False, f"no such agent {target!r}")
            return self.queue_message(agent, target, str(args.get("text") or ""))

        if action == "cancel_orders":
            before = len(self.bids) + len(self.asks)
            self.bids = [o for o in self.bids if o["agent"] != agent]
            self.asks = [o for o in self.asks if o["agent"] != agent]
            n = before - len(self.bids) - len(self.asks)
            return ActionResult(agent, action, args, True, f"cancelled {n} order(s)")

        if action != "submit_order":
            return ActionResult(agent, action, args, False, f"unknown action {action!r}")

        return self._submit_order(agent, args)

    def _submit_order(self, agent: str, args: Dict[str, Any]) -> ActionResult:
        side = str(args.get("side") or "").lower().strip()
        if side not in ("buy", "sell"):
            return ActionResult(agent, "submit_order", args, False,
                                "side must be 'buy' or 'sell'")
        try:
            price = round(float(args.get("price")), 2)
            qty = int(args.get("qty"))
        except (TypeError, ValueError):
            return ActionResult(agent, "submit_order", args, False,
                                "price must be a number and qty an integer")
        if price <= 0 or qty <= 0:
            return ActionResult(agent, "submit_order", args, False,
                                "price and qty must be positive")
        max_size = int(self.params["max_order_size"])
        if qty > max_size:
            return ActionResult(agent, "submit_order", args, False,
                                f"qty {qty} exceeds max order size {max_size}")

        acct = self.accounts[agent]
        # Conservation is enforced here, not hoped for: an agent cannot buy
        # with money it lacks or sell shares it does not hold, counting what is
        # already committed to resting orders.
        if side == "buy":
            committed = sum(o["price"] * o["qty"] for o in self.bids if o["agent"] == agent)
            if committed + price * qty > acct["cash"] + 1e-9:
                return ActionResult(agent, "submit_order", args, False,
                                    f"insufficient cash: {acct['cash']:.2f} available, "
                                    f"{committed:.2f} already committed")
        else:
            committed = sum(o["qty"] for o in self.asks if o["agent"] == agent)
            if committed + qty > acct["shares"]:
                return ActionResult(agent, "submit_order", args, False,
                                    f"insufficient shares: {acct['shares']} held, "
                                    f"{committed} already committed")

        self._order_seq += 1
        order = {
            "id": self._order_seq, "agent": agent, "side": side,
            "price": price, "qty": qty, "tick": self.tick,
        }
        fills = self._match(order)
        if order["qty"] > 0:
            book = self.bids if side == "buy" else self.asks
            book.append(order)
            self._sort_books()

        filled = sum(f["qty"] for f in fills)
        return ActionResult(
            agent, "submit_order", {"side": side, "price": price, "qty": qty}, True,
            (f"filled {filled} @ {fills[-1]['price']}" if fills else "resting on the book")
            + (f", {order['qty']} remaining" if order["qty"] else ""),
            {"fills": fills, "resting_qty": order["qty"]},
        )

    def _sort_books(self) -> None:
        # Price-time priority: best price first, earliest order id breaking ties.
        self.bids.sort(key=lambda o: (-o["price"], o["id"]))
        self.asks.sort(key=lambda o: (o["price"], o["id"]))

    def _match(self, order: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Cross an incoming order against the book. Mutates ``order['qty']``."""
        fills: List[Dict[str, Any]] = []
        book = self.asks if order["side"] == "buy" else self.bids

        while order["qty"] > 0 and book:
            best = book[0]
            crosses = (order["price"] >= best["price"]) if order["side"] == "buy" \
                else (order["price"] <= best["price"])
            if not crosses:
                break
            qty = min(order["qty"], best["qty"])
            # The resting order sets the price — standard, and it means an
            # aggressive order cannot manufacture a better price for itself.
            price = best["price"]
            buyer = order["agent"] if order["side"] == "buy" else best["agent"]
            seller = best["agent"] if order["side"] == "buy" else order["agent"]
            self._settle(buyer, seller, price, qty)

            order["qty"] -= qty
            best["qty"] -= qty
            if best["qty"] == 0:
                book.pop(0)

            trade = {"price": price, "qty": qty, "buyer": buyer,
                     "seller": seller, "tick": self.tick}
            self.tape.append(trade)
            fills.append(trade)
            self.last_price = price
            self.log_event(f"{buyer} bought {qty} from {seller} @ {price}")
            # A fill is the market acting on you. In triggered mode the resting
            # side never asked to be woken and would otherwise sleep through
            # the only thing that happened to its position.
            self.poke(buyer, f"your buy of {qty} @ {price} filled")
            self.poke(seller, f"your sell of {qty} @ {price} filled")

        return fills

    def _settle(self, buyer: str, seller: str, price: float, qty: int) -> None:
        self.accounts[buyer]["cash"] -= price * qty
        self.accounts[buyer]["shares"] += qty
        self.accounts[seller]["cash"] += price * qty
        self.accounts[seller]["shares"] -= qty

    # ── Frame / scoring ──────────────────────────────────────────────────────

    def frame(self) -> Dict[str, Any]:
        return {
            "renderer": self.renderer,
            "tick": self.tick,
            "ticker": self.params["ticker"],
            "last_price": self.last_price,
            "best_bid": self.bids[0]["price"] if self.bids else None,
            "best_ask": self.asks[0]["price"] if self.asks else None,
            "book": {
                "bids": [{"price": o["price"], "qty": o["qty"], "agent": o["agent"]}
                         for o in self.bids[:8]],
                "asks": [{"price": o["price"], "qty": o["qty"], "agent": o["agent"]}
                         for o in self.asks[:8]],
            },
            "tape": self.tape[-12:],
            "agents": [
                {"name": name, "cash": round(a["cash"], 2), "shares": a["shares"],
                 "equity": self._equity(name)}
                for name, a in sorted(self.accounts.items())
            ],
            "price_history": [t["price"] for t in self.tape][-60:],
        }

    def score(self) -> Dict[str, Any]:
        return {
            name: {
                "max_pnl": round(self._equity(name) - (a["start_equity"] or 0.0), 2),
                "max_cash": round(a["cash"], 2),
                "max_shares": a["shares"],
            }
            for name, a in sorted(self.accounts.items())
        }

    def state(self) -> Dict[str, Any]:
        return {**self.frame(), "private_values": dict(self._private_values)}
