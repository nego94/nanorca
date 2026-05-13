"""
bot/execution/order_router.py — Routes orders to the Go executor via gRPC.

This is the Python side of the Python↔Go bridge. All exchange I/O goes
through the Go executor service for maximum execution speed.
"""
from __future__ import annotations

import logging
from typing import Any

import grpc

log = logging.getLogger("nanorca.execution.router")

# Proto-generated stubs are imported after `make proto` generates them.
# Fallback to stub classes during skeleton phase so imports don't break.
try:
    from proto import nanorca_pb2 as pb2
    from proto import nanorca_pb2_grpc as pb2_grpc
    _PROTO_AVAILABLE = True
except ImportError:
    log.warning("Proto stubs not generated yet — run `make proto`. Using stub mode.")
    _PROTO_AVAILABLE = False
    pb2 = None
    pb2_grpc = None


class OrderRouter:
    """Routes all exchange operations through the Go executor via gRPC."""

    def __init__(self, config) -> None:
        self._config = config
        self._channel: grpc.aio.Channel | None = None
        self._stub = None

    async def connect(self) -> None:
        """Open gRPC channel to Go executor. Called at startup."""
        addr = self._config.executor_grpc_addr
        self._channel = grpc.aio.insecure_channel(addr)
        if _PROTO_AVAILABLE:
            self._stub = pb2_grpc.ExecutorServiceStub(self._channel)
        log.info(f"gRPC channel opened to executor at {addr}")

    async def disconnect(self) -> None:
        """Close gRPC channel gracefully."""
        if self._channel:
            await self._channel.close()
            log.info("gRPC channel closed")

    async def scan_markets(self, markets: list[str]) -> list[dict[str, Any]]:
        """
        Call the Go executor ScanMarkets RPC.

        Returns a list of market snapshot dicts.
        Falls back to empty list if executor is not yet reachable (skeleton mode).
        """
        if not _PROTO_AVAILABLE or self._stub is None:
            log.debug("scan_markets: proto not available — returning empty (skeleton mode)")
            return []

        try:
            req = pb2.MarketScanRequest(markets=markets)
            resp = await self._stub.ScanMarkets(req, timeout=15)
            snapshots = []
            for snap in resp.snapshots:
                snapshots.append({
                    "exchange": snap.exchange,
                    "market": snap.market,
                    "price": snap.price,
                    "bid": snap.bid,
                    "ask": snap.ask,
                    "volume_24h": snap.volume_24h,
                    "funding_rate": snap.funding_rate,
                    "timestamp_ms": snap.timestamp_ms,
                    "available": snap.available,
                })
            log.debug(f"Scan returned {len(snapshots)} snapshots, {resp.error_count} errors")
            return snapshots
        except grpc.RpcError as e:
            log.error(f"scan_markets gRPC error: {e.code()} — {e.details()}")
            return []

    async def place_order(self, decision: dict[str, Any], paper: bool) -> dict[str, Any]:
        """
        Call the Go executor PlaceOrder RPC.

        Returns a result dict with order_id, filled_price, size_usd.
        """
        if not _PROTO_AVAILABLE or self._stub is None:
            log.debug("place_order: proto not available — returning paper stub (skeleton mode)")
            return {
                "exchange_order_id": f"STUB_{decision.get('market', 'UNK')}",
                "filled_price": 0.0,
                "filled_size_usd": 0.0,
                "paper": True,
            }

        size_usd = decision.get("size_usd", 0.0)
        try:
            req = pb2.PlaceOrderRequest(
                exchange=decision["exchange"],
                market=decision["market"],
                side=_side_enum(decision.get("direction", "buy"), pb2),
                size_usd=size_usd,
                stop_loss_pct=decision.get("stop_loss_pct", 0.0),
                paper=paper,
            )
            resp = await self._stub.PlaceOrder(req, timeout=12)
            if not resp.success:
                raise RuntimeError(f"PlaceOrder failed: {resp.error}")
            return {
                "exchange_order_id": resp.exchange_order_id,
                "filled_price": resp.filled_price,
                "filled_size_usd": resp.filled_size_usd,
                "slippage_pct": resp.slippage_pct,
                "paper": paper,
            }
        except grpc.RpcError as e:
            raise RuntimeError(f"PlaceOrder gRPC error: {e.code()} — {e.details()}") from e

    async def get_positions(self) -> list[dict]:
        """Fetch all open positions from the Go executor."""
        if not _PROTO_AVAILABLE or self._stub is None:
            return []
        try:
            resp = await self._stub.GetPositions(pb2.GetPositionsRequest(), timeout=10)
            return [
                {
                    "exchange_order_id": p.exchange_order_id,
                    "exchange":   p.exchange,
                    "market":     p.market,
                    "side":       p.side,
                    "entry_price":    p.entry_price,
                    "current_price":  p.current_price,
                    "size_usd":       p.size_usd,
                    "unrealized_pnl": p.unrealized_pnl,
                }
                for p in resp.positions
            ]
        except grpc.RpcError as e:
            log.error(f"get_positions gRPC error: {e.code()}")
            return []

    async def close_all_positions(self) -> None:
        """Close all open positions (used by capital floor trigger)."""
        if not _PROTO_AVAILABLE or self._stub is None:
            log.debug("close_all_positions: proto not available — noop (skeleton mode)")
            return
        positions_resp = await self._stub.GetPositions(pb2.GetPositionsRequest(), timeout=10)
        for pos in positions_resp.positions:
            try:
                req = pb2.CloseOrderRequest(
                    exchange=pos.exchange,
                    exchange_order_id=pos.exchange_order_id,
                    market=pos.market,
                    paper=True,  # floor close is always treated as safe
                )
                await self._stub.CloseOrder(req, timeout=12)
                log.info(f"Force-closed position {pos.exchange_order_id} on {pos.exchange}")
            except grpc.RpcError as e:
                log.error(f"Failed to force-close {pos.exchange_order_id}: {e}")

    async def health_check(self) -> bool:
        """Returns True if Go executor is healthy."""
        if not _PROTO_AVAILABLE or self._stub is None:
            return False
        try:
            resp = await self._stub.Health(pb2.HealthRequest(), timeout=5)
            return resp.ok
        except grpc.RpcError:
            return False


def _side_enum(direction: str, pb2) -> int:
    """Convert direction string to proto OrderSide enum value."""
    mapping = {"buy": pb2.BUY, "sell": pb2.SELL, "long": pb2.LONG, "short": pb2.SHORT}
    return mapping.get(direction.lower(), pb2.BUY)
