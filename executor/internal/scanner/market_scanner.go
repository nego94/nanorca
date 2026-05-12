// internal/scanner/market_scanner.go — parallel market scanner (hot path)
// Scans all configured markets across all three exchanges concurrently.
// Returns a MarketScanResponse that the Python brain uses to build signals.
package scanner

import (
	"context"
	"sync"
	"time"

	"go.uber.org/zap"

	"github.com/nanorca/executor/internal/exchanges"
	pb "github.com/nanorca/executor/proto"
)

// MarketScanner scans all exchanges in parallel and aggregates snapshots.
type MarketScanner struct {
	logger    *zap.Logger
	binance   *exchanges.BinanceClient
	polym     *exchanges.PolymarketClient
	hl        *exchanges.HyperliquidClient
	markets   []string // priority markets (e.g. ["BTC","ETH","SOL"])
}

// NewMarketScanner constructs the scanner with exchange clients and priority list.
func NewMarketScanner(
	logger *zap.Logger,
	binance *exchanges.BinanceClient,
	polym *exchanges.PolymarketClient,
	hl *exchanges.HyperliquidClient,
	markets []string,
) *MarketScanner {
	return &MarketScanner{
		logger:  logger.Named("scanner"),
		binance: binance,
		polym:   polym,
		hl:      hl,
		markets: markets,
	}
}

// ScanAll performs a parallel scan of all priority markets across all exchanges.
// Each exchange is queried concurrently. Total scan time = slowest exchange.
func (s *MarketScanner) ScanAll(ctx context.Context) *pb.MarketScanResponse {
	start := time.Now()

	var (
		mu         sync.Mutex
		snapshots  []*pb.MarketSnapshot
		errorCount int32
		wg         sync.WaitGroup
	)

	addSnapshot := func(snap *pb.MarketSnapshot) {
		mu.Lock()
		snapshots = append(snapshots, snap)
		mu.Unlock()
	}

	addError := func() {
		mu.Lock()
		errorCount++
		mu.Unlock()
	}

	// ── Binance scans ─────────────────────────────────────────────────────────
	for _, market := range s.markets {
		wg.Add(1)
		go func(m string) {
			defer wg.Done()
			symbol := m + "USDT"
			price, err := s.binance.GetPrice(ctx, symbol)
			if err != nil {
				s.logger.Warn("Binance price fetch failed",
					zap.String("symbol", symbol), zap.Error(err))
				addSnapshot(&pb.MarketSnapshot{
					Exchange:  "binance",
					Market:    symbol,
					Available: false,
					TimestampMs: time.Now().UnixMilli(),
				})
				addError()
				return
			}
			fundingRate, _ := s.binance.GetFundingRate(ctx, symbol) // non-fatal if fails
			addSnapshot(&pb.MarketSnapshot{
				Exchange:    "binance",
				Market:      symbol,
				Price:       price,
				FundingRate: fundingRate,
				Available:   true,
				TimestampMs: time.Now().UnixMilli(),
			})
		}(market)
	}

	// ── Hyperliquid scans ─────────────────────────────────────────────────────
	wg.Add(1)
	go func() {
		defer wg.Done()
		rates, err := s.hl.GetFundingRates(ctx)
		if err != nil {
			s.logger.Warn("Hyperliquid funding rates fetch failed", zap.Error(err))
			addError()
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

	// ── Polymarket scans ──────────────────────────────────────────────────────
	wg.Add(1)
	go func() {
		defer wg.Done()
		markets, err := s.polym.GetTopMarkets(ctx, 20)
		if err != nil {
			s.logger.Warn("Polymarket market fetch failed", zap.Error(err))
			addError()
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
		zap.Int32("errors", errorCount),
		zap.Duration("elapsed", time.Since(start)),
	)

	return &pb.MarketScanResponse{
		Snapshots:   snapshots,
		ScannedAtMs: time.Now().UnixMilli(),
		ErrorCount:  errorCount,
	}
}
