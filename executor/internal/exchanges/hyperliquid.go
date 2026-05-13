// internal/exchanges/hyperliquid.go — Hyperliquid perpetual DEX client (Phase 2B)
// Uses simple REST POST /info endpoint — no API key needed for read-only data.
// Order execution requires EIP-712 wallet signature (Phase 5).
package exchanges

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"go.uber.org/zap"
)

// HyperliquidClient wraps the Hyperliquid REST + WebSocket APIs.
type HyperliquidClient struct {
	logger         *zap.Logger
	paperMode      bool
	restURL        string
	privateKey     string
	accountAddress string
	lastPrices     map[string]float64
}

// NewHyperliquidClient constructs a Hyperliquid client from env.
func NewHyperliquidClient(logger *zap.Logger, paperMode bool) *HyperliquidClient {
	restURL := "https://api.hyperliquid.xyz"
	if getEnv("HYPERLIQUID_TESTNET", "true") == "true" {
		restURL = "https://api.hyperliquid-testnet.xyz"
	}
	return &HyperliquidClient{
		logger:         logger.Named("hyperliquid"),
		paperMode:      paperMode,
		restURL:        restURL,
		privateKey:     getEnv("HYPERLIQUID_PRIVATE_KEY", ""),
		accountAddress: getEnv("HYPERLIQUID_ACCOUNT_ADDRESS", ""),
		lastPrices:     make(map[string]float64),
	}
}

// HLFundingRate holds funding data for one perpetual contract.
type HLFundingRate struct {
	Coin        string
	FundingRate float64 // 8-hour rate (positive = longs pay shorts)
	MarkPrice   float64
	IndexPrice  float64
}

// hlInfo sends a POST /info request (Hyperliquid's unified read endpoint).
func (h *HyperliquidClient) hlInfo(ctx context.Context, payload any) ([]byte, error) {
	b, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	return httpPost(ctx, newHTTPClient(), h.restURL+"/info", b, map[string]string{})
}

// Ping checks connectivity to Hyperliquid.
func (h *HyperliquidClient) Ping(ctx context.Context) error {
	_, err := h.hlInfo(ctx, map[string]string{"type": "meta"})
	return err
}

// GetFundingRates returns current funding rates and mark prices for all perps.
// No auth required — public endpoint.
func (h *HyperliquidClient) GetFundingRates(ctx context.Context) ([]HLFundingRate, error) {
	body, err := h.hlInfo(ctx, map[string]string{"type": "metaAndAssetCtxs"})
	if err != nil {
		return nil, fmt.Errorf("hyperliquid funding rates: %w", err)
	}

	// Response is [meta, assetCtxs] array
	var raw []json.RawMessage
	if err := json.Unmarshal(body, &raw); err != nil || len(raw) < 2 {
		return nil, fmt.Errorf("hyperliquid unexpected response shape")
	}

	// Parse meta (coin names)
	var meta struct {
		Universe []struct {
			Name string `json:"name"`
		} `json:"universe"`
	}
	if err := json.Unmarshal(raw[0], &meta); err != nil {
		return nil, err
	}

	// Parse asset contexts (funding rate, mark price, etc.)
	var ctxs []struct {
		FundingRate float64 `json:"funding,string"`
		MarkPx      float64 `json:"markPx,string"`
		MidPx       float64 `json:"midPx,string"`
	}
	if err := json.Unmarshal(raw[1], &ctxs); err != nil {
		return nil, err
	}

	rates := make([]HLFundingRate, 0, len(meta.Universe))
	for i, coin := range meta.Universe {
		if i >= len(ctxs) {
			break
		}
		h.lastPrices[coin.Name] = ctxs[i].MarkPx
		rates = append(rates, HLFundingRate{
			Coin:        coin.Name,
			FundingRate: ctxs[i].FundingRate,
			MarkPrice:   ctxs[i].MarkPx,
			IndexPrice:  ctxs[i].MidPx,
		})
	}
	h.logger.Debug("Hyperliquid funding rates", zap.Int("count", len(rates)))
	return rates, nil
}

// GetAccountBalance fetches real USD balance from Hyperliquid.
// Requires HYPERLIQUID_ACCOUNT_ADDRESS.
func (h *HyperliquidClient) GetAccountBalance(ctx context.Context) (*ExchangeBalance, error) {
	if h.accountAddress == "" {
		return &ExchangeBalance{Exchange: "hyperliquid", Error: "HYPERLIQUID_ACCOUNT_ADDRESS not set"}, nil
	}
	body, err := h.hlInfo(ctx, map[string]any{
		"type": "clearinghouseState",
		"user": h.accountAddress,
	})
	if err != nil {
		return &ExchangeBalance{Exchange: "hyperliquid", Error: err.Error()}, err
	}

	var state struct {
		MarginSummary struct {
			AccountValue float64 `json:"accountValue,string"`
		} `json:"marginSummary"`
	}
	if err := json.Unmarshal(body, &state); err != nil {
		return &ExchangeBalance{Exchange: "hyperliquid", Error: err.Error()}, err
	}

	usd := state.MarginSummary.AccountValue
	h.logger.Info("Hyperliquid balance", zap.Float64("usd", usd))
	return &ExchangeBalance{
		Exchange:  "hyperliquid",
		USDT:      usd,
		TotalUSD:  usd,
		UpdatedAt: time.Now(),
	}, nil
}

// PlaceOrder routes to paper simulation or live (not yet enabled).
func (h *HyperliquidClient) PlaceOrder(ctx context.Context, coin, side string, sizeUSD float64) (string, float64, float64, error) {
	if h.paperMode {
		return h.simOrder(ctx, coin, side, sizeUSD)
	}
	return "", 0, 0, fmt.Errorf("live HL orders not enabled — keep PAPER_TRADING=true")
}

// CloseOrder routes to paper simulation or live (not yet enabled).
func (h *HyperliquidClient) CloseOrder(ctx context.Context, orderID, coin string) (float64, float64, float64, error) {
	if h.paperMode {
		return h.simClose(ctx, coin)
	}
	return 0, 0, 0, fmt.Errorf("live HL close not enabled")
}

func (h *HyperliquidClient) simOrder(ctx context.Context, coin, side string, sizeUSD float64) (string, float64, float64, error) {
	// Use real funding rate data to get current mark price
	rates, err := h.GetFundingRates(ctx)
	var price float64
	if err == nil {
		for _, r := range rates {
			if r.Coin == coin {
				price = r.MarkPrice
				break
			}
		}
	}
	if price == 0 {
		price = h.lastPrices[coin]
	}
	if price == 0 {
		price = 50000 // last resort fallback
	}
	slip := 0.001
	if side == "short" {
		price *= 1 - slip
	} else {
		price *= 1 + slip
	}
	id := fmt.Sprintf("PAPER_HL_%s_%d", coin, time.Now().UnixMilli())
	h.logger.Info("[PAPER] HL order", zap.String("id", id), zap.String("coin", coin),
		zap.String("side", side), zap.Float64("price", price))
	return id, price, sizeUSD * 0.00035, nil
}

func (h *HyperliquidClient) simClose(ctx context.Context, coin string) (float64, float64, float64, error) {
	price := h.lastPrices[coin]
	if price == 0 {
		rates, _ := h.GetFundingRates(ctx)
		for _, r := range rates {
			if r.Coin == coin {
				price = r.MarkPrice
				break
			}
		}
	}
	return price, 0, 0.2, nil
}
