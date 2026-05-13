// internal/exchanges/binance.go — Real Binance REST + WebSocket client
// Phase 2A: live prices, real balance, paper simulation at real prices.
package exchanges

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"net/url"
	"sort"
	"strconv"
	"strings"
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

// GetAccountBalance fetches real USDT balance across Spot and Funding wallets.
// Requires API key with "Read" permission.
func (b *BinanceClient) GetAccountBalance(ctx context.Context) (*ExchangeBalance, error) {
	if b.apiKey == "" {
		return &ExchangeBalance{Exchange: "binance", Error: "BINANCE_API_KEY not set"}, nil
	}

	spotUSDT := b.spotBalanceUSDT(ctx)
	allWalletsUSD := b.allWalletsUSD(ctx)
	// Use the larger of the two: stablecoin spot balance vs BTC-converted total.
	// The BTC-converted total covers all wallet types (Funding, Earn, etc.)
	// but is an approximation. The spot stablecoin balance is exact for USDT/USDC.
	total := allWalletsUSD
	if spotUSDT > total {
		total = spotUSDT
	}

	b.logger.Info("Binance balance",
		zap.Float64("spot_stable_usdt", spotUSDT),
		zap.Float64("all_wallets_usd", allWalletsUSD),
		zap.Float64("total_usd", total),
	)
	return &ExchangeBalance{
		Exchange:  "binance",
		USDT:      total,
		TotalUSD:  total,
		UpdatedAt: time.Now(),
	}, nil
}

// spotBalanceUSDT returns free USDT/USDC/BUSD from the Spot wallet.
func (b *BinanceClient) spotBalanceUSDT(ctx context.Context) float64 {
	params := url.Values{}
	params.Set("timestamp", strconv.FormatInt(time.Now().UnixMilli(), 10))
	params.Set("recvWindow", "5000")
	params.Set("signature", b.sign(params.Encode()))

	body, err := httpGet(ctx, newHTTPClient(),
		b.baseURL+"/api/v3/account?"+params.Encode(),
		map[string]string{"X-MBX-APIKEY": b.apiKey},
		b.logger)
	if err != nil {
		b.logger.Debug("Binance spot balance failed", zap.Error(err))
		return 0
	}
	var account struct {
		Balances []struct {
			Asset string  `json:"asset"`
			Free  float64 `json:"free,string"`
		} `json:"balances"`
	}
	if err := unmarshal(body, &account); err != nil {
		return 0
	}
	stablecoins := map[string]bool{"USDT": true, "USDC": true, "BUSD": true, "FDUSD": true}
	var total float64
	for _, bal := range account.Balances {
		if stablecoins[bal.Asset] {
			total += bal.Free
		}
	}
	return total
}

// allWalletsUSD returns the total USD-equivalent balance across ALL Binance wallet types
// (Spot + Funding + Earn) using the /sapi/v1/asset/wallet/balance endpoint.
// That endpoint returns BTC-equivalent totals, so we convert using the current BTC price.
// Non-fatal: returns 0 if the API key lacks SAPI permissions.
func (b *BinanceClient) allWalletsUSD(ctx context.Context) float64 {
	params := url.Values{}
	params.Set("timestamp", strconv.FormatInt(time.Now().UnixMilli(), 10))
	params.Set("recvWindow", "5000")
	params.Set("signature", b.sign(params.Encode()))

	body, err := httpGet(ctx, newHTTPClient(),
		b.baseURL+"/sapi/v1/asset/wallet/balance?"+params.Encode(),
		map[string]string{"X-MBX-APIKEY": b.apiKey},
		b.logger)
	if err != nil {
		b.logger.Debug("Binance SAPI wallet balance unavailable", zap.Error(err))
		return 0
	}
	var wallets []struct {
		WalletName string  `json:"walletName"`
		Balance    float64 `json:"balance,string"` // BTC-denominated total
		Activate   bool    `json:"activate"`
	}
	if err := unmarshal(body, &wallets); err != nil {
		return 0
	}
	// Sum total BTC across all active wallets
	var totalBTC float64
	for _, w := range wallets {
		if w.Activate {
			totalBTC += w.Balance
		}
	}
	if totalBTC == 0 {
		return 0
	}
	// Convert BTC → USD using cached price or live fetch
	btcUSD := b.prices["BTCUSDT"]
	if btcUSD == 0 {
		btcUSD, _ = b.GetPrice(ctx, "BTCUSDT")
	}
	return totalBTC * btcUSD
}

// PlaceOrder routes to paper simulation or live futures limit order.
// Live mode places a GTX (Post-Only maker) limit order on Binance Futures.
// Requires the Binance account to have futures trading enabled.
func (b *BinanceClient) PlaceOrder(ctx context.Context, symbol, side string, sizeUSD float64) (orderID string, filledPrice float64, fees float64, err error) {
	if b.paperMode {
		return b.simOrder(ctx, symbol, side, sizeUSD)
	}
	return b.placeFuturesLimitOrder(ctx, symbol, side, sizeUSD)
}

// CloseOrder routes to paper simulation or live futures close.
func (b *BinanceClient) CloseOrder(ctx context.Context, orderID, symbol, side string) (exitPrice, pnl, fees float64, err error) {
	if b.paperMode {
		return b.simClose(ctx, symbol)
	}
	return b.closeFuturesOrder(ctx, orderID, symbol, side)
}

// simOrder simulates a futures limit order at real market price with realistic slippage.
// Uses futures maker fee model (0.02% per side) so paper P&L matches live expectations.
func (b *BinanceClient) simOrder(ctx context.Context, symbol, side string, sizeUSD float64) (string, float64, float64, error) {
	price, _ := b.GetPrice(ctx, symbol)
	if price == 0 {
		price = b.prices[symbol]
	}
	if price == 0 {
		return "", 0, 0, fmt.Errorf("no price for %s", symbol)
	}
	// Simulate limit order fill: buy at slightly above bid, sell at slightly below ask.
	// 0.05% slippage matches a limit order that rests briefly then fills.
	if side == "sell" || side == "short" {
		price *= 0.9995
	} else {
		price *= 1.0005
	}
	id := fmt.Sprintf("PAPER_BN_%s_%d", symbol, time.Now().UnixMilli())
	b.logger.Info("[PAPER] futures order", zap.String("id", id), zap.String("side", side),
		zap.Float64("usd", sizeUSD), zap.Float64("price", price))
	// Futures maker fee: 0.02% per side (0.0002). Round-trip: 0.04%.
	return id, price, sizeUSD * 0.0002, nil
}

func (b *BinanceClient) simClose(ctx context.Context, symbol string) (float64, float64, float64, error) {
	price, _ := b.GetPrice(ctx, symbol)
	if price == 0 {
		price = b.prices[symbol]
	}
	// Futures maker fee on close side: 0.02%
	return price, 0, price * 0.0002, nil
}

// placeFuturesLimitOrder places a GTX (Post-Only) limit order on Binance Futures.
// GTX = maker-or-cancel: order is rejected immediately if it would be a taker.
// This guarantees the 0.02% maker fee and never the 0.05% taker fee.
//
// Prerequisites:
//   - Binance account with Futures trading activated (one-time setup on Binance web)
//   - API key must have "Futures" permission enabled (separate from spot permissions)
//   - USDT-M Futures account must have USDT transferred from spot wallet
func (b *BinanceClient) placeFuturesLimitOrder(ctx context.Context, symbol, side string, sizeUSD float64) (string, float64, float64, error) {
	// Fetch futures price for quantity calculation
	price, err := b.getFuturesPrice(ctx, symbol)
	if err != nil || price == 0 {
		return "", 0, 0, fmt.Errorf("can't get futures price for %s: %w", symbol, err)
	}

	// Fetch futures best bid/ask to place limit price inside the spread
	bid, ask, _ := b.getFuturesBookTicker(ctx, symbol)
	var limitPrice float64
	var futureSide string
	if strings.ToUpper(side) == "BUY" || strings.ToUpper(side) == "LONG" {
		futureSide = "BUY"
		limitPrice = bid
		if limitPrice == 0 {
			limitPrice = price * 0.9999 // fallback: 0.01% below mid
		}
	} else {
		futureSide = "SELL"
		limitPrice = ask
		if limitPrice == 0 {
			limitPrice = price * 1.0001
		}
	}
	// Round price to 2 decimal places (safe for USDT pairs; precise symbols handled separately)
	limitPrice = math.Round(limitPrice*100) / 100

	// Quantity = USD / price, truncated to 3 decimal places (min lot size for most pairs)
	qty := math.Floor((sizeUSD/limitPrice)*1000) / 1000
	if qty <= 0 {
		return "", 0, 0, fmt.Errorf("quantity too small: sizeUSD=%.2f price=%.4f qty=%.3f", sizeUSD, limitPrice, qty)
	}

	params := url.Values{}
	params.Set("symbol", symbol)
	params.Set("side", futureSide)
	params.Set("type", "LIMIT")
	params.Set("timeInForce", "GTX") // Post-Only: rejected if it would be a taker
	params.Set("quantity", fmt.Sprintf("%.3f", qty))
	params.Set("price", fmt.Sprintf("%.2f", limitPrice))
	params.Set("timestamp", strconv.FormatInt(time.Now().UnixMilli(), 10))
	params.Set("recvWindow", "5000")

	encoded := params.Encode()
	endpoint := b.futuresURL + "/fapi/v1/order?" + encoded + "&signature=" + b.sign(encoded)

	body, err := httpPostForm(ctx, newHTTPClient(), endpoint,
		map[string]string{"X-MBX-APIKEY": b.apiKey})
	if err != nil {
		return "", 0, 0, fmt.Errorf("futures PlaceOrder failed: %w", err)
	}
	var resp struct {
		OrderID int64  `json:"orderId"`
		Status  string `json:"status"`
	}
	if err := unmarshal(body, &resp); err != nil {
		return "", 0, 0, err
	}
	makerFee := sizeUSD * 0.0002 // 0.02% maker fee
	b.logger.Info("Futures order placed",
		zap.String("symbol", symbol), zap.String("side", futureSide),
		zap.Int64("orderID", resp.OrderID), zap.Float64("price", limitPrice),
		zap.Float64("qty", qty), zap.String("status", resp.Status))
	return strconv.FormatInt(resp.OrderID, 10), limitPrice, makerFee, nil
}

// closeFuturesOrder places a market close order on Binance Futures.
// Uses MARKET type so the position closes immediately regardless of price.
func (b *BinanceClient) closeFuturesOrder(ctx context.Context, orderID, symbol, side string) (float64, float64, float64, error) {
	// Determine close side (opposite of entry)
	closeSide := "SELL"
	if strings.ToUpper(side) == "SELL" || strings.ToUpper(side) == "SHORT" {
		closeSide = "BUY"
	}

	params := url.Values{}
	params.Set("symbol", symbol)
	params.Set("side", closeSide)
	params.Set("type", "MARKET")
	params.Set("reduceOnly", "true")
	params.Set("timestamp", strconv.FormatInt(time.Now().UnixMilli(), 10))
	params.Set("recvWindow", "5000")

	encoded := params.Encode()
	endpoint := b.futuresURL + "/fapi/v1/order?" + encoded + "&signature=" + b.sign(encoded)

	body, err := httpPostForm(ctx, newHTTPClient(), endpoint,
		map[string]string{"X-MBX-APIKEY": b.apiKey})
	if err != nil {
		return 0, 0, 0, fmt.Errorf("futures CloseOrder failed: %w", err)
	}
	var resp struct {
		AvgPrice float64 `json:"avgPrice,string"`
	}
	if err := unmarshal(body, &resp); err != nil {
		return 0, 0, 0, err
	}
	takerFee := resp.AvgPrice * 0.0005 // 0.05% taker fee on market close
	return resp.AvgPrice, 0, takerFee, nil
}

// getFuturesPrice returns the mark price from Binance Futures.
func (b *BinanceClient) getFuturesPrice(ctx context.Context, symbol string) (float64, error) {
	body, err := httpGet(ctx, newHTTPClient(),
		b.futuresURL+"/fapi/v1/ticker/price?symbol="+symbol, nil, b.logger)
	if err != nil {
		// Fall back to spot price
		return b.GetPrice(ctx, symbol)
	}
	var r struct {
		Price float64 `json:"price,string"`
	}
	if err := unmarshal(body, &r); err != nil {
		return b.GetPrice(ctx, symbol)
	}
	return r.Price, nil
}

// getFuturesBookTicker returns best bid/ask from Binance Futures order book.
func (b *BinanceClient) getFuturesBookTicker(ctx context.Context, symbol string) (bid, ask float64, err error) {
	body, err := httpGet(ctx, newHTTPClient(),
		b.futuresURL+"/fapi/v1/ticker/bookTicker?symbol="+symbol, nil, b.logger)
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

// GetTopUSDTMarkets returns the top-N Binance USDT spot pairs ranked by 24h quote volume.
// Uses a single call to /api/v3/ticker/24hr. Results are cached by the scanner.
func (b *BinanceClient) GetTopUSDTMarkets(ctx context.Context, topN int) ([]string, error) {
	body, err := httpGet(ctx, newHTTPClient(), b.baseURL+"/api/v3/ticker/24hr", nil, b.logger)
	if err != nil {
		return nil, fmt.Errorf("top markets: %w", err)
	}
	var tickers []struct {
		Symbol   string  `json:"symbol"`
		QuoteVol float64 `json:"quoteVolume,string"`
	}
	if err := unmarshal(body, &tickers); err != nil {
		return nil, err
	}
	// Keep only USDT pairs (e.g. BTCUSDT, ETHUSDT) — skip leveraged tokens (*UP/*DOWN)
	type kv struct {
		sym string
		vol float64
	}
	var pairs []kv
	for _, t := range tickers {
		if !strings.HasSuffix(t.Symbol, "USDT") {
			continue
		}
		base := strings.TrimSuffix(t.Symbol, "USDT")
		if strings.Contains(base, "UP") || strings.Contains(base, "DOWN") ||
			strings.Contains(base, "BULL") || strings.Contains(base, "BEAR") {
			continue
		}
		pairs = append(pairs, kv{t.Symbol, t.QuoteVol})
	}
	sort.Slice(pairs, func(i, j int) bool { return pairs[i].vol > pairs[j].vol })
	out := make([]string, 0, topN)
	for i, p := range pairs {
		if i >= topN {
			break
		}
		// Return just the base (e.g. "BTC") so scanner appends "USDT" consistently
		out = append(out, strings.TrimSuffix(p.sym, "USDT"))
	}
	b.logger.Info("Top Binance USDT markets fetched", zap.Int("count", len(out)))
	return out, nil
}

func (b *BinanceClient) sign(payload string) string {
	mac := hmac.New(sha256.New, []byte(b.apiSecret))
	mac.Write([]byte(payload))
	return hex.EncodeToString(mac.Sum(nil))
}
