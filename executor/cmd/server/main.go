// cmd/server/main.go — NANORCA Go Executor entry point
// Starts the gRPC server, initialises exchange clients, market scanner, and WebSocket feeds.
package main

import (
	"context"
	"fmt"
	"net"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"go.uber.org/zap"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/reflection"

	"github.com/nanorca/executor/internal/exchanges"
	"github.com/nanorca/executor/internal/feed"
	"github.com/nanorca/executor/internal/scanner"
	"github.com/nanorca/executor/pkg/grpcserver"
	pb "github.com/nanorca/executor/proto"
)

func main() {
	// ── Logger ────────────────────────────────────────────────────────────────
	logger, err := buildLogger()
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to build logger: %v\n", err)
		os.Exit(1)
	}
	defer logger.Sync() //nolint:errcheck

	// ── Config from env ───────────────────────────────────────────────────────
	grpcPort := getEnv("EXECUTOR_GRPC_PORT", "50051")
	paperMode := getEnv("PAPER_TRADING", "true") == "true"

	logger.Info("NANORCA Executor starting",
		zap.String("grpc_port", grpcPort),
		zap.Bool("paper_mode", paperMode),
	)

	// ── Exchange clients (stubbed — replace with real impl in Phase 2) ────────
	binanceClient := exchanges.NewBinanceClient(logger, paperMode)
	polymarketClient := exchanges.NewPolymarketClient(logger, paperMode)
	hyperliquidClient := exchanges.NewHyperliquidClient(logger, paperMode)

	// ── WebSocket feed manager ────────────────────────────────────────────────
	feedManager := feed.NewWSFeedManager(logger, binanceClient, hyperliquidClient)

	// ── Market scanner ────────────────────────────────────────────────────────
	priorityMarkets := splitCSV(getEnv("PRIORITY_MARKETS", "BTC,ETH,SOL"))
	binanceScanTopN := 0
	if v := getEnv("BINANCE_SCAN_TOP_N", "0"); v != "0" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			binanceScanTopN = n
		}
	}
	mscanner := scanner.NewMarketScanner(logger, binanceClient, polymarketClient, hyperliquidClient, priorityMarkets, binanceScanTopN, feedManager.Cache)

	// ── gRPC server ───────────────────────────────────────────────────────────
	lis, err := net.Listen("tcp", ":"+grpcPort)
	if err != nil {
		logger.Fatal("failed to listen", zap.Error(err))
	}

	srv := grpc.NewServer(
		grpc.UnaryInterceptor(loggingInterceptor(logger)),
	)

	// Register our executor service
	handler := grpcserver.NewServer(logger, mscanner, binanceClient, polymarketClient, hyperliquidClient, paperMode)
	pb.RegisterExecutorServiceServer(srv, handler)

	// Register gRPC health + reflection (useful for debugging)
	grpc_health_v1.RegisterHealthServer(srv, handler)
	reflection.Register(srv)

	// ── Background goroutines ─────────────────────────────────────────────────
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start WebSocket feeds in background
	go feedManager.Run(ctx)

	// ── Graceful shutdown ─────────────────────────────────────────────────────
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		<-quit
		logger.Info("Shutdown signal received")
		cancel()

		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer shutdownCancel()

		done := make(chan struct{})
		go func() {
			srv.GracefulStop()
			close(done)
		}()

		select {
		case <-done:
			logger.Info("gRPC server stopped gracefully")
		case <-shutdownCtx.Done():
			logger.Warn("Forced stop after timeout")
			srv.Stop()
		}
	}()

	logger.Info("gRPC server listening", zap.String("addr", lis.Addr().String()))
	if err := srv.Serve(lis); err != nil {
		logger.Fatal("gRPC server error", zap.Error(err))
	}
}

// buildLogger creates a structured JSON logger (zap).
// Uses development mode if LOG_LEVEL=debug, production mode otherwise.
func buildLogger() (*zap.Logger, error) {
	logLevel := getEnv("LOG_LEVEL", "info")
	if logLevel == "debug" {
		return zap.NewDevelopment()
	}
	return zap.NewProduction()
}

// loggingInterceptor logs every gRPC call duration and error (if any).
func loggingInterceptor(logger *zap.Logger) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		start := time.Now()
		resp, err := handler(ctx, req)
		logger.Debug("gRPC call",
			zap.String("method", info.FullMethod),
			zap.Duration("duration", time.Since(start)),
			zap.Error(err),
		)
		return resp, err
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func splitCSV(s string) []string {
	var out []string
	start := 0
	for i := 0; i <= len(s); i++ {
		if i == len(s) || s[i] == ',' {
			part := s[start:i]
			if len(part) > 0 {
				out = append(out, part)
			}
			start = i + 1
		}
	}
	return out
}
