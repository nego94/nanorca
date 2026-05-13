// internal/exchanges/common.go — shared helpers for all exchange clients
package exchanges

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"go.uber.org/zap"
)

// getEnv returns env var value or fallback.
func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// newHTTPClient returns a configured http.Client with a 10s timeout.
func newHTTPClient() *http.Client {
	return &http.Client{Timeout: 10 * time.Second}
}

// ExchangeBalance is a real-time balance from one exchange.
type ExchangeBalance struct {
	Exchange  string
	USDT      float64
	TotalUSD  float64
	UpdatedAt time.Time
	Error     string // non-empty if fetch failed
}

// httpGet performs a GET with headers and simple retry, returns body bytes.
func httpGet(ctx context.Context, client *http.Client, rawURL string, headers map[string]string, log *zap.Logger) ([]byte, error) {
	var (
		body []byte
		err  error
	)
	for attempt := 1; attempt <= 3; attempt++ {
		req, e := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
		if e != nil {
			return nil, fmt.Errorf("build request: %w", e)
		}
		for k, v := range headers {
			req.Header.Set(k, v)
		}

		resp, e := client.Do(req)
		if e != nil {
			err = e
			if attempt < 3 {
				wait := time.Duration(attempt*attempt) * 300 * time.Millisecond
				log.Warn("HTTP retry", zap.String("url", rawURL), zap.Int("attempt", attempt), zap.Duration("wait", wait))
				select {
				case <-ctx.Done():
					return nil, ctx.Err()
				case <-time.After(wait):
				}
			}
			continue
		}
		defer resp.Body.Close()
		body, err = io.ReadAll(resp.Body)
		if err != nil {
			return nil, fmt.Errorf("read body: %w", err)
		}
		if resp.StatusCode != http.StatusOK {
			return nil, fmt.Errorf("HTTP %d from %s: %.300s", resp.StatusCode, rawURL, string(body))
		}
		return body, nil
	}
	return nil, fmt.Errorf("GET %s failed after retries: %w", rawURL, err)
}

// httpPost performs a POST with JSON body.
func httpPost(ctx context.Context, client *http.Client, rawURL string, jsonBody []byte, headers map[string]string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, rawURL, bytesReader(jsonBody))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d: %.300s", resp.StatusCode, string(body))
	}
	return body, nil
}

// unmarshal wraps json.Unmarshal with a friendly error message.
func unmarshal(data []byte, dst any) error {
	if err := json.Unmarshal(data, dst); err != nil {
		return fmt.Errorf("JSON parse failed: %w (body: %.200s)", err, string(data))
	}
	return nil
}

// bytesReader returns an io.Reader for a byte slice.
func bytesReader(b []byte) io.Reader {
	return &byteBuf{data: b}
}

type byteBuf struct {
	data []byte
	pos  int
}

func (r *byteBuf) Read(p []byte) (n int, err error) {
	if r.pos >= len(r.data) {
		return 0, io.EOF
	}
	n = copy(p, r.data[r.pos:])
	r.pos += n
	return n, nil
}
