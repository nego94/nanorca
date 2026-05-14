// pkg/grpcserver/server.go — gRPC handler implementations
// Implements the ExecutorService proto interface + gRPC HealthServer.
// Phase 2A: Health() now pings all three exchanges in parallel for real status.
package grpcserver

import (
	"context"
	"sync"
	"sync/atomic"
	"time"

	"go.uber.org/zap"
	"google.golang.org/grpc/health/grpc_health_v1"

	"github.com/nanorca/executor/internal/exchanges"
	executor_pkg "github.com/nanorca/executor/internal/executor"
	"github.com/nanorca/executor/internal/scanner"
	pb "github.com/nanorca/executor/proto"
)

// Server implements pb.ExecutorServiceServer and grpc_health_v1.HealthServer.
type Server struct {
	pb.UnimplementedExecutorServiceServer
	logger    *zap.Logger
	scanner   *scanner.MarketScanner
	executor  *executor_pkg.OrderExecutor
	binance   *exchanges.BinanceClient
	polym     *exchanges.PolymarketClient
	hl        *exchanges.HyperliquidClient
	paperMode bool
	startedAt time.Time
	healthy   atomic.Bool
}

// NewServer constructs the gRPC server handler.
func NewServer(
	logger *zap.Logger,
	mscanner *scanner.MarketScanner,
	binance *exchanges.BinanceClient,
	polym *exchanges.PolymarketClient,
	hl *exchanges.HyperliquidClient,
	paperMode bool,
) *Server {
	s := &Server{
		logger:    logger.Named("grpc"),
		scanner:   mscanner,
		executor:  executor_pkg.NewOrderExecutor(logger, binance, polym, hl, paperMode),
		binance:   binance,
		polym:     polym,
		hl:        hl,
		paperMode: paperMode,
		startedAt: time.Now(),
	}
	s.healthy.Store(true)
	return s
}

// ScanMarkets runs a full parallel market scan and returns snapshots.
// req.Markets carries extra user-requested symbols (from /check Telegram command)
// that are unioned with the dynamic top-N Binance list in the scanner.
func (s *Server) ScanMarkets(ctx context.Context, req *pb.MarketScanRequest) (*pb.MarketScanResponse, error) {
	return s.scanner.ScanAll(ctx, req.Markets), nil
}

// PlaceOrder routes an order to the appropriate exchange.
func (s *Server) PlaceOrder(ctx context.Context, req *pb.PlaceOrderRequest) (*pb.PlaceOrderResponse, error) {
	return s.executor.PlaceOrder(ctx, req)
}

// CloseOrder closes an open position.
func (s *Server) CloseOrder(ctx context.Context, req *pb.CloseOrderRequest) (*pb.CloseOrderResponse, error) {
	return s.executor.CloseOrder(ctx, req)
}

// GetPositions returns all tracked open positions.
func (s *Server) GetPositions(ctx context.Context, _ *pb.GetPositionsRequest) (*pb.GetPositionsResponse, error) {
	return &pb.GetPositionsResponse{
		Positions: s.executor.GetPositions(ctx),
	}, nil
}

// GetBalances fetches real account balances from all three exchanges in parallel.
func (s *Server) GetBalances(ctx context.Context, _ *pb.GetBalancesRequest) (*pb.GetBalancesResponse, error) {
	type result struct {
		bal *exchanges.ExchangeBalance
	}
	balCtx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()

	results := make(chan *exchanges.ExchangeBalance, 3)

	go func() {
		bal, err := s.binance.GetAccountBalance(balCtx)
		if err != nil || bal == nil {
			bal = &exchanges.ExchangeBalance{Exchange: "binance", Error: "fetch failed"}
		}
		results <- bal
	}()
	go func() {
		bal, err := s.hl.GetAccountBalance(balCtx)
		if err != nil || bal == nil {
			bal = &exchanges.ExchangeBalance{Exchange: "hyperliquid", Error: "fetch failed"}
		}
		results <- bal
	}()
	go func() {
		bal, err := s.polym.GetAccountBalance(balCtx)
		if err != nil || bal == nil {
			bal = &exchanges.ExchangeBalance{Exchange: "polymarket", Error: "fetch failed"}
		}
		results <- bal
	}()

	var pbBalances []*pb.ExchangeBalance
	var totalUSD float64
	for i := 0; i < 3; i++ {
		b := <-results
		available := b.Error == ""
		pbBalances = append(pbBalances, &pb.ExchangeBalance{
			Exchange:  b.Exchange,
			Usdt:      b.USDT,
			TotalUsd:  b.TotalUSD,
			Available: available,
			Error:     b.Error,
		})
		if available {
			totalUSD += b.TotalUSD
		}
	}

	return &pb.GetBalancesResponse{
		Balances: pbBalances,
		TotalUsd: totalUSD,
	}, nil
}

// Health pings all three exchanges in parallel and returns real connectivity status.
func (s *Server) Health(ctx context.Context, _ *pb.HealthRequest) (*pb.HealthResponse, error) {
	pingCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()

	var bnOk, hlOk, polyOk bool
	var wg sync.WaitGroup
	wg.Add(3)
	go func() { defer wg.Done(); bnOk = s.binance.Ping(pingCtx) == nil }()
	go func() { defer wg.Done(); hlOk = s.hl.Ping(pingCtx) == nil }()
	go func() { defer wg.Done(); polyOk = s.polym.Ping(pingCtx) == nil }()
	wg.Wait()

	// Overall ok requires at least Binance reachable (primary exchange)
	overall := s.healthy.Load() && bnOk

	if !bnOk {
		s.logger.Warn("Health check: Binance unreachable")
	}
	if !hlOk {
		s.logger.Warn("Health check: Hyperliquid unreachable")
	}
	if !polyOk {
		s.logger.Warn("Health check: Polymarket unreachable")
	}

	return &pb.HealthResponse{
		Ok:            overall,
		Status:        "NANORCA executor serving",
		UptimeSeconds: int64(time.Since(s.startedAt).Seconds()),
		BinanceOk:     bnOk,
		PolymarketOk:  polyOk,
		HyperliquidOk: hlOk,
	}, nil
}

// Check implements grpc_health_v1.HealthServer.
func (s *Server) Check(_ context.Context, _ *grpc_health_v1.HealthCheckRequest) (*grpc_health_v1.HealthCheckResponse, error) {
	if s.healthy.Load() {
		return &grpc_health_v1.HealthCheckResponse{Status: grpc_health_v1.HealthCheckResponse_SERVING}, nil
	}
	return &grpc_health_v1.HealthCheckResponse{Status: grpc_health_v1.HealthCheckResponse_NOT_SERVING}, nil
}

// Watch implements grpc_health_v1.HealthServer (streaming — not used, required by interface).
func (s *Server) Watch(_ *grpc_health_v1.HealthCheckRequest, stream grpc_health_v1.Health_WatchServer) error {
	return stream.Send(&grpc_health_v1.HealthCheckResponse{Status: grpc_health_v1.HealthCheckResponse_SERVING})
}
