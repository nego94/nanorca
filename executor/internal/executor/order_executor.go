// internal/executor/order_executor.go — order placement and lifecycle tracking (hot path)
// Routes orders to the correct exchange, tracks open positions, enforces per-order timeouts.
package executor

import (
	"context"
	"fmt"
	"sync"
	"time"

	"go.uber.org/zap"

	"github.com/nanorca/executor/internal/exchanges"
	pb "github.com/nanorca/executor/proto"
)

// Position tracks an open trade.
type Position struct {
	ExchangeOrderID string
	Exchange        string
	Market          string
	Side            string // "long"|"short"|"yes"|"no"|"buy"|"sell"
	EntryPrice      float64
	SizeUSD         float64
	OpenedAt        time.Time
	StopLossPct     float64
	Paper           bool
}

// OrderExecutor manages order placement, monitoring, and closing.
type OrderExecutor struct {
	logger    *zap.Logger
	binance   *exchanges.BinanceClient
	polym     *exchanges.PolymarketClient
	hl        *exchanges.HyperliquidClient
	paperMode bool

	mu        sync.RWMutex
	positions map[string]*Position // keyed by ExchangeOrderID
}

// NewOrderExecutor creates the order executor with injected exchange clients.
func NewOrderExecutor(
	logger *zap.Logger,
	binance *exchanges.BinanceClient,
	polym *exchanges.PolymarketClient,
	hl *exchanges.HyperliquidClient,
	paperMode bool,
) *OrderExecutor {
	return &OrderExecutor{
		logger:    logger.Named("executor"),
		binance:   binance,
		polym:     polym,
		hl:        hl,
		paperMode: paperMode,
		positions: make(map[string]*Position),
	}
}

// PlaceOrder routes an order to the correct exchange and records the position.
func (e *OrderExecutor) PlaceOrder(ctx context.Context, req *pb.PlaceOrderRequest) (*pb.PlaceOrderResponse, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	var (
		orderID     string
		filledPrice float64
		fees        float64
		err         error
	)

	side := req.Side.String()

	switch req.Exchange {
	case "binance":
		orderID, filledPrice, fees, err = e.binance.PlaceOrder(ctx, req.Market, side, req.SizeUsd)
	case "polymarket":
		orderID, filledPrice, fees, err = e.polym.PlaceOrder(ctx, req.Market, side, req.SizeUsd)
	case "hyperliquid":
		orderID, filledPrice, fees, err = e.hl.PlaceOrder(ctx, req.Market, side, req.SizeUsd)
	default:
		return &pb.PlaceOrderResponse{
			Success: false,
			Error:   fmt.Sprintf("unknown exchange: %s", req.Exchange),
		}, nil
	}

	if err != nil {
		e.logger.Error("PlaceOrder failed",
			zap.String("exchange", req.Exchange),
			zap.String("market", req.Market),
			zap.Error(err),
		)
		return &pb.PlaceOrderResponse{Success: false, Error: err.Error()}, nil
	}

	// Track position
	pos := &Position{
		ExchangeOrderID: orderID,
		Exchange:        req.Exchange,
		Market:          req.Market,
		Side:            side,
		EntryPrice:      filledPrice,
		SizeUSD:         req.SizeUsd,
		OpenedAt:        time.Now(),
		StopLossPct:     req.StopLossPct,
		Paper:           req.Paper,
	}
	e.mu.Lock()
	e.positions[orderID] = pos
	e.mu.Unlock()

	e.logger.Info("Order placed",
		zap.String("order_id", orderID),
		zap.String("exchange", req.Exchange),
		zap.String("market", req.Market),
		zap.String("side", side),
		zap.Float64("filled_price", filledPrice),
		zap.Bool("paper", req.Paper),
	)

	slippagePct := 0.0
	if filledPrice > 0 {
		slippagePct = (filledPrice - filledPrice) / filledPrice // will be calc'd properly in Phase 5
	}

	return &pb.PlaceOrderResponse{
		Success:         true,
		ExchangeOrderId: orderID,
		FilledPrice:     filledPrice,
		FilledSizeUsd:   req.SizeUsd,
		SlippagePct:     slippagePct,
	}, nil
}

// CloseOrder closes an open position by exchange order ID.
func (e *OrderExecutor) CloseOrder(ctx context.Context, req *pb.CloseOrderRequest) (*pb.CloseOrderResponse, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	e.mu.RLock()
	pos, ok := e.positions[req.ExchangeOrderId]
	e.mu.RUnlock()

	if !ok {
		return &pb.CloseOrderResponse{
			Success: false,
			Error:   fmt.Sprintf("order %s not found in position tracker", req.ExchangeOrderId),
		}, nil
	}

	var (
		exitPrice float64
		pnl       float64
		fees      float64
		err       error
	)

	switch req.Exchange {
	case "binance":
		exitPrice, pnl, fees, err = e.binance.CloseOrder(ctx, req.ExchangeOrderId, pos.Market, pos.Side)
	case "polymarket":
		exitPrice, pnl, fees, err = e.polym.CloseOrder(ctx, req.ExchangeOrderId, pos.Market, pos.Side)
	case "hyperliquid":
		exitPrice, pnl, fees, err = e.hl.CloseOrder(ctx, req.ExchangeOrderId, pos.Market)
	default:
		return &pb.CloseOrderResponse{
			Success: false,
			Error:   fmt.Sprintf("unknown exchange: %s", req.Exchange),
		}, nil
	}

	if err != nil {
		return &pb.CloseOrderResponse{Success: false, Error: err.Error()}, nil
	}

	// Remove from position tracker
	e.mu.Lock()
	delete(e.positions, req.ExchangeOrderId)
	e.mu.Unlock()

	e.logger.Info("Order closed",
		zap.String("order_id", req.ExchangeOrderId),
		zap.Float64("exit_price", exitPrice),
		zap.Float64("pnl", pnl),
		zap.Float64("fees", fees),
	)

	return &pb.CloseOrderResponse{
		Success:    true,
		ExitPrice:  exitPrice,
		PnlUsd:     pnl,
		FeesUsd:    fees,
	}, nil
}

// GetPositions returns all tracked open positions.
func (e *OrderExecutor) GetPositions(ctx context.Context) []*pb.OpenPosition {
	e.mu.RLock()
	defer e.mu.RUnlock()

	result := make([]*pb.OpenPosition, 0, len(e.positions))
	for _, p := range e.positions {
		result = append(result, &pb.OpenPosition{
			ExchangeOrderId: p.ExchangeOrderID,
			Exchange:        p.Exchange,
			Market:          p.Market,
			EntryPrice:      p.EntryPrice,
			SizeUsd:         p.SizeUSD,
			OpenedAtMs:      p.OpenedAt.UnixMilli(),
		})
	}
	return result
}

// ForceCloseAll closes all open positions (used by capital floor trigger).
func (e *OrderExecutor) ForceCloseAll(ctx context.Context) {
	e.mu.RLock()
	ids := make([]string, 0, len(e.positions))
	for id := range e.positions {
		ids = append(ids, id)
	}
	e.mu.RUnlock()

	for _, id := range ids {
		e.mu.RLock()
		pos := e.positions[id]
		e.mu.RUnlock()
		if pos == nil {
			continue
		}
		_, err := e.CloseOrder(ctx, &pb.CloseOrderRequest{
			Exchange:        pos.Exchange,
			ExchangeOrderId: id,
			Market:          pos.Market,
			Paper:           pos.Paper,
		})
		if err != nil {
			e.logger.Error("ForceCloseAll: failed to close position",
				zap.String("order_id", id), zap.Error(err))
		}
	}
}
