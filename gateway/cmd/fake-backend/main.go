package main

import (
	"flag"
	"log"
	"net/http"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/fakebackend"
)

type chunksFlag []string

func (chunks *chunksFlag) String() string {
	return "repeatable response chunk"
}

func (chunks *chunksFlag) Set(value string) error {
	*chunks = append(*chunks, value)
	return nil
}

func main() {
	var responseChunks chunksFlag
	listenAddress := flag.String("listen", "127.0.0.1:18080", "HTTP listen address")
	ttft := flag.Duration("ttft", 0, "delay before response headers")
	chunkInterval := flag.Duration("chunk-interval", 0, "delay between SSE chunks")
	failAfterChunks := flag.Int("fail-after-chunks", 0, "truncate SSE after N chunks; zero disables failure")
	flag.Var(&responseChunks, "chunk", "response chunk; repeat to define an ordered SSE response")
	flag.Parse()

	if *failAfterChunks < 0 {
		log.Fatal("-fail-after-chunks must be non-negative")
	}
	if len(responseChunks) == 0 {
		responseChunks = []string{"Kavora fake backend response"}
	}

	backend := fakebackend.New(fakebackend.Config{
		ResponseChunks:  responseChunks,
		TTFT:            *ttft,
		ChunkInterval:   *chunkInterval,
		FailAfterChunks: *failAfterChunks,
	})
	server := &http.Server{
		Addr:              *listenAddress,
		Handler:           backend,
		ReadHeaderTimeout: 5 * time.Second,
	}

	log.Printf("kavora fake backend listening on %s", *listenAddress)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
