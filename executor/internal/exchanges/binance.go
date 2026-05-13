// internal/exchanges/binance.go — Real Binance REST + WebSocket client
// Phase 2A: live prices, real balance, paper simulation at real prices.
package exchanges

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/url"
	"strconv"
	"time"

	"github.com/gorilla/websocket"
	"go.uber.org/zap"
)

// BinanceClient wraps Binance REST and WebSocket APIs.
type BinanceClient struct {
	logger     *zap.Logger
	paperMode  bool
	baseURL    string
	futuresURL string
	wsURL      string
	apiKey     string
	apiSecret  string
	prices     map[string]float64 // last-seen price cache for paper simulation
}


// NewBinanceClient builds a Binance client from env vars.
func NewBinanceClient(logger *zap.Logger, paperMode bool) *BinanceClient {
	baseURL    := "https://api.binance.com"
	futuresURL := "https://fapi.binance.com"
	wsURL      := "wss://stream.binance.com:9443"
	if getEnv("BINANCE_TESTNET", "false") == "true" {
		baseURL    = "https://testnet.binance.vision"
		futuresURL = "https://testnet.binancefuture.com"
		wsURL      = "wss://testnet.binance.vision/ws"
	}
	return &BinanceClient{
		logger:     logger.Named("binance"),
		paperMode:  paperMode,
		baseURL:    baseURL,
		futuresURL: futuresURL,
		wsURL:      wsURL,
		apiKey:     getEnv("BINANCE_API_KEY", ""),
		apiSecret:  getEnv("BINANCE_API_SECRET", ""),
		prices:     make(map[string]float64),
	}
}

// Ping checks Binance REST connectivity.
func (b *BinanceClient) Ping(ctx context.Context) error {
	_, err := httpGet(ctx, newHTTPClient(), b.baseURL+"/api/v3/ping", nil, b.logger)
	if err != nil {
		return fmt.Errorf("binance ping: %w", err)
	}
	return nil
}

// GetPrice returns the latest price for a symbol (e.g. "BTCUSDT").
func (b *BinanceClient) GetPrice(ctx context.Context, symbol string) (float64, error) {
	body, err := httpGet(ctx, newHTTPClient(),
		b.baseURL+"/api/v3/ticker/price?symbol="+symbol, nil, b.logger)
	if err != nil {
		return 0, err
	}
	var r struct {
		Price float64 `json:"price,string"`
	}
	if err := unmarshal(body, &r); err != nil {
		return 0, err
	}
	b.prices[symbol] = r.Price
	b.logger.Debug("Binance price", zap.String("sym", symbol), zap.Float64("price", r.Price))
	return r.Price, nil
}

// GetBookTicker returns the best bid/ask for a symbol.
func (b *BinanceClient) GetBookTicker(ctx context.Context, symbol string) (bid, ask float64, err error) {
	body, err := httpGet(ctx, newHTTPClient(),
		b.baseURL+"/api/v3/ticker/bookTicker?symbol="+symbol, nil, b.logger)
	if err != nil {
		return 0, 0, err
	}
	var r struct {
		Bid float64 `json:"bidPrice,string"`
		Ask float64 `json:"askPrice,string"`
	}
	if err := unmarshal(body, &r); err != nil {
		return 0, 0, err
	}
	return r.Bid, r.Ask, nil
}

// GetVolume24h returns the 24h USDT volume for a symbol.
func (b *BinanceClient) GetVolume24h(ctx context.Context, symbol string) (float64, error) {
	body, err := httpGet(ctx, newHTTPClient(),
		b.baseURL+"/api/v3/ticker/24hr?symbol="+symbol, nil, b.logger)
	if err != nil {
		return 0, err
	}
	var r struct {
		QuoteVol float64 `json:"quoteVolume,string"`
	}
	if err := unmarshal(body, &r); err != nil {
		return 0, err
	}
	return r.QuoteVol, nil
}

// GetFundingRate returns the current funding rate for a perp contract.
// Returns 0 without error for spot symbols that don't have funding rates.
func (b *BinanceClient) GetFundingRate(ctx context.Context, symbol string) (float64, error) {
	body, err := httpGet(ctx, newHTTPClient(),
		b.futuresURL+"/fapi/v1/premiumIndex?symbol="+symbol, nil, b.logger)
	if err != nil {
		return 0, nil // non-fatal for spot symbols
	}
	var r struct {
		Rate float64 `json:"lastFundingRate,string"`
	}
	if err := unmarshal(body, &r); err != nil {
		return 0, nil
	}
	return r.Rate, nil
}

// GetAccountBalance fetches the real USDT spot balance. Requires API key.
func (b *BinanceClient) GetAccountBalance(ctx context.Context) (*ExchangeBalance, error) {
	if b.apiKey == "" {
		return &ExchangeBalance{Exchange: "binance", Error: "BINANCE_API_KEY not set"}, nil
	}
	params := url.Values{}
	params.Set("timestamp", strconv.FormatInt(time.Now().UnixMilli(), 10))
	params.Set("recvWindow", "5000")
	params.Set("signature", b.sign(params.Encode()))

	body, err := httpGet(ctx, newHTTPClient(),
		b.baseURL+"/api/v3/account?"+params.Encode(),
		map[string]string{"X-MBX-APIKEY": b.apiKey},
		b.logger)
	if err != nil {
		return &ExchangeBalance{Exchange: "binance", Error: err.Error()}, err
	}

	var account struct {
		Balances []struct {
			Asset string  `json:"asset"`
			Free  float64 `json:"free,string"`
		} `json:"balances"`
	}
	if err := unmarshal(body, &account); err != nil {
		return &ExchangeBalance{Exchange: "binance", Error: err.Error()}, err
	}

	var usdt float64
	for _, bal := range account.Balances {
		if bal.Asset == "USDT" {
			usdt = bal.Free
			break
		}
	}
	b.logger.Info("Binance balance", zap.Float64("usdt", usdt))
	return &ExchangeBalance{
		Exchange:  "binance",
		USDT:      usdt,
		TotalUSD:  usdt,
		UpdatedAt: time.Now(),
	}, nil
}

// PlaceOrder routes to paper simulation or returns error (live not yet enabled).
func (b *BinanceClient) PlaceOrder(ctx context.Context, symbol, side string, sizeUSD float64) (orderID string, filledPrice float64, fees float64, err error) {
	if b.paperMode {
		return b.simOrder(ctx, symbol, side, sizeUSD)
	}
	return "", 0, 0, fmt.Errorf("live orders blocked: set PAPER_TRADING=false only after 14 profitable paper days")
}

// CloseOrder routes to paper simulation or returns error.
func (b *BinanceClient) CloseOrder(ctx context.Context, orderID, symbol, side string) (exitPrice, pnl, fees float64, err error) {
	if b.paperMode {
		return b.simClose(ctx, symbol)
	}
	return 0, 0, 0, fmt.Errorf("live close blocked: PAPER_TRADING must be false")
}

func (b *BinanceClient) simOrder(ctx context.Context, symbol, side string, sizeUSD float64) (string, float64, float64, error) {
	price, _ := b.GetPrice(ctx, symbol)
	if price == 0 {
		price = b.prices[symbol]
	}
	if price == 0 {
		return "", 0, 0, fmt.Errorf("no price for %s", symbol)
	}
	if side == "sell" || side == "short" {
		price *= 0.999
	} else {
		price *= 1.001
	}
	id := fmt.Sprintf("PAPER_BN_%s_%d", symbol, time.Now().UnixMilli())
	b.logger.Info("[PAPER] order", zap.String("id", id), zap.String("side", side),
		zap.Float64("usd", sizeUSD), zap.Float64("price", price))
	return id, price, sizeUSD * 0.001, nil
}

func (b *BinanceClient) simClose(ctx context.Context, symbol string) (float64, float64, float64, error) {
	price, _ := b.GetPrice(ctx, symbol)
	if price == 0 {
		price = b.prices[symbol]
	}
	return price, 0, 0.5, nil
}

// BookTick is a real-time best bid/ask snapshot from the Binance WS stream.
type BookTick struct {
	Symbol    string
	BidPrice  float64
	AskPrice  float64
	Timestamp time.Time
}

// SubscribeBookTicker streams bid/ask ticks. Reconnects automatically on drop.
func (b *BinanceClient) SubscribeBookTicker(ctx context.Context, symbol string, ch chan<- BookTick) error {
	endpoint := fmt.Sprintf("%s/ws/%s@bookTicker", b.wsURL, symbol)
	for {
		if err := b.runWS(ctx, endpoint, symbol, ch); err != nil {
			b.logger.Warn("WS reconnecting", zap.String("sym", symbol), zap.Error(err))
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(5 * time.Second):
		}
	}
}

func (b *BinanceClient) runWS(ctx context.Context, endpoint, symbol string, ch chan<- BookTick) error {
	conn, _, err := websocket.DefaultDialer.DialContext(ctx, endpoint, nil)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()
	b.logger.Info("Binance WS connected", zap.String("sym", symbol))
	done := make(chan error, 1)
	go func() {
		for {
			var msg struct {
				B float64 `json:"b,string"`
				A float64 `json:"a,string"`
			}
			if err := conn.ReadJSON(&msg); err != nil {
				done <- err
				return
			}
			select {
			case ch <- BookTick{Symbol: symbol, BidPrice: msg.B, AskPrice: msg.A, Timestamp: time.Now()}:
			default:
			}
		}
	}()
	select {
	case <-ctx.Done():
		return nil
	case err := <-done:
		return err
	}
}

func (b *BinanceClient) sign(payload string) string {
	mac := hmac.New(sha256.New, []byte(b.apiSecret))
	mac.Write([]byte(payload))
	return hex.EncodeToString(mac.Sum(nil))
}
