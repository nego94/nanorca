// cmd/healthcheck/main.go — tiny binary used by Docker HEALTHCHECK
// Makes a gRPC Health call to the executor and exits 0 on success, 1 on failure.
package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/health/grpc_health_v1"
)

func main() {
	port := "50051"
	if p := os.Getenv("EXECUTOR_GRPC_PORT"); p != "" {
		port = p
	}
	addr := "localhost:" + port

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	conn, err := grpc.DialContext(ctx, addr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithBlock(),
	)
	if err != nil {
		fmt.Fprintf(os.Stderr, "healthcheck: dial failed: %v\n", err)
		os.Exit(1)
	}
	defer conn.Close()

	client := grpc_health_v1.NewHealthClient(conn)
	resp, err := client.Check(ctx, &grpc_health_v1.HealthCheckRequest{})
	if err != nil || resp.GetStatus() != grpc_health_v1.HealthCheckResponse_SERVING {
		fmt.Fprintf(os.Stderr, "healthcheck: not serving (err=%v)\n", err)
		os.Exit(1)
	}
	fmt.Println("OK")
}
