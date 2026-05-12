// pkg/grpcserver/server.go — gRPC handler implementations
// Implements the ExecutorService proto interface + gRPC HealthServer.
package grpcserver

import (
	"context"
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
		paperMode: paperMode,
		startedAt: time.Now(),
	}
	s.healthy.Store(true)
	return s
}

// ScanMarkets runs a full parallel market scan and returns snapshots.
func (s *Server) ScanMarkets(ctx context.Context, req *pb.MarketScanRequest) (*pb.MarketScanResponse, error) {
	return s.scanner.ScanAll(ctx), nil
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

// Health returns the health status of the executor service.
func (s *Server) Health(ctx context.Context, _ *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{
		Ok:            s.healthy.Load(),
		Status:        "NANORCA executor serving",
		UptimeSeconds: int64(time.Since(s.startedAt).Seconds()),
		// TODO Phase 2: set per-exchange status from ping results
		BinanceOk:     true,
		PolymarketOk:  true,
		HyperliquidOk: true,
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
