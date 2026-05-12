// internal/exchanges/polymarket.go — Hyperliquid exchange client (hot path)
// Hyperliquid is a decentralized perpetual DEX. Uses both REST info API and WebSocket.
// In paper mode: simulates fills. In live mode: signs orders with private key.
package exchanges

import (
	"context"
	"fmt"
	"time"

	"go.uber.org/zap"
)

// HyperliquidClient wraps the Hyperliquid REST + WebSocket APIs.
type HyperliquidClient struct {
	logger         *zap.Logger
	paperMode      bool
	restURL        string
	wsURL          string
	privateKey     string
	accountAddress string
}

// NewHyperliquidClient constructs a Hyperliquid client from env.
func NewHyperliquidClient(logger *zap.Logger, paperMode bool) *HyperliquidClient {
	restURL := "https://api.hyperliquid.xyz"
	wsURL   := "wss://api.hyperliquid.xyz/ws"

	if getEnv("HYPERLIQUID_TESTNET", "true") == "true" {
		restURL = "https://api.hyperliquid-testnet.xyz"
		wsURL   = "wss://api.hyperliquid-testnet.xyz/ws"
	}

	return &HyperliquidClient{
		logger:         logger.Named("hyperliquid"),
		paperMode:      paperMode,
		restURL:        restURL,
		wsURL:          wsURL,
		privateKey:     getEnv("HYPERLIQUID_PRIVATE_KEY", ""),
		accountAddress: getEnv("HYPERLIQUID_ACCOUNT_ADDRESS", ""),
	}
}

// HLFundingRate holds funding data for one perpetual.
type HLFundingRate struct {
	Coin        string
	FundingRate float64 // 8-hour rate (positive = longs pay shorts)
	MarkPrice   float64
	IndexPrice  float64
}

// GetFundingRates returns current funding rates for all or specified coins.
func (h *HyperliquidClient) GetFundingRates(ctx context.Context) ([]HLFundingRate, error) {
	// TODO Phase 2: POST /info {"type": "metaAndAssetCtxs"}
	h.logger.Debug("Hyperliquid GetFundingRates stub")
	return nil, fmt.Errorf("not implemented (stub)")
}

// GetPrice returns the current mark price for a coin.
func (h *HyperliquidClient) GetPrice(ctx context.Context, coin string) (float64, error) {
	// TODO Phase 2: POST /info {"type": "allMids"}
	h.logger.Debug("Hyperliquid GetPrice stub", zap.String("coin", coin))
	return 0, fmt.Errorf("not implemented (stub)")
}

// PlaceOrder places a market order on Hyperliquid perpetuals.
// side: "long" | "short". sizeUSD is the notional value.
// In paper mode: simulates. In live mode: signs with private key (EIP-712).
func (h *HyperliquidClient) PlaceOrder(ctx context.Context, coin, side string, sizeUSD float64) (orderID string, filledPrice float64, fees float64, err error) {
	if h.paperMode {
		return h.simulateOrder(coin, side, sizeUSD)
	}
	// TODO Phase 5: POST /exchange — signed EIP-712 order
	return "", 0, 0, fmt.Errorf("live order not implemented (stub)")
}

// CloseOrder closes (market exit) an open Hyperliquid position.
func (h *HyperliquidClient) CloseOrder(ctx context.Context, orderID, coin string) (exitPrice, pnl, fees float64, err error) {
	if h.paperMode {
		return h.simulateClose(orderID, coin)
	}
	// TODO Phase 5: POST /exchange — reduce-only order
	return 0, 0, 0, fmt.Errorf("live close not implemented (stub)")
}

func (h *HyperliquidClient) simulateOrder(coin, side string, sizeUSD float64) (string, float64, float64, error) {
	simulatedPrice := 50000.0
	slippage       := 0.001 // 0.1% for HL perps
	if side == "short" {
		simulatedPrice *= (1 - slippage)
	} else {
		simulatedPrice *= (1 + slippage)
	}
	fees    := sizeUSD * 0.00035 // HL maker fee is ~0.035%
	orderID := fmt.Sprintf("PAPER_HL_%s_%d", coin, time.Now().UnixMilli())
	h.logger.Info("[PAPER] Hyperliquid simulated order",
		zap.String("order_id", orderID),
		zap.String("coin", coin),
		zap.String("side", side),
		zap.Float64("size_usd", sizeUSD),
		zap.Float64("simulated_price", simulatedPrice),
	)
	return orderID, simulatedPrice, fees, nil
}

func (h *HyperliquidClient) simulateClose(orderID, coin string) (float64, float64, float64, error) {
	exitPrice := 51500.0
	pnl       := 15.0
	fees      := 0.2
	h.logger.Info("[PAPER] Hyperliquid simulated close",
		zap.String("order_id", orderID),
		zap.Float64("exit_price", exitPrice),
		zap.Float64("pnl", pnl),
	)
	return exitPrice, pnl, fees, nil
}

// Ping checks connectivity to Hyperliquid.
func (h *HyperliquidClient) Ping(ctx context.Context) error {
	// TODO Phase 2: POST /info {"type": "meta"}
	h.logger.Debug("Hyperliquid ping (stub)")
	return nil
}

// SubscribeAllMids subscribes to the WebSocket all-mids (mark prices) feed.
// Sends price maps to ch. Blocks until ctx is cancelled.
func (h *HyperliquidClient) SubscribeAllMids(ctx context.Context, ch chan<- map[string]float64) error {
	// TODO Phase 2: WSS {"method": "subscribe", "subscription": {"type": "allMids"}}
	h.logger.Info("Hyperliquid SubscribeAllMids stub")
	<-ctx.Done()
	return nil
}
