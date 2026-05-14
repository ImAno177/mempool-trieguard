package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"

	"mempool-trieguard/internal/bench"
	"mempool-trieguard/internal/config"
	"mempool-trieguard/internal/detector"
)

func main() {
	cfgPath := flag.String("config", "configs/app.yaml", "app config path")
	counterpartiesPath := flag.String("counterparties", "", "counterparties json path")
	replayPath := flag.String("replay", "", "replay jsonl path")
	tokenMetadataPath := flag.String("token-metadata", "", "optional token metadata json path")
	method := flag.String("method", "mempool_trieguard", "method: mempool_trieguard|mempool_trieguard_legacy|confirmed_chain|linear_scan|address_only_trie|prefix_only|suffix_only|intersection_trie|no_token|no_time|no_value")
	outDir := flag.String("out", "results", "output directory")
	noAlerts := flag.Bool("no-alerts", false, "omit alert JSONL and alert payload in summary")
	flag.Parse()

	if *counterpartiesPath == "" || *replayPath == "" {
		log.Fatalf("counterparties and replay paths are required")
	}

	if _, err := os.Stat(*cfgPath); os.IsNotExist(err) {
		*cfgPath = ""
	}
	cfg, err := config.Load(*cfgPath)
	if err != nil {
		log.Fatalf("load config: %v", err)
	}

	cps, err := bench.LoadCounterpartiesJSON(*counterpartiesPath)
	if err != nil {
		log.Fatalf("load counterparties: %v", err)
	}
	replay, err := bench.LoadReplayJSONL(*replayPath)
	if err != nil {
		log.Fatalf("load replay: %v", err)
	}
	tokenMetadata, err := bench.LoadTokenMetadataJSON(*tokenMetadataPath)
	if err != nil {
		log.Fatalf("load token metadata: %v", err)
	}

	dcfg := detector.Config{
		WindowDays:           cfg.Detector.WindowDays,
		KP:                   cfg.Detector.KP,
		KS:                   cfg.Detector.KS,
		ThetaP:               cfg.Detector.ThetaP,
		ThetaS:               cfg.Detector.ThetaS,
		MinPrefixDepth:       cfg.Detector.MinPrefixDepth,
		MinSuffixDepth:       cfg.Detector.MinSuffixDepth,
		MaxCandidatesPerSide: cfg.Detector.MaxCandidatesPerSide,
		Tau:                  cfg.Detector.Tau,
		Lambda:               cfg.Detector.Lambda,
		TinyValue:            cfg.Detector.TinyValue,
	}
	if len(cfg.Detector.Weights) == 5 {
		copy(dcfg.Weights[:], cfg.Detector.Weights)
	}

	output, err := bench.Run(*method, dcfg, cps, replay, tokenMetadata)
	if err != nil {
		log.Fatalf("run benchmark: %v", err)
	}

	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		log.Fatalf("mkdir out: %v", err)
	}

	alertsPath := filepath.Join(*outDir, fmt.Sprintf("alerts_%s.jsonl", *method))
	metricsPath := filepath.Join(*outDir, fmt.Sprintf("metrics_%s.csv", *method))
	summaryPath := filepath.Join(*outDir, fmt.Sprintf("summary_%s.json", *method))

	if *noAlerts {
		output.Alerts = nil
	} else {
		if err := bench.WriteAlertsJSONL(alertsPath, output.Alerts); err != nil {
			log.Fatalf("write alerts: %v", err)
		}
	}
	if err := bench.WriteMetricsCSV(metricsPath, output.Metrics); err != nil {
		log.Fatalf("write metrics: %v", err)
	}
	b, _ := json.MarshalIndent(output, "", "  ")
	if err := os.WriteFile(summaryPath, b, 0o644); err != nil {
		log.Fatalf("write summary: %v", err)
	}

	fmt.Printf("method=%s precision=%.4f recall=%.4f f1=%.4f throughput_tps=%.2f alerts=%d\n", *method, output.Metrics.Precision, output.Metrics.Recall, output.Metrics.F1, output.Metrics.ThroughputTPS, len(output.Alerts))
	if *noAlerts {
		fmt.Printf("alerts=omitted\nmetrics=%s\nsummary=%s\n", metricsPath, summaryPath)
	} else {
		fmt.Printf("alerts=%s\nmetrics=%s\nsummary=%s\n", alertsPath, metricsPath, summaryPath)
	}
}
