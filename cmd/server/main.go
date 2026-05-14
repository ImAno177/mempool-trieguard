package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"mempool-trieguard/internal/config"
	"mempool-trieguard/internal/live"
	"mempool-trieguard/internal/store"
	"mempool-trieguard/internal/web"
)

func main() {
	cfgPath := flag.String("config", "configs/app.yaml", "path to app yaml")
	flag.Parse()

	if _, err := os.Stat(*cfgPath); os.IsNotExist(err) {
		*cfgPath = ""
	}

	cfg, err := config.Load(*cfgPath)
	if err != nil {
		log.Fatalf("load config: %v", err)
	}

	if err := os.MkdirAll(filepath.Dir(cfg.DBPath), 0o755); err != nil {
		log.Fatalf("mkdir db dir: %v", err)
	}
	if err := os.MkdirAll(cfg.DataDir, 0o755); err != nil {
		log.Fatalf("mkdir data dir: %v", err)
	}

	st, err := store.Open(cfg.DBPath)
	if err != nil {
		log.Fatalf("open store: %v", err)
	}
	defer st.Close()

	liveSvc, err := live.NewService(cfg, st)
	if err != nil {
		log.Fatalf("init live service: %v", err)
	}

	webSrv, err := web.NewServer(cfg, st, liveSvc)
	if err != nil {
		log.Fatalf("init web server: %v", err)
	}

	httpSrv := &http.Server{
		Addr:    cfg.ListenAddr,
		Handler: webSrv.Handler(),
	}

	go func() {
		log.Printf("server listening on %s (mode=%s)", cfg.ListenAddr, cfg.Mode)
		if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("listen: %v", err)
		}
	}()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig

	liveSvc.Stop()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(ctx)
}
