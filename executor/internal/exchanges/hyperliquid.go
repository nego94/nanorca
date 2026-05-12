// internal/exchanges/polymarket.go — Polymarket CLOB API client
// Polymarket uses a Polygon-based wallet for auth and CLOB REST for orders.
// In paper mode: simulates fills with 0.05% slippage (tighter than crypto).
package exchanges

import (
	"context"
	"fmt"
	"time"

	"go.uber.org/zap"
)

// PolymarketClient wraps the Polymarket CLOB REST API.
type PolymarketClient struct {
	logger      *zap.Logger
	paperMode   bool
	host        string
	apiKey      string
	apiSecret   string
	passphrase  string
	privateKey  string // Polygon wallet private key
}

// NewPolymarketClient constructs a Polymarket client from env.
func NewPolymarketClient(logger *zap.Logger, paperMode bool) *PolymarketClient {
	return &PolymarketClient{
		logger:     logger.Named("polymarket"),
		paperMode:  paperMode,
		host:       getEnv("POLYMARKET_HOST", "https://clob.polymarket.com"),
		apiKey:     getEnv("POLYMARKET_API_KEY", ""),
		apiSecret:  getEnv("POLYMARKET_API_SECRET", ""),
		passphrase: getEnv("POLYMARKET_API_PASSPHRASE", ""),
		privateKey: getEnv("POLYMARKET_PRIVATE_KEY", ""),
	}
}

// PolymarketMarket represents a single prediction market.
type PolymarketMarket struct {
	ConditionID string
	Question    string
	YesPrice    float64 // 0.0–1.0 (implied probability)
	NoPrice     float64
	Volume24h   float64
	Active      bool
}

// GetTopMarkets returns the highest-volume active markets.
// Sorted by volume descending. Used by the market scanner.
func (p *PolymarketClient) GetTopMarkets(ctx context.Context, limit int) ([]PolymarketMarket, error) {
	// TODO Phase 2: GET /markets?active=true&order=volume&limit=<limit>
	p.logger.Debug("Polymarket GetTopMarkets stub", zap.Int("limit", limit))
	return nil, fmt.Errorf("not implemented (stub)")
}

// GetMarket returns a single market by condition ID.
func (p *PolymarketClient) GetMarket(ctx context.Context, conditionID string) (*PolymarketMarket, error) {
	// TODO Phase 2: GET /markets/<conditionID>
	p.logger.Debug("Polymarket GetMarket stub", zap.String("condition_id", conditionID))
	return nil, fmt.Errorf("not implemented (stub)")
}

// PlaceOrder places a limit order (YES or NO) on Polymarket.
// In paper mode: simulates the fill immediately at current price + 0.05% slippage.
// sizeUSD is the notional position size.
func (p *PolymarketClient) PlaceOrder(ctx context.Context, conditionID, side string, sizeUSD float64) (orderID string, filledPrice float64, fees float64, err error) {
	if p.paperMode {
		return p.simulateOrder(conditionID, side, sizeUSD)
	}
	// TODO Phase 5: POST /order with L1 auth header (HMAC + wallet signature)
	return "", 0, 0, fmt.Errorf("live order not implemented (stub)")
}

// CloseOrder sells out of a Polymarket position.
func (p *PolymarketClient) CloseOrder(ctx context.Context, orderID, conditionID, side string) (exitPrice, pnl, fees float64, err error) {
	if p.paperMode {
		return p.simulateClose(orderID, conditionID)
	}
	// TODO Phase 5: POST /order with opposing side
	return 0, 0, 0, fmt.Errorf("live close not implemented (stub)")
}

func (p *PolymarketClient) simulateOrder(conditionID, side string, sizeUSD float64) (string, float64, float64, error) {
	simulatedPrice := 0.52 // stub — YES price
	if side == "NO" {
		simulatedPrice = 0.48
	}
	simulatedPrice *= 1.0005 // 0.05% slippage
	fees := sizeUSD * 0.002  // Polymarket charges ~0.2% taker
	orderID := fmt.Sprintf("PAPER_POLY_%s_%d", conditionID[:8], time.Now().UnixMilli())
	p.logger.Info("[PAPER] Polymarket simulated order",
		zap.String("order_id", orderID),
		zap.String("condition_id", conditionID),
		zap.String("side", side),
		zap.Float64("size_usd", sizeUSD),
		zap.Float64("simulated_price", simulatedPrice),
	)
	return orderID, simulatedPrice, fees, nil
}

func (p *PolymarketClient) simulateClose(orderID, conditionID string) (float64, float64, float64, error) {
	exitPrice := 0.65 // stub — resolution-like price
	pnl       := 5.0
	fees      := 0.1
	p.logger.Info("[PAPER] Polymarket simulated close",
		zap.String("order_id", orderID),
		zap.Float64("exit_price", exitPrice),
		zap.Float64("pnl", pnl),
	)
	return exitPrice, pnl, fees, nil
}

// Ping checks connectivity to Polymarket CLOB.
func (p *PolymarketClient) Ping(ctx context.Context) error {
	// TODO Phase 2: GET /
	p.logger.Debug("Polymarket ping (stub)")
	return nil
}
