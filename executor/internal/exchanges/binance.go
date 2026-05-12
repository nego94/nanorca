// internal/exchanges/binance.go — Binance exchange client (hot path)
// Handles REST + WebSocket for price feeds, order placement, and position monitoring.
// In paper mode: simulates orders with realistic slippage; uses testnet for data.
package exchanges

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"go.uber.org/zap"
)

// BinanceClient wraps Binance REST and WebSocket APIs.
type BinanceClient struct {
	logger    *zap.Logger
	paperMode bool
	baseURL   string
	wsURL     string
	apiKey    string
	apiSecret string
	httpClient *http.Client
}

// NewBinanceClient constructs a Binance client from environment variables.
func NewBinanceClient(logger *zap.Logger, paperMode bool) *BinanceClient {
	baseURL := "https://api.binance.com"
	wsURL   := "wss://stream.binance.com:9443"

	if paperMode || getEnv("BINANCE_TESTNET", "true") == "true" {
		baseURL = "https://testnet.binance.vision"
		wsURL   = "wss://testnet.binance.vision"
	}

	return &BinanceClient{
		logger:    logger.Named("binance"),
		paperMode: paperMode,
		baseURL:   baseURL,
		wsURL:     wsURL,
		apiKey:    getEnv("BINANCE_API_KEY", ""),
		apiSecret: getEnv("BINANCE_API_SECRET", ""),
		httpClient: &http.Client{
			Timeout: 10 * time.Second,
		},
	}
}

// Ping checks connectivity to Binance.
// Returns nil on success. Used by the health check.
func (b *BinanceClient) Ping(ctx context.Context) error {
	// TODO Phase 2: GET /api/v3/ping
	b.logger.Debug("Binance ping (stub)")
	return nil
}

// GetPrice returns the latest mid-price for a symbol (e.g. "BTCUSDT").
// Returns (price, error). Uses GET /api/v3/ticker/price.
func (b *BinanceClient) GetPrice(ctx context.Context, symbol string) (float64, error) {
	// TODO Phase 2: implement real REST call with retry + timeout
	b.logger.Debug("Binance GetPrice stub", zap.String("symbol", symbol))
	return 0, fmt.Errorf("not implemented (stub)")
}

// GetFundingRate returns the current funding rate for a perpetual contract.
// Only available for symbols on Binance Futures (e.g. "BTCUSDT").
func (b *BinanceClient) GetFundingRate(ctx context.Context, symbol string) (float64, error) {
	// TODO Phase 2: GET /fapi/v1/premiumIndex
	b.logger.Debug("Binance GetFundingRate stub", zap.String("symbol", symbol))
	return 0, fmt.Errorf("not implemented (stub)")
}

// PlaceOrder places a market order on Binance.
// In paper mode: simulates with 0.1% slippage, does not touch the exchange.
func (b *BinanceClient) PlaceOrder(ctx context.Context, symbol, side string, sizeUSD float64) (orderID string, filledPrice float64, fees float64, err error) {
	if b.paperMode {
		return b.simulateOrder(symbol, side, sizeUSD)
	}
	// TODO Phase 5: POST /api/v3/order with HMAC signature
	return "", 0, 0, fmt.Errorf("live order not implemented (stub)")
}

// CloseOrder closes (exits) an existing position at market price.
func (b *BinanceClient) CloseOrder(ctx context.Context, orderID, symbol, side string) (exitPrice, pnl, fees float64, err error) {
	if b.paperMode {
		return b.simulateClose(orderID, symbol)
	}
	// TODO Phase 5: POST /api/v3/order with opposite side
	return 0, 0, 0, fmt.Errorf("live close not implemented (stub)")
}

// simulateOrder generates a realistic paper trade result.
// Applies 0.1% slippage to simulate real market conditions.
func (b *BinanceClient) simulateOrder(symbol, side string, sizeUSD float64) (string, float64, float64, error) {
	slippage := 0.001 // 0.1%
	simulatedPrice := 50000.0 * (1 + slippage) // TODO: replace with real price lookup
	fees := sizeUSD * 0.001 // 0.1% taker fee
	orderID := fmt.Sprintf("PAPER_%s_%d", symbol, time.Now().UnixMilli())
	b.logger.Info("[PAPER] Binance simulated order",
		zap.String("order_id", orderID),
		zap.String("symbol", symbol),
		zap.String("side", side),
		zap.Float64("size_usd", sizeUSD),
		zap.Float64("simulated_price", simulatedPrice),
	)
	return orderID, simulatedPrice, fees, nil
}

func (b *BinanceClient) simulateClose(orderID, symbol string) (float64, float64, float64, error) {
	exitPrice := 51000.0 // TODO: replace with real price lookup
	pnl       := 10.0   // stub
	fees      := 0.5
	b.logger.Info("[PAPER] Binance simulated close",
		zap.String("order_id", orderID),
		zap.Float64("exit_price", exitPrice),
		zap.Float64("pnl", pnl),
	)
	return exitPrice, pnl, fees, nil
}

// SubscribeBookTicker subscribes to the WebSocket best bid/ask feed for a symbol.
// Sends updates to the provided channel. Blocks until ctx is cancelled.
func (b *BinanceClient) SubscribeBookTicker(ctx context.Context, symbol string, ch chan<- BookTick) error {
	// TODO Phase 2: wss://stream.binance.com:9443/ws/<symbol>@bookTicker
	b.logger.Info("Binance SubscribeBookTicker stub", zap.String("symbol", symbol))
	<-ctx.Done()
	return nil
}

// BookTick represents a single best bid/ask update from Binance WS.
type BookTick struct {
	Symbol    string
	BidPrice  float64
	AskPrice  float64
	Timestamp time.Time
}
