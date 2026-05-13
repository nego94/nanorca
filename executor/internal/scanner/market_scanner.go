// internal/scanner/market_scanner.go — parallel market scanner (hot path)
// Scans all configured markets across exchanges concurrently.
// Phase 3: Binance market list is dynamically refreshed from top-N by 24h volume.
package scanner

import (
	"context"
	"sync"
	"sync/atomic"
	"time"

	"go.uber.org/zap"

	"github.com/nanorca/executor/internal/exchanges"
	"github.com/nanorca/executor/internal/feed"
	pb "github.com/nanorca/executor/proto"
)

// MarketScanner scans all exchanges in parallel and aggregates snapshots.
type MarketScanner struct {
	logger         *zap.Logger
	binance        *exchanges.BinanceClient
	polym          *exchanges.PolymarketClient
	hl             *exchanges.HyperliquidClient
	staticMarkets  []string         // fallback static list (e.g. ["BTC","ETH","SOL"])
	binanceScanTopN int             // 0 = use staticMarkets; >0 = dynamic top-N
	priceCache     *feed.PriceCache

	// dynamic market list cache (refreshed every marketCacheTTL)
	marketsMu      sync.RWMutex
	cachedMarkets  []string
	marketsCachedAt time.Time
	marketCacheTTL  time.Duration
}

// NewMarketScanner constructs the scanner.
func NewMarketScanner(
	logger *zap.Logger,
	binance *exchanges.BinanceClient,
	polym *exchanges.PolymarketClient,
	hl *exchanges.HyperliquidClient,
	staticMarkets []string,
	binanceScanTopN int,
	priceCache *feed.PriceCache,
) *MarketScanner {
	return &MarketScanner{
		logger:          logger.Named("scanner"),
		binance:         binance,
		polym:           polym,
		hl:              hl,
		staticMarkets:   staticMarkets,
		binanceScanTopN: binanceScanTopN,
		priceCache:      priceCache,
		marketCacheTTL:  10 * time.Minute,
	}
}

// binanceMarkets returns the current Binance market list.
// If binanceScanTopN > 0, fetches top-N by volume (cached for 10 min).
// Falls back to staticMarkets on error.
func (s *MarketScanner) binanceMarkets(ctx context.Context) []string {
	if s.binanceScanTopN <= 0 {
		return s.staticMarkets
	}

	s.marketsMu.RLock()
	if time.Since(s.marketsCachedAt) < s.marketCacheTTL && len(s.cachedMarkets) > 0 {
		markets := s.cachedMarkets
		s.marketsMu.RUnlock()
		return markets
	}
	s.marketsMu.RUnlock()

	// Cache miss — fetch fresh list
	fetched, err := s.binance.GetTopUSDTMarkets(ctx, s.binanceScanTopN)
	if err != nil {
		s.logger.Warn("Top Binance markets fetch failed, using static list", zap.Error(err))
		return s.staticMarkets
	}

	s.marketsMu.Lock()
	s.cachedMarkets = fetched
	s.marketsCachedAt = time.Now()
	s.marketsMu.Unlock()

	return fetched
}

// ScanAll performs a parallel scan across all exchanges.
func (s *MarketScanner) ScanAll(ctx context.Context) *pb.MarketScanResponse {
	start := time.Now()

	var (
		mu        sync.Mutex
		snapshots []*pb.MarketSnapshot
		errCount  atomic.Int32
		wg        sync.WaitGroup
	)

	addSnapshot := func(snap *pb.MarketSnapshot) {
		mu.Lock()
		snapshots = append(snapshots, snap)
		mu.Unlock()
	}

	markets := s.binanceMarkets(ctx)

	// ── Binance scans — price + volume (parallel per symbol) ─────────────────
	for _, market := range markets {
		wg.Add(1)
		go func(m string) {
			defer wg.Done()
			symbol := m + "USDT"

			priceCh := make(chan struct {
				price float64
				err   error
			}, 1)
			volCh := make(chan float64, 1)

			go func() {
				p, err := s.binance.GetPrice(ctx, symbol)
				priceCh <- struct {
					price float64
					err   error
				}{p, err}
			}()
			go func() {
				v, _ := s.binance.GetVolume24h(ctx, symbol)
				volCh <- v
			}()

			pr := <-priceCh
			vol := <-volCh

			if pr.err != nil {
				s.logger.Warn("Binance price fetch failed",
					zap.String("symbol", symbol), zap.Error(pr.err))
				addSnapshot(&pb.MarketSnapshot{
					Exchange:    "binance",
					Market:      symbol,
					Available:   false,
					TimestampMs: time.Now().UnixMilli(),
				})
				errCount.Add(1)
				return
			}

			fundingRate, _ := s.binance.GetFundingRate(ctx, symbol)

			if s.priceCache != nil {
				_ = s.priceCache.Momentum(symbol, 5*time.Minute)
			}

			addSnapshot(&pb.MarketSnapshot{
				Exchange:    "binance",
				Market:      symbol,
				Price:       pr.price,
				Bid:         pr.price,
				Ask:         pr.price,
				Volume_24H:  vol,
				FundingRate: fundingRate,
				Available:   true,
				TimestampMs: time.Now().UnixMilli(),
			})
		}(market)
	}

	// ── Hyperliquid — funding rates for all perps ─────────────────────────────
	wg.Add(1)
	go func() {
		defer wg.Done()
		rates, err := s.hl.GetFundingRates(ctx)
		if err != nil {
			s.logger.Warn("Hyperliquid funding rates fetch failed", zap.Error(err))
			errCount.Add(1)
			return
		}
		for _, r := range rates {
			addSnapshot(&pb.MarketSnapshot{
				Exchange:    "hyperliquid",
				Market:      r.Coin,
				Price:       r.MarkPrice,
				FundingRate: r.FundingRate,
				Available:   true,
				TimestampMs: time.Now().UnixMilli(),
			})
		}
	}()

	// ── Polymarket — top active prediction markets ────────────────────────────
	wg.Add(1)
	go func() {
		defer wg.Done()
		markets, err := s.polym.GetTopMarkets(ctx, 20)
		if err != nil {
			s.logger.Warn("Polymarket market fetch failed", zap.Error(err))
			errCount.Add(1)
			return
		}
		for _, m := range markets {
			addSnapshot(&pb.MarketSnapshot{
				Exchange:    "polymarket",
				Market:      m.ConditionID,
				Price:       m.YesPrice,
				Bid:         m.YesPrice,
				Ask:         m.NoPrice,
				Volume_24H:  m.Volume24h,
				Available:   m.Active,
				TimestampMs: time.Now().UnixMilli(),
			})
		}
	}()

	wg.Wait()

	s.logger.Info("Market scan complete",
		zap.Int("snapshots", len(snapshots)),
		zap.Int32("errors", errCount.Load()),
		zap.Int("binance_markets", len(markets)),
		zap.Duration("elapsed", time.Since(start)),
	)

	return &pb.MarketScanResponse{
		Snapshots:   snapshots,
		ScannedAtMs: time.Now().UnixMilli(),
		ErrorCount:  errCount.Load(),
	}
}
