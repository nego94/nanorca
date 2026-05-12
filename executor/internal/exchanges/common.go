// internal/exchanges/common.go — shared helpers for exchange clients
package exchanges

import "os"

// getEnv returns the value of an environment variable or a fallback default.
func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
