// internal/exchanges/polymarket.go — Polymarket CLOB client (Phase 2C)
// Read-only market data requires NO auth.
// Trading requires new Relayer API (POLYMARKET_RELAYER_API_KEY + POLYMARKET_RELAYER_KEY_ADDRESS).
package exchanges

import (
	"context"
	"fmt"
	"time"

	"go.uber.org/zap"
)

// PolymarketClient wraps the Polymarket CLOB REST API.
type PolymarketClient struct {
	logger         *zap.Logger
	paperMode      bool
	clobHost       string
	relayerHost    string
	relayerAPIKey  string
	relayerKeyAddr string
	privateKey     string
}

// NewPolymarketClient constructs a Polymarket client from env.
func NewPolymarketClient(logger *zap.Logger, paperMode bool) *PolymarketClient {
	return &PolymarketClient{
		logger:         logger.Named("polymarket"),
		paperMode:      paperMode,
		clobHost:       "https://clob.polymarket.com",
		relayerHost:    getEnv("POLYMARKET_HOST", "https://relayer-v2.polymarket.com"),
		relayerAPIKey:  getEnv("POLYMARKET_RELAYER_API_KEY", ""),
		relayerKeyAddr: getEnv("POLYMARKET_RELAYER_KEY_ADDRESS", ""),
		privateKey:     getEnv("POLYMARKET_PRIVATE_KEY", ""),
	}
}

// PolymarketMarket is a single prediction market snapshot.
type PolymarketMarket struct {
	ConditionID string
	Question    string
	YesPrice    float64 // 0.0–1.0 implied probability
	NoPrice     float64
	Volume24h   float64
	Active      bool
}

// Ping checks CLOB connectivity.
func (p *PolymarketClient) Ping(ctx context.Context) error {
	_, err := httpGet(ctx, newHTTPClient(), p.clobHost+"/markets?limit=1", nil, p.logger)
	return err
}

// GetTopMarkets returns the highest-volume active prediction markets.
// No auth required — public CLOB endpoint.
func (p *PolymarketClient) GetTopMarkets(ctx context.Context, limit int) ([]PolymarketMarket, error) {
	url := fmt.Sprintf("%s/markets?active=true&closed=false&limit=%d&order=volume&ascending=false", p.clobHost, limit)
	body, err := httpGet(ctx, newHTTPClient(), url, nil, p.logger)
	if err != nil {
		return nil, fmt.Errorf("polymarket markets: %w", err)
	}

	var resp struct {
		Data []struct {
			ConditionID string  `json:"condition_id"`
			Question    string  `json:"question"`
			Active      bool    `json:"active"`
			Volume      float64 `json:"volume"`
			Tokens      []struct {
				Outcome string  `json:"outcome"`
				Price   float64 `json:"price"`
			} `json:"tokens"`
		} `json:"data"`
	}
	if err := unmarshal(body, &resp); err != nil {
		return nil, err
	}

	markets := make([]PolymarketMarket, 0, len(resp.Data))
	for _, m := range resp.Data {
		pm := PolymarketMarket{
			ConditionID: m.ConditionID,
			Question:    m.Question,
			Active:      m.Active,
			Volume24h:   m.Volume,
		}
		for _, t := range m.Tokens {
			if t.Outcome == "Yes" {
				pm.YesPrice = t.Price
			} else if t.Outcome == "No" {
				pm.NoPrice = t.Price
			}
		}
		markets = append(markets, pm)
	}
	p.logger.Debug("Polymarket markets fetched", zap.Int("count", len(markets)))
	return markets, nil
}

// PlaceOrder places a YES/NO order. Paper mode simulates at real market price.
func (p *PolymarketClient) PlaceOrder(ctx context.Context, conditionID, side string, sizeUSD float64) (string, float64, float64, error) {
	if p.paperMode {
		// Fetch the actual current price for this market
		m, err := p.getMarketPrice(ctx, conditionID)
		price := 0.52
		if err == nil && m > 0 {
			price = m
		}
		if side == "NO" {
			price = 1 - price
		}
		price *= 1.0005 // 0.05% slippage
		id := fmt.Sprintf("PAPER_POLY_%s_%d", conditionID[:min(8, len(conditionID))], time.Now().UnixMilli())
		p.logger.Info("[PAPER] Polymarket order", zap.String("id", id),
			zap.String("side", side), zap.Float64("price", price))
		return id, price, sizeUSD * 0.002, nil
	}
	return "", 0, 0, fmt.Errorf("live Polymarket orders not enabled — Phase 5")
}

// CloseOrder closes a Polymarket position.
func (p *PolymarketClient) CloseOrder(ctx context.Context, orderID, conditionID, side string) (float64, float64, float64, error) {
	if p.paperMode {
		price, _ := p.getMarketPrice(ctx, conditionID)
		if price == 0 {
			price = 0.65
		}
		return price, 0, 0.1, nil
	}
	return 0, 0, 0, fmt.Errorf("live Polymarket close not enabled")
}

// getMarketPrice fetches the YES price for a condition ID.
func (p *PolymarketClient) getMarketPrice(ctx context.Context, conditionID string) (float64, error) {
	body, err := httpGet(ctx, newHTTPClient(),
		p.clobHost+"/markets/"+conditionID, nil, p.logger)
	if err != nil {
		return 0, err
	}
	var resp struct {
		Tokens []struct {
			Outcome string  `json:"outcome"`
			Price   float64 `json:"price"`
		} `json:"tokens"`
	}
	if err := unmarshal(body, &resp); err != nil {
		return 0, err
	}
	for _, t := range resp.Tokens {
		if t.Outcome == "Yes" {
			return t.Price, nil
		}
	}
	return 0, nil
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
