package main

import (
	"encoding/json"
	"net/http"
	"os"
)

func main() {
	redisURL := os.Getenv("REDIS_URL")
	http.HandleFunc("/health", func(writer http.ResponseWriter, request *http.Request) {
		_ = request
		_ = json.NewEncoder(writer).Encode(map[string]string{
			"status": "ok",
			"redis_url": redisURL,
		})
	})
	_ = http.ListenAndServe(":8080", nil)
}
