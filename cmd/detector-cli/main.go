package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"mempool-trieguard/internal/bench"
	"mempool-trieguard/internal/config"
	"mempool-trieguard/internal/detector"
)

func main() {
	cfgPath := flag.String("config", "configs/app.yaml", "app config path")
	counterpartiesPath := flag.String("counterparties", "", "counterparties json path")
	replayPath := flag.String("replay", "", "replay jsonl path")
	tokenMetadataPath := flag.String("token-metadata", "", "optional token metadata json path")
	method := flag.String("method", "mempool_trieguard", "method: mempool_trieguard|mempool_trieguard_legacy|confirmed_chain|linear_scan|db_index|dblsh2_display|lsh_apg_display|address_only_trie|prefix_only|suffix_only|intersection_trie|no_type|no_token|no_time|no_value")
	outDir := flag.String("out", "results", "output directory")
	noAlerts := flag.Bool("no-alerts", false, "omit alert JSONL and alert payload in summary")
	dailyMetrics := flag.Bool("daily-metrics", false, "write per-day metrics in addition to aggregate metrics")
	dayBoundariesPath := flag.String("day-boundaries", "", "optional CSV mapping Ethereum UTC days to block ranges")
	tauSweep := flag.Bool("tau-sweep", false, "run one-pass tau sweep and write metrics for each tau")
	tauGrid := flag.String("tau-grid", "", "comma-separated tau values for --tau-sweep")
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
		ScoreMode:            cfg.Detector.ScoreMode,
		LogisticIntercept:    cfg.Detector.LogisticIntercept,
		AddressScoreMode:     cfg.Detector.AddressScoreMode,
		AddressBalanceAlpha:  cfg.Detector.AddressBalanceAlpha,
		AddressBalanceGamma:  cfg.Detector.AddressBalanceGamma,
		ContextGateBase:      cfg.Detector.ContextGateBase,
		TinyValue:            cfg.Detector.TinyValue,
	}
	if len(cfg.Detector.Weights) == 5 {
		copy(dcfg.Weights[:], cfg.Detector.Weights)
	}
	if len(cfg.Detector.ContextWeights) == 4 {
		copy(dcfg.ContextWeights[:], cfg.Detector.ContextWeights)
	}
	if len(cfg.Detector.LogisticWeights) == 3 {
		copy(dcfg.LogisticWeights[:], cfg.Detector.LogisticWeights)
	}

	var dayIndex *bench.DayBoundaryIndex
	if strings.TrimSpace(*dayBoundariesPath) != "" {
		dayIndex, err = bench.LoadDayBoundariesCSV(*dayBoundariesPath)
		if err != nil {
			log.Fatalf("load day boundaries: %v", err)
		}
		*dailyMetrics = true
	}

	if *tauSweep {
		taus, err := parseTauGrid(*tauGrid)
		if err != nil {
			log.Fatalf("parse tau grid: %v", err)
		}
		output, err := bench.RunTauSweep(*method, dcfg, cps, replay, tokenMetadata, taus)
		if err != nil {
			log.Fatalf("run tau sweep: %v", err)
		}
		if err := os.MkdirAll(*outDir, 0o755); err != nil {
			log.Fatalf("mkdir out: %v", err)
		}
		metricsPath := filepath.Join(*outDir, fmt.Sprintf("tau_sweep_%s.csv", *method))
		summaryPath := filepath.Join(*outDir, fmt.Sprintf("tau_sweep_%s.json", *method))
		if err := bench.WriteTauSweepCSV(metricsPath, output.Metrics); err != nil {
			log.Fatalf("write tau sweep metrics: %v", err)
		}
		b, _ := json.MarshalIndent(output, "", "  ")
		if err := os.WriteFile(summaryPath, b, 0o644); err != nil {
			log.Fatalf("write tau sweep summary: %v", err)
		}
		best := output.Metrics[0]
		for _, row := range output.Metrics[1:] {
			if row.F1 > best.F1 || (row.F1 == best.F1 && row.Precision > best.Precision) || (row.F1 == best.F1 && row.Precision == best.Precision && row.Tau < best.Tau) {
				best = row
			}
		}
		fmt.Printf("method=%s best_tau=%.6f precision=%.4f recall=%.4f f1=%.4f\n", *method, best.Tau, best.Precision, best.Recall, best.F1)
		fmt.Printf("tau_sweep=%s\nsummary=%s\n", metricsPath, summaryPath)
		return
	}

	output, err := bench.RunWithOptions(*method, dcfg, cps, replay, tokenMetadata, bench.RunOptions{
		DailyMetrics: *dailyMetrics,
		DayIndex:     dayIndex,
	})
	if err != nil {
		log.Fatalf("run benchmark: %v", err)
	}

	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		log.Fatalf("mkdir out: %v", err)
	}

	alertsPath := filepath.Join(*outDir, fmt.Sprintf("alerts_%s.jsonl", *method))
	metricsPath := filepath.Join(*outDir, fmt.Sprintf("metrics_%s.csv", *method))
	dailyMetricsPath := filepath.Join(*outDir, fmt.Sprintf("daily_metrics_%s.csv", *method))
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
	if *dailyMetrics {
		if err := bench.WriteDailyMetricsCSV(dailyMetricsPath, output.DailyMetrics); err != nil {
			log.Fatalf("write daily metrics: %v", err)
		}
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
	if *dailyMetrics {
		fmt.Printf("daily_metrics=%s\n", dailyMetricsPath)
	}
}

func parseTauGrid(value string) ([]float64, error) {
	if strings.TrimSpace(value) == "" {
		return []float64{0.4}, nil
	}
	parts := strings.Split(value, ",")
	taus := make([]float64, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		tau, err := strconv.ParseFloat(part, 64)
		if err != nil {
			return nil, err
		}
		taus = append(taus, tau)
	}
	if len(taus) == 0 {
		return nil, fmt.Errorf("empty tau grid")
	}
	return taus, nil
}
