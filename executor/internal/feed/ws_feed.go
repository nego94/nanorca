// internal/feed/ws_feed.go — WebSocket feed manager (hot path)
// Manages persistent WebSocket connections to Binance and Hyperliquid.
// Reconnects automatically on disconnection with exponential backoff.
// Phase 2A: ticks are cached in PriceCache for momentum signal computation.
package feed

import (
	"context"
	"sync"
	"time"

	"go.uber.org/zap"
	"github.com/nanorca/executor/internal/exchanges"
)

// pricePoint is a single timestamped price sample.
type pricePoint struct {
	price float64
	at    time.Time
}

// PriceCache stores a rolling window of mid-prices per symbol.
// Thread-safe. Used by MarketScanner to compute momentum without an extra REST call.
type PriceCache struct {
	mu     sync.RWMutex
	data   map[string][]pricePoint
	maxAge time.Duration
}

// NewPriceCache creates a cache that retains up to maxAge of price history.
func NewPriceCache(maxAge time.Duration) *PriceCache {
	return &PriceCache{
		data:   make(map[string][]pricePoint),
		maxAge: maxAge,
	}
}

// Record adds a price sample for symbol and evicts entries older than maxAge.
func (c *PriceCache) Record(symbol string, price float64) {
	if price <= 0 {
		return
	}
	now := time.Now()
	cutoff := now.Add(-c.maxAge)

	c.mu.Lock()
	defer c.mu.Unlock()

	pts := append(c.data[symbol], pricePoint{price: price, at: now})
	// evict old entries in one pass from the front
	start := 0
	for start < len(pts) && pts[start].at.Before(cutoff) {
		start++
	}
	c.data[symbol] = pts[start:]
}

// Momentum returns the percentage price change over window duration.
// Positive = upward move, negative = downward. Returns 0 if insufficient data.
func (c *PriceCache) Momentum(symbol string, window time.Duration) float64 {
	c.mu.RLock()
	defer c.mu.RUnlock()

	pts := c.data[symbol]
	if len(pts) < 2 {
		return 0
	}
	// find the oldest point within the requested window
	cutoff := time.Now().Add(-window)
	oldest := pts[0]
	for _, p := range pts {
		if p.at.After(cutoff) {
			oldest = p
			break
		}
	}
	newest := pts[len(pts)-1]
	if oldest.price == 0 {
		return 0
	}
	return (newest.price - oldest.price) / oldest.price * 100
}

// Len returns how many samples are cached for a symbol (useful for debug).
func (c *PriceCache) Len(symbol string) int {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return len(c.data[symbol])
}

// WSFeedManager manages all WebSocket subscriptions.
type WSFeedManager struct {
	logger  *zap.Logger
	binance *exchanges.BinanceClient
	hl      *exchanges.HyperliquidClient
	// Cache is exported so MarketScanner can read momentum without extra REST calls.
	Cache *PriceCache
}

// NewWSFeedManager creates the feed manager with a 10-minute price history window.
func NewWSFeedManager(logger *zap.Logger, binance *exchanges.BinanceClient, hl *exchanges.HyperliquidClient) *WSFeedManager {
	return &WSFeedManager{
		logger:  logger.Named("ws_feed"),
		binance: binance,
		hl:      hl,
		Cache:   NewPriceCache(10 * time.Minute),
	}
}

// Run starts all WebSocket subscriptions. Blocks until ctx is cancelled.
func (m *WSFeedManager) Run(ctx context.Context) {
	m.logger.Info("WebSocket feed manager starting")

	go m.runWithReconnect(ctx, "binance_book_ticker", func(ctx context.Context) error {
		ch := make(chan exchanges.BookTick, 100)
		go func() {
			for tick := range ch {
				mid := (tick.BidPrice + tick.AskPrice) / 2
				m.Cache.Record(tick.Symbol, mid)
			}
		}()
		return m.binance.SubscribeBookTicker(ctx, "BTCUSDT", ch)
	})

	go m.runWithReconnect(ctx, "hyperliquid_funding_poll", func(ctx context.Context) error {
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
				for _, r := range rates {
					m.Cache.Record("HL:"+r.Coin, r.MarkPrice)
				}
				m.logger.Debug("Hyperliquid funding rates polled", zap.Int("count", len(rates)))
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
