// internal/feed/ws_feed.go — WebSocket feed manager (hot path)
// Manages persistent WebSocket connections to Binance and Hyperliquid.
// Reconnects automatically on disconnection with exponential backoff.
package feed

import (
	"context"
	"time"

	"go.uber.org/zap"
	"github.com/nanorca/executor/internal/exchanges"
)

// WSFeedManager manages all WebSocket subscriptions.
type WSFeedManager struct {
	logger  *zap.Logger
	binance *exchanges.BinanceClient
	hl      *exchanges.HyperliquidClient
}

// NewWSFeedManager creates the feed manager.
func NewWSFeedManager(logger *zap.Logger, binance *exchanges.BinanceClient, hl *exchanges.HyperliquidClient) *WSFeedManager {
	return &WSFeedManager{logger: logger.Named("ws_feed"), binance: binance, hl: hl}
}

// Run starts all WebSocket subscriptions. Blocks until ctx is cancelled.
func (m *WSFeedManager) Run(ctx context.Context) {
	m.logger.Info("WebSocket feed manager starting")
	go m.runWithReconnect(ctx, "binance_book_ticker", func(ctx context.Context) error {
		ch := make(chan exchanges.BookTick, 100)
		go func() {
			for range ch {
				// TODO Phase 2: cache tick in shared price map
			}
		}()
		return m.binance.SubscribeBookTicker(ctx, "BTCUSDT", ch)
	})
	go m.runWithReconnect(ctx, "hyperliquid_funding_poll", func(ctx context.Context) error {
		// Poll funding rates every 30s (Hyperliquid WS requires complex EIP-712 auth — Phase 5)
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return nil
			case <-ticker.C:
				rates, err := m.hl.GetFundingRates(ctx)
				if err != nil {
					m.logger.Warn("Hyperliquid funding rate poll failed", zap.Error(err))
					continue
				}
				m.logger.Debug("Hyperliquid funding rates polled", zap.Int("count", len(rates)))
				// TODO: cache in shared price map
				_ = rates
			}
		}
	})
	<-ctx.Done()
	m.logger.Info("WebSocket feed manager stopped")
}

// runWithReconnect wraps a feed function with exponential backoff reconnection.
func (m *WSFeedManager) runWithReconnect(ctx context.Context, name string, fn func(context.Context) error) {
	backoff, maxBackoff := time.Second, 60*time.Second
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		m.logger.Info("Starting WebSocket feed", zap.String("feed", name))
		err := fn(ctx)
		select {
		case <-ctx.Done():
			return
		default:
		}
		if err != nil {
			m.logger.Warn("Feed disconnected, reconnecting",
				zap.String("feed", name), zap.Error(err), zap.Duration("backoff", backoff))
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff *= 2
		if backoff > maxBackoff {
			backoff = maxBackoff
		}
	}
}
