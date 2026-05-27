package bench

import (
	"bufio"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"mempool-trieguard/internal/detector"
)

type ReplayEvent struct {
	detector.PendingTx
	BlockNumber     int64     `json:"block_number,omitempty"`
	BlockTime       time.Time `json:"block_time"`
	IsPoisoning     bool      `json:"is_poisoning"`
	VictimHint      string    `json:"victim_hint,omitempty"`
	LabelTxClass    string    `json:"label_tx_class,omitempty"`
	RunID           int       `json:"run_id,omitempty"`
	LossRate        float64   `json:"loss_rate,omitempty"`
	DelayProfileSec int       `json:"delay_profile_sec,omitempty"`
}

type MethodMetrics struct {
	Method               string  `json:"method"`
	Precision            float64 `json:"precision"`
	Recall               float64 `json:"recall"`
	F1                   float64 `json:"f1"`
	FalseAlertsPerAccDay float64 `json:"false_alerts_per_account_per_day"`
	MeanLatencyMs        float64 `json:"mean_latency_ms"`
	P95LatencyMs         float64 `json:"p95_latency_ms"`
	P99LatencyMs         float64 `json:"p99_latency_ms"`
	ThroughputTPS        float64 `json:"throughput_tps"`
	LookupMeanMs         float64 `json:"lookup_mean_ms"`
	LookupP95Ms          float64 `json:"lookup_p95_ms"`
	LookupP99Ms          float64 `json:"lookup_p99_ms"`
	TP                   int     `json:"tp"`
	FP                   int     `json:"fp"`
	FN                   int     `json:"fn"`
	TN                   int     `json:"tn"`
	TotalEvents          int     `json:"total_events"`
	ProtectedVictims     int     `json:"protected_victims"`
	AverageCandidates    float64 `json:"average_candidates_scored"`
	PositiveVisible      int     `json:"positive_visible_events"`
	PositiveDetected     int     `json:"positive_detected_events"`
	MissedNoCandidate    int     `json:"positive_missed_no_candidate"`
	MissedBelowTau       int     `json:"positive_missed_below_tau"`
	EstimatedMemoryPer1k float64 `json:"estimated_memory_per_1k_counterparties_kb"`
}

type Output struct {
	Metrics      MethodMetrics    `json:"metrics"`
	Alerts       []detector.Alert `json:"alerts"`
	DailyMetrics []DailyMetrics   `json:"daily_metrics,omitempty"`
}

type DailyMetrics struct {
	Method               string  `json:"method"`
	Day                  string  `json:"day"`
	Precision            float64 `json:"precision"`
	Recall               float64 `json:"recall"`
	F1                   float64 `json:"f1"`
	FalseAlertsPerAccDay float64 `json:"false_alerts_per_account_per_day"`
	MeanLatencyMs        float64 `json:"mean_latency_ms"`
	ThroughputTPS        float64 `json:"throughput_tps"`
	LookupMeanMs         float64 `json:"lookup_mean_ms"`
	TP                   int     `json:"tp"`
	FP                   int     `json:"fp"`
	FN                   int     `json:"fn"`
	TN                   int     `json:"tn"`
	TotalEvents          int     `json:"total_events"`
	ProtectedVictims     int     `json:"protected_victims"`
	AverageCandidates    float64 `json:"average_candidates_scored"`
	PositiveVisible      int     `json:"positive_visible_events"`
	PositiveDetected     int     `json:"positive_detected_events"`
	MissedNoCandidate    int     `json:"positive_missed_no_candidate"`
	MissedBelowTau       int     `json:"positive_missed_below_tau"`
}

type RunOptions struct {
	DailyMetrics bool
	DayIndex     *DayBoundaryIndex
}

type DayBoundary struct {
	Day        string
	StartBlock int64
	EndBlock   int64
}

type DayBoundaryIndex struct {
	boundaries []DayBoundary
}

type TauSweepMetrics struct {
	Method               string  `json:"method"`
	Tau                  float64 `json:"tau"`
	Precision            float64 `json:"precision"`
	Recall               float64 `json:"recall"`
	F1                   float64 `json:"f1"`
	FalseAlertsPerAccDay float64 `json:"false_alerts_per_account_per_day"`
	MeanLatencyMs        float64 `json:"mean_latency_ms"`
	P95LatencyMs         float64 `json:"p95_latency_ms"`
	P99LatencyMs         float64 `json:"p99_latency_ms"`
	ThroughputTPS        float64 `json:"throughput_tps"`
	LookupMeanMs         float64 `json:"lookup_mean_ms"`
	LookupP95Ms          float64 `json:"lookup_p95_ms"`
	LookupP99Ms          float64 `json:"lookup_p99_ms"`
	TP                   int     `json:"tp"`
	FP                   int     `json:"fp"`
	FN                   int     `json:"fn"`
	TN                   int     `json:"tn"`
	TotalEvents          int     `json:"total_events"`
	ProtectedVictims     int     `json:"protected_victims"`
	AverageCandidates    float64 `json:"average_candidates_scored"`
	PositiveVisible      int     `json:"positive_visible_events"`
	PositiveDetected     int     `json:"positive_detected_events"`
	MissedNoCandidate    int     `json:"positive_missed_no_candidate"`
	MissedBelowTau       int     `json:"positive_missed_below_tau"`
	EstimatedMemoryPer1k float64 `json:"estimated_memory_per_1k_counterparties_kb"`
}

type TauSweepOutput struct {
	Metrics []TauSweepMetrics `json:"metrics"`
}

func LoadCounterpartiesJSON(path string) ([]detector.Counterparty, error) {
	if strings.HasSuffix(strings.ToLower(path), ".jsonl") {
		f, err := os.Open(path)
		if err != nil {
			return nil, err
		}
		defer f.Close()
		scanner := bufio.NewScanner(f)
		cps := []detector.Counterparty{}
		lineNo := 0
		for scanner.Scan() {
			lineNo++
			line := strings.TrimSpace(scanner.Text())
			if line == "" {
				continue
			}
			var cp detector.Counterparty
			if err := json.Unmarshal([]byte(line), &cp); err != nil {
				return nil, fmt.Errorf("parse counterparty line %d: %w", lineNo, err)
			}
			cps = append(cps, cp)
		}
		if err := scanner.Err(); err != nil {
			return nil, err
		}
		return cps, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cps []detector.Counterparty
	if err := json.Unmarshal(b, &cps); err != nil {
		return nil, err
	}
	return cps, nil
}

func LoadTokenMetadataJSON(path string) ([]detector.TokenMetadata, error) {
	if strings.TrimSpace(path) == "" {
		return nil, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(strings.TrimSpace(string(b))) == 0 {
		return nil, nil
	}
	var byAddress map[string]detector.TokenMetadata
	if err := json.Unmarshal(b, &byAddress); err == nil {
		out := make([]detector.TokenMetadata, 0, len(byAddress))
		for addr, md := range byAddress {
			if md.Address == "" {
				md.Address = addr
			}
			out = append(out, md)
		}
		return out, nil
	}
	var list []detector.TokenMetadata
	if err := json.Unmarshal(b, &list); err != nil {
		return nil, err
	}
	return list, nil
}

func LoadReplayJSONL(path string) ([]ReplayEvent, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	out := []ReplayEvent{}
	lineNo := 0
	for scanner.Scan() {
		lineNo++
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var ev ReplayEvent
		if err := json.Unmarshal([]byte(line), &ev); err != nil {
			return nil, fmt.Errorf("parse replay line %d: %w", lineNo, err)
		}
		if ev.ObservedAt.IsZero() {
			ev.ObservedAt = time.Now().UTC()
		}
		if ev.BlockTime.IsZero() {
			ev.BlockTime = ev.ObservedAt.Add(12 * time.Second)
		}
		out = append(out, ev)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if len(out) == 0 {
		return nil, errors.New("empty replay events")
	}
	return out, nil
}

func LoadDayBoundariesCSV(path string) (*DayBoundaryIndex, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	r := csv.NewReader(f)
	rows, err := r.ReadAll()
	if err != nil {
		return nil, err
	}
	if len(rows) < 2 {
		return nil, fmt.Errorf("day boundary CSV has no data rows: %s", path)
	}
	header := map[string]int{}
	for i, name := range rows[0] {
		header[strings.TrimSpace(name)] = i
	}
	required := []string{"day", "start_block", "end_block"}
	for _, name := range required {
		if _, ok := header[name]; !ok {
			return nil, fmt.Errorf("day boundary CSV missing column %q", name)
		}
	}
	boundaries := make([]DayBoundary, 0, len(rows)-1)
	for lineNo, row := range rows[1:] {
		if len(row) == 0 {
			continue
		}
		day := strings.TrimSpace(row[header["day"]])
		if day == "" {
			continue
		}
		startBlock, err := strconv.ParseInt(strings.TrimSpace(row[header["start_block"]]), 10, 64)
		if err != nil {
			return nil, fmt.Errorf("parse start_block on CSV row %d: %w", lineNo+2, err)
		}
		endBlock, err := strconv.ParseInt(strings.TrimSpace(row[header["end_block"]]), 10, 64)
		if err != nil {
			return nil, fmt.Errorf("parse end_block on CSV row %d: %w", lineNo+2, err)
		}
		boundaries = append(boundaries, DayBoundary{
			Day:        day,
			StartBlock: startBlock,
			EndBlock:   endBlock,
		})
	}
	sort.Slice(boundaries, func(i, j int) bool {
		return boundaries[i].StartBlock < boundaries[j].StartBlock
	})
	if len(boundaries) == 0 {
		return nil, fmt.Errorf("day boundary CSV has no valid ranges: %s", path)
	}
	return &DayBoundaryIndex{boundaries: boundaries}, nil
}

func (idx *DayBoundaryIndex) DayForBlock(blockNumber int64) string {
	if idx == nil || len(idx.boundaries) == 0 || blockNumber <= 0 {
		return ""
	}
	i := sort.Search(len(idx.boundaries), func(i int) bool {
		return idx.boundaries[i].StartBlock > blockNumber
	})
	if i == 0 {
		return ""
	}
	row := idx.boundaries[i-1]
	if blockNumber >= row.StartBlock && blockNumber <= row.EndBlock {
		return row.Day
	}
	return ""
}

func RunTauSweep(method string, cfg detector.Config, cps []detector.Counterparty, replay []ReplayEvent, metadata []detector.TokenMetadata, taus []float64) (TauSweepOutput, error) {
	if len(taus) == 0 {
		return TauSweepOutput{}, errors.New("empty tau grid")
	}
	sort.Float64s(taus)

	eng := detector.NewEngine(cfg)
	if err := eng.LoadCounterparties(cps); err != nil {
		return TauSweepOutput{}, err
	}
	eng.SetTokenMetadata(metadata)
	scoreFn, err := buildScoreRunner(method, eng, cfg, cps, metadata)
	if err != nil {
		return TauSweepOutput{}, err
	}

	start := time.Now()
	lookupLat := make([]float64, 0, len(replay))
	candCount := 0
	candObs := 0
	positiveVisible := 0
	positiveNoCandidate := 0
	posScores := make([]float64, 0)
	negScores := make([]float64, 0)

	for _, ev := range replay {
		visible := method == "confirmed_chain" || eventVisible(ev)
		if !visible {
			if ev.IsPoisoning {
				posScores = append(posScores, -1)
			} else {
				negScores = append(negScores, -1)
			}
			continue
		}
		if ev.IsPoisoning {
			positiveVisible++
		}

		s0 := time.Now()
		score, perf := scoreFn(ev)
		lookupLat = append(lookupLat, time.Since(s0).Seconds()*1000)
		eventCandidates := 0
		for _, p := range perf {
			candCount += p.CandidatesScored
			eventCandidates += p.CandidatesScored
			candObs++
		}

		scoreValue := -1.0
		if score.Found {
			scoreValue = score.Score.Total
		}
		if ev.IsPoisoning {
			if !score.Found && eventCandidates == 0 {
				positiveNoCandidate++
			}
			posScores = append(posScores, scoreValue)
		} else {
			negScores = append(negScores, scoreValue)
		}
	}

	sort.Float64s(posScores)
	sort.Float64s(negScores)
	totalPos := len(posScores)
	totalNeg := len(negScores)
	elapsed := time.Since(start).Seconds()
	throughput := safeDiv(float64(len(replay)), elapsed)

	rows := make([]TauSweepMetrics, 0, len(taus))
	for _, tau := range taus {
		tp := countScoresAtLeast(posScores, tau)
		fp := countScoresAtLeast(negScores, tau)
		fn := totalPos - tp
		tn := totalNeg - fp
		precision := safeDiv(float64(tp), float64(tp+fp))
		recall := safeDiv(float64(tp), float64(tp+fn))
		f1 := safeDiv(2*precision*recall, precision+recall)
		missedBelowTau := positiveVisible - tp - positiveNoCandidate
		if missedBelowTau < 0 {
			missedBelowTau = 0
		}
		rows = append(rows, TauSweepMetrics{
			Method:               method,
			Tau:                  tau,
			Precision:            precision,
			Recall:               recall,
			F1:                   f1,
			FalseAlertsPerAccDay: 0,
			MeanLatencyMs:        0,
			P95LatencyMs:         0,
			P99LatencyMs:         0,
			ThroughputTPS:        throughput,
			LookupMeanMs:         mean(lookupLat),
			LookupP95Ms:          percentile(lookupLat, 95),
			LookupP99Ms:          percentile(lookupLat, 99),
			TP:                   tp,
			FP:                   fp,
			FN:                   fn,
			TN:                   tn,
			TotalEvents:          len(replay),
			ProtectedVictims:     eng.ProtectedVictimCount(),
			AverageCandidates:    safeDiv(float64(candCount), float64(max(1, candObs))),
			PositiveVisible:      positiveVisible,
			PositiveDetected:     tp,
			MissedNoCandidate:    positiveNoCandidate,
			MissedBelowTau:       missedBelowTau,
			EstimatedMemoryPer1k: estimateMemoryPer1k(cps),
		})
	}
	return TauSweepOutput{Metrics: rows}, nil
}

func countScoresAtLeast(scores []float64, tau float64) int {
	idx := sort.Search(len(scores), func(i int) bool {
		return scores[i] >= tau
	})
	return len(scores) - idx
}

func Run(method string, cfg detector.Config, cps []detector.Counterparty, replay []ReplayEvent, metadata []detector.TokenMetadata) (Output, error) {
	return RunWithOptions(method, cfg, cps, replay, metadata, RunOptions{})
}

type dailyAgg struct {
	TP                int
	FP                int
	FN                int
	TN                int
	TotalEvents       int
	LookupSumMs       float64
	LookupCount       int
	AlertLatencySumMs float64
	AlertLatencyCount int
	FalseAlerts       int
	CandidatesScored  int
	CandidateObs      int
	PositiveVisible   int
	PositiveDetected  int
	MissedNoCandidate int
	MissedBelowTau    int
}

func RunWithOptions(method string, cfg detector.Config, cps []detector.Counterparty, replay []ReplayEvent, metadata []detector.TokenMetadata, opts RunOptions) (Output, error) {
	eng := detector.NewEngine(cfg)
	if err := eng.LoadCounterparties(cps); err != nil {
		return Output{}, err
	}
	eng.SetTokenMetadata(metadata)
	detectFn, err := buildDetectorRunner(method, eng, cfg, cps, metadata)
	if err != nil {
		return Output{}, err
	}

	start := time.Now()
	alerts := make([]detector.Alert, 0)
	lookupLat := make([]float64, 0, len(replay))
	alertLat := make([]float64, 0)
	candCount := 0
	candObs := 0
	positiveVisible := 0
	positiveDetected := 0
	missedNoCandidate := 0
	missedBelowTau := 0

	predByTx := map[string]bool{}
	labelByTx := map[string]bool{}
	dayByTx := map[string]string{}
	daily := map[string]*dailyAgg{}
	falseByVictimDay := map[string]int{}
	victims := map[string]struct{}{}
	days := map[string]struct{}{}

	for _, ev := range replay {
		day := eventDay(ev, opts.DayIndex)
		if day == "" {
			day = ev.ObservedAt.UTC().Format("2006-01-02")
		}
		if opts.DailyMetrics {
			agg := ensureDailyAgg(daily, day)
			agg.TotalEvents++
		}
		labelByTx[ev.Hash] = labelByTx[ev.Hash] || ev.IsPoisoning
		if _, ok := dayByTx[ev.Hash]; !ok {
			dayByTx[ev.Hash] = day
		}
		from, _ := detector.NormalizeAddress(ev.From)
		to, _ := detector.NormalizeAddress(ev.To)
		if from != "" {
			victims[from] = struct{}{}
		}
		if to != "" {
			victims[to] = struct{}{}
		}
		days[day] = struct{}{}

		visible := method == "confirmed_chain" || eventVisible(ev)
		if ev.IsPoisoning && visible {
			positiveVisible++
			if opts.DailyMetrics {
				ensureDailyAgg(daily, day).PositiveVisible++
			}
		}
		if !visible {
			lookupLat = append(lookupLat, 0)
			if opts.DailyMetrics {
				agg := ensureDailyAgg(daily, day)
				agg.LookupCount++
			}
			continue
		}

		s0 := time.Now()
		methodAlerts, perf := detectFn(ev)
		lookupMs := time.Since(s0).Seconds() * 1000
		lookupLat = append(lookupLat, lookupMs)
		if opts.DailyMetrics {
			agg := ensureDailyAgg(daily, day)
			agg.LookupSumMs += lookupMs
			agg.LookupCount++
		}
		eventCandidates := 0
		for _, p := range perf {
			candCount += p.CandidatesScored
			eventCandidates += p.CandidatesScored
			candObs++
			if opts.DailyMetrics {
				agg := ensureDailyAgg(daily, day)
				agg.CandidatesScored += p.CandidatesScored
				agg.CandidateObs++
			}
		}

		if len(methodAlerts) > 0 {
			predByTx[ev.Hash] = true
			alerts = append(alerts, methodAlerts...)
			for _, a := range methodAlerts {
				lat := a.ObservedAt.Sub(ev.ObservedAt).Seconds() * 1000
				if method == "confirmed_chain" {
					lat = ev.BlockTime.Sub(ev.ObservedAt).Seconds() * 1000
				}
				alertLat = append(alertLat, lat)
				if opts.DailyMetrics {
					agg := ensureDailyAgg(daily, day)
					agg.AlertLatencySumMs += lat
					agg.AlertLatencyCount++
				}
				if !ev.IsPoisoning {
					k := a.Victim + "|" + day
					falseByVictimDay[k]++
					if opts.DailyMetrics {
						ensureDailyAgg(daily, day).FalseAlerts++
					}
				}
			}
		}
		if ev.IsPoisoning {
			if len(methodAlerts) > 0 {
				positiveDetected++
				if opts.DailyMetrics {
					ensureDailyAgg(daily, day).PositiveDetected++
				}
			} else if eventCandidates == 0 {
				missedNoCandidate++
				if opts.DailyMetrics {
					ensureDailyAgg(daily, day).MissedNoCandidate++
				}
			} else {
				missedBelowTau++
				if opts.DailyMetrics {
					ensureDailyAgg(daily, day).MissedBelowTau++
				}
			}
		}
	}

	tp, fp, fn, tn := 0, 0, 0, 0
	for tx, isPoison := range labelByTx {
		pred := predByTx[tx]
		if opts.DailyMetrics {
			agg := ensureDailyAgg(daily, dayByTx[tx])
			switch {
			case pred && isPoison:
				agg.TP++
			case pred && !isPoison:
				agg.FP++
			case !pred && isPoison:
				agg.FN++
			case !pred && !isPoison:
				agg.TN++
			}
		}
		switch {
		case pred && isPoison:
			tp++
		case pred && !isPoison:
			fp++
		case !pred && isPoison:
			fn++
		case !pred && !isPoison:
			tn++
		}
	}

	precision := safeDiv(float64(tp), float64(tp+fp))
	recall := safeDiv(float64(tp), float64(tp+fn))
	f1 := safeDiv(2*precision*recall, precision+recall)

	totalFalse := 0
	for _, v := range falseByVictimDay {
		totalFalse += v
	}
	falseRate := safeDiv(float64(totalFalse), float64(max(1, len(victims))*max(1, len(days))))

	elapsed := time.Since(start).Seconds()
	throughput := safeDiv(float64(len(replay)), elapsed)

	out := Output{
		Metrics: MethodMetrics{
			Method:               method,
			Precision:            precision,
			Recall:               recall,
			F1:                   f1,
			FalseAlertsPerAccDay: falseRate,
			MeanLatencyMs:        mean(alertLat),
			P95LatencyMs:         percentile(alertLat, 95),
			P99LatencyMs:         percentile(alertLat, 99),
			ThroughputTPS:        throughput,
			LookupMeanMs:         mean(lookupLat),
			LookupP95Ms:          percentile(lookupLat, 95),
			LookupP99Ms:          percentile(lookupLat, 99),
			TP:                   tp,
			FP:                   fp,
			FN:                   fn,
			TN:                   tn,
			TotalEvents:          len(replay),
			ProtectedVictims:     eng.ProtectedVictimCount(),
			AverageCandidates:    safeDiv(float64(candCount), float64(max(1, candObs))),
			PositiveVisible:      positiveVisible,
			PositiveDetected:     positiveDetected,
			MissedNoCandidate:    missedNoCandidate,
			MissedBelowTau:       missedBelowTau,
			EstimatedMemoryPer1k: estimateMemoryPer1k(cps),
		},
		Alerts: alerts,
	}
	if opts.DailyMetrics {
		out.DailyMetrics = finalizeDailyMetrics(method, daily, eng.ProtectedVictimCount(), elapsed)
	}
	return out, nil
}

var replayBlockGenesis = time.Date(2015, 7, 30, 0, 0, 0, 0, time.UTC)

func ensureDailyAgg(daily map[string]*dailyAgg, day string) *dailyAgg {
	if day == "" {
		day = "unknown"
	}
	agg, ok := daily[day]
	if !ok {
		agg = &dailyAgg{}
		daily[day] = agg
	}
	return agg
}

func eventDay(ev ReplayEvent, idx *DayBoundaryIndex) string {
	if idx != nil {
		blockNumber := ev.BlockNumber
		if blockNumber <= 0 {
			blockNumber = approximateReplayBlockNumber(ev.BlockTime)
		}
		if day := idx.DayForBlock(blockNumber); day != "" {
			return day
		}
	}
	if !ev.BlockTime.IsZero() {
		return ev.BlockTime.UTC().Format("2006-01-02")
	}
	return ev.ObservedAt.UTC().Format("2006-01-02")
}

func approximateReplayBlockNumber(blockTime time.Time) int64 {
	if blockTime.IsZero() {
		return 0
	}
	// Full-label replay shards were generated from block numbers with a fixed
	// 12-second clock; invert that encoding when older shards lack block_number.
	seconds := blockTime.UTC().Sub(replayBlockGenesis).Seconds()
	if seconds <= 0 {
		return 0
	}
	return int64(math.Round(seconds / 12.0))
}

func finalizeDailyMetrics(method string, daily map[string]*dailyAgg, protectedVictims int, elapsedSeconds float64) []DailyMetrics {
	days := make([]string, 0, len(daily))
	for day := range daily {
		days = append(days, day)
	}
	sort.Strings(days)
	rows := make([]DailyMetrics, 0, len(days))
	totalEvents := 0
	for _, agg := range daily {
		totalEvents += agg.TotalEvents
	}
	for _, day := range days {
		agg := daily[day]
		precision := safeDiv(float64(agg.TP), float64(agg.TP+agg.FP))
		recall := safeDiv(float64(agg.TP), float64(agg.TP+agg.FN))
		f1 := safeDiv(2*precision*recall, precision+recall)
		dayElapsed := elapsedSeconds * safeDiv(float64(agg.TotalEvents), float64(totalEvents))
		rows = append(rows, DailyMetrics{
			Method:               method,
			Day:                  day,
			Precision:            precision,
			Recall:               recall,
			F1:                   f1,
			FalseAlertsPerAccDay: safeDiv(float64(agg.FalseAlerts), float64(max(1, protectedVictims))),
			MeanLatencyMs:        safeDiv(agg.AlertLatencySumMs, float64(agg.AlertLatencyCount)),
			ThroughputTPS:        safeDiv(float64(agg.TotalEvents), dayElapsed),
			LookupMeanMs:         safeDiv(agg.LookupSumMs, float64(agg.LookupCount)),
			TP:                   agg.TP,
			FP:                   agg.FP,
			FN:                   agg.FN,
			TN:                   agg.TN,
			TotalEvents:          agg.TotalEvents,
			ProtectedVictims:     protectedVictims,
			AverageCandidates:    safeDiv(float64(agg.CandidatesScored), float64(max(1, agg.CandidateObs))),
			PositiveVisible:      agg.PositiveVisible,
			PositiveDetected:     agg.PositiveDetected,
			MissedNoCandidate:    agg.MissedNoCandidate,
			MissedBelowTau:       agg.MissedBelowTau,
		})
	}
	return rows
}

func buildDetectorRunner(method string, eng *detector.Engine, cfg detector.Config, cps []detector.Counterparty, metadata []detector.TokenMetadata) (func(ReplayEvent) ([]detector.Alert, []detector.PerfRecord), error) {
	switch method {
	case "mempool_trieguard":
		return func(ev ReplayEvent) ([]detector.Alert, []detector.PerfRecord) {
			return eng.Detect(ev.PendingTx)
		}, nil
	case "mempool_trieguard_legacy":
		return func(ev ReplayEvent) ([]detector.Alert, []detector.PerfRecord) {
			return eng.DetectLegacy(ev.PendingTx)
		}, nil
	case "confirmed_chain":
		return func(ev ReplayEvent) ([]detector.Alert, []detector.PerfRecord) {
			alerts, perf := eng.DetectPriorRule(ev.PendingTx)
			for i := range alerts {
				alerts[i].ObservedAt = ev.BlockTime
			}
			return alerts, perf
		}, nil
	case "address_only_trie":
		tmp := cfg
		tmp.Weights = [5]float64{1, 0, 0, 0, 0}
		tmpEng := detector.NewEngine(tmp)
		if err := tmpEng.LoadCounterparties(cps); err != nil {
			return nil, err
		}
		tmpEng.SetTokenMetadata(metadata)
		return func(ev ReplayEvent) ([]detector.Alert, []detector.PerfRecord) {
			return tmpEng.Detect(ev.PendingTx)
		}, nil
	case "prefix_only":
		tmp := cfg
		tmpEng := detector.NewEngine(tmp)
		if err := tmpEng.LoadCounterparties(cps); err != nil {
			return nil, err
		}
		tmpEng.SetTokenMetadata(metadata)
		return func(ev ReplayEvent) ([]detector.Alert, []detector.PerfRecord) {
			return tmpEng.DetectPrefixOnly(ev.PendingTx)
		}, nil
	case "suffix_only":
		tmp := cfg
		tmpEng := detector.NewEngine(tmp)
		if err := tmpEng.LoadCounterparties(cps); err != nil {
			return nil, err
		}
		tmpEng.SetTokenMetadata(metadata)
		return func(ev ReplayEvent) ([]detector.Alert, []detector.PerfRecord) {
			return tmpEng.DetectSuffixOnly(ev.PendingTx)
		}, nil
	case "intersection_trie":
		tmp := cfg
		tmpEng := detector.NewEngine(tmp)
		if err := tmpEng.LoadCounterparties(cps); err != nil {
			return nil, err
		}
		tmpEng.SetTokenMetadata(metadata)
		return func(ev ReplayEvent) ([]detector.Alert, []detector.PerfRecord) {
			return tmpEng.DetectIntersection(ev.PendingTx)
		}, nil
	case "no_token":
		return runnerWithWeights(cfg, cps, metadata, zeroAndRenormalize(cfg.Weights, 2), detectorModeDefault)
	case "no_time":
		return runnerWithWeights(cfg, cps, metadata, zeroAndRenormalize(cfg.Weights, 3), detectorModeDefault)
	case "no_value":
		return runnerWithWeights(cfg, cps, metadata, zeroAndRenormalize(cfg.Weights, 4), detectorModeDefault)
	case "linear_scan":
		return func(ev ReplayEvent) ([]detector.Alert, []detector.PerfRecord) {
			return eng.DetectLinear(ev.PendingTx)
		}, nil
	default:
		return func(ev ReplayEvent) ([]detector.Alert, []detector.PerfRecord) {
			return eng.Detect(ev.PendingTx)
		}, nil
	}
}

type scoreRunner func(ReplayEvent) (detector.ScoreResult, []detector.PerfRecord)

func buildScoreRunner(method string, eng *detector.Engine, cfg detector.Config, cps []detector.Counterparty, metadata []detector.TokenMetadata) (scoreRunner, error) {
	switch method {
	case "mempool_trieguard":
		return func(ev ReplayEvent) (detector.ScoreResult, []detector.PerfRecord) {
			return eng.MaxScore(ev.PendingTx)
		}, nil
	case "address_only_trie":
		tmp := cfg
		tmp.Weights = [5]float64{1, 0, 0, 0, 0}
		tmpEng := detector.NewEngine(tmp)
		if err := tmpEng.LoadCounterparties(cps); err != nil {
			return nil, err
		}
		tmpEng.SetTokenMetadata(metadata)
		return func(ev ReplayEvent) (detector.ScoreResult, []detector.PerfRecord) {
			return tmpEng.MaxScore(ev.PendingTx)
		}, nil
	case "prefix_only":
		tmpEng := detector.NewEngine(cfg)
		if err := tmpEng.LoadCounterparties(cps); err != nil {
			return nil, err
		}
		tmpEng.SetTokenMetadata(metadata)
		return func(ev ReplayEvent) (detector.ScoreResult, []detector.PerfRecord) {
			return tmpEng.MaxScorePrefixOnly(ev.PendingTx)
		}, nil
	case "suffix_only":
		tmpEng := detector.NewEngine(cfg)
		if err := tmpEng.LoadCounterparties(cps); err != nil {
			return nil, err
		}
		tmpEng.SetTokenMetadata(metadata)
		return func(ev ReplayEvent) (detector.ScoreResult, []detector.PerfRecord) {
			return tmpEng.MaxScoreSuffixOnly(ev.PendingTx)
		}, nil
	case "intersection_trie":
		tmpEng := detector.NewEngine(cfg)
		if err := tmpEng.LoadCounterparties(cps); err != nil {
			return nil, err
		}
		tmpEng.SetTokenMetadata(metadata)
		return func(ev ReplayEvent) (detector.ScoreResult, []detector.PerfRecord) {
			return tmpEng.MaxScoreIntersection(ev.PendingTx)
		}, nil
	case "no_token":
		return runnerWithScoreWeights(cfg, cps, metadata, zeroAndRenormalize(cfg.Weights, 2))
	case "no_time":
		return runnerWithScoreWeights(cfg, cps, metadata, zeroAndRenormalize(cfg.Weights, 3))
	case "no_value":
		return runnerWithScoreWeights(cfg, cps, metadata, zeroAndRenormalize(cfg.Weights, 4))
	default:
		return nil, fmt.Errorf("tau sweep does not support method %q", method)
	}
}

func runnerWithScoreWeights(cfg detector.Config, cps []detector.Counterparty, metadata []detector.TokenMetadata, weights [5]float64) (scoreRunner, error) {
	tmp := cfg
	tmp.Weights = weights
	tmpEng := detector.NewEngine(tmp)
	if err := tmpEng.LoadCounterparties(cps); err != nil {
		return nil, err
	}
	tmpEng.SetTokenMetadata(metadata)
	return func(ev ReplayEvent) (detector.ScoreResult, []detector.PerfRecord) {
		return tmpEng.MaxScore(ev.PendingTx)
	}, nil
}

type detectorMode int

const (
	detectorModeDefault detectorMode = iota
)

func runnerWithWeights(cfg detector.Config, cps []detector.Counterparty, metadata []detector.TokenMetadata, weights [5]float64, mode detectorMode) (func(ReplayEvent) ([]detector.Alert, []detector.PerfRecord), error) {
	_ = mode
	tmp := cfg
	tmp.Weights = weights
	tmpEng := detector.NewEngine(tmp)
	if err := tmpEng.LoadCounterparties(cps); err != nil {
		return nil, err
	}
	tmpEng.SetTokenMetadata(metadata)
	return func(ev ReplayEvent) ([]detector.Alert, []detector.PerfRecord) {
		return tmpEng.Detect(ev.PendingTx)
	}, nil
}

func zeroAndRenormalize(weights [5]float64, idx int) [5]float64 {
	if idx < 0 || idx >= len(weights) {
		return weights
	}
	weights[idx] = 0
	sum := 0.0
	for _, w := range weights {
		sum += w
	}
	if sum <= 0 {
		return weights
	}
	for i := range weights {
		weights[i] = weights[i] / sum
	}
	return weights
}

func eventVisible(ev ReplayEvent) bool {
	if ev.Visible == nil {
		return true
	}
	return *ev.Visible
}

func WriteAlertsJSONL(path string, alerts []detector.Alert) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := bufio.NewWriter(f)
	defer w.Flush()

	for _, a := range alerts {
		b, _ := json.Marshal(a)
		if _, err := w.WriteString(string(b) + "\n"); err != nil {
			return err
		}
	}
	return nil
}

func WriteTauSweepCSV(path string, rows []TauSweepMetrics) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := csv.NewWriter(f)
	defer w.Flush()

	head := []string{"method", "tau", "precision", "recall", "f1", "false_alerts_per_account_per_day", "mean_latency_ms", "p95_latency_ms", "p99_latency_ms", "throughput_tps", "lookup_mean_ms", "lookup_p95_ms", "lookup_p99_ms", "tp", "fp", "fn", "tn", "total_events", "protected_victims", "average_candidates", "positive_visible_events", "positive_detected_events", "positive_missed_no_candidate", "positive_missed_below_tau", "estimated_memory_per_1k_counterparties_kb"}
	if err := w.Write(head); err != nil {
		return err
	}
	for _, metrics := range rows {
		row := []string{
			metrics.Method,
			fmt.Sprintf("%.6f", metrics.Tau),
			fmt.Sprintf("%.6f", metrics.Precision),
			fmt.Sprintf("%.6f", metrics.Recall),
			fmt.Sprintf("%.6f", metrics.F1),
			fmt.Sprintf("%.6f", metrics.FalseAlertsPerAccDay),
			fmt.Sprintf("%.6f", metrics.MeanLatencyMs),
			fmt.Sprintf("%.6f", metrics.P95LatencyMs),
			fmt.Sprintf("%.6f", metrics.P99LatencyMs),
			fmt.Sprintf("%.6f", metrics.ThroughputTPS),
			fmt.Sprintf("%.6f", metrics.LookupMeanMs),
			fmt.Sprintf("%.6f", metrics.LookupP95Ms),
			fmt.Sprintf("%.6f", metrics.LookupP99Ms),
			fmt.Sprintf("%d", metrics.TP),
			fmt.Sprintf("%d", metrics.FP),
			fmt.Sprintf("%d", metrics.FN),
			fmt.Sprintf("%d", metrics.TN),
			fmt.Sprintf("%d", metrics.TotalEvents),
			fmt.Sprintf("%d", metrics.ProtectedVictims),
			fmt.Sprintf("%.6f", metrics.AverageCandidates),
			fmt.Sprintf("%d", metrics.PositiveVisible),
			fmt.Sprintf("%d", metrics.PositiveDetected),
			fmt.Sprintf("%d", metrics.MissedNoCandidate),
			fmt.Sprintf("%d", metrics.MissedBelowTau),
			fmt.Sprintf("%.6f", metrics.EstimatedMemoryPer1k),
		}
		if err := w.Write(row); err != nil {
			return err
		}
	}
	return nil
}

func WriteMetricsCSV(path string, metrics MethodMetrics) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := csv.NewWriter(f)
	defer w.Flush()

	head := []string{"method", "precision", "recall", "f1", "false_alerts_per_account_per_day", "mean_latency_ms", "p95_latency_ms", "p99_latency_ms", "throughput_tps", "lookup_mean_ms", "lookup_p95_ms", "lookup_p99_ms", "tp", "fp", "fn", "tn", "total_events", "protected_victims", "average_candidates", "positive_visible_events", "positive_detected_events", "positive_missed_no_candidate", "positive_missed_below_tau", "estimated_memory_per_1k_counterparties_kb"}
	row := []string{
		metrics.Method,
		fmt.Sprintf("%.6f", metrics.Precision),
		fmt.Sprintf("%.6f", metrics.Recall),
		fmt.Sprintf("%.6f", metrics.F1),
		fmt.Sprintf("%.6f", metrics.FalseAlertsPerAccDay),
		fmt.Sprintf("%.6f", metrics.MeanLatencyMs),
		fmt.Sprintf("%.6f", metrics.P95LatencyMs),
		fmt.Sprintf("%.6f", metrics.P99LatencyMs),
		fmt.Sprintf("%.6f", metrics.ThroughputTPS),
		fmt.Sprintf("%.6f", metrics.LookupMeanMs),
		fmt.Sprintf("%.6f", metrics.LookupP95Ms),
		fmt.Sprintf("%.6f", metrics.LookupP99Ms),
		fmt.Sprintf("%d", metrics.TP),
		fmt.Sprintf("%d", metrics.FP),
		fmt.Sprintf("%d", metrics.FN),
		fmt.Sprintf("%d", metrics.TN),
		fmt.Sprintf("%d", metrics.TotalEvents),
		fmt.Sprintf("%d", metrics.ProtectedVictims),
		fmt.Sprintf("%.6f", metrics.AverageCandidates),
		fmt.Sprintf("%d", metrics.PositiveVisible),
		fmt.Sprintf("%d", metrics.PositiveDetected),
		fmt.Sprintf("%d", metrics.MissedNoCandidate),
		fmt.Sprintf("%d", metrics.MissedBelowTau),
		fmt.Sprintf("%.6f", metrics.EstimatedMemoryPer1k),
	}
	if err := w.Write(head); err != nil {
		return err
	}
	if err := w.Write(row); err != nil {
		return err
	}
	return nil
}

func WriteDailyMetricsCSV(path string, rows []DailyMetrics) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := csv.NewWriter(f)
	defer w.Flush()

	head := []string{"method", "day", "precision", "recall", "f1", "false_alerts_per_account_per_day", "mean_latency_ms", "throughput_tps", "lookup_mean_ms", "tp", "fp", "fn", "tn", "total_events", "protected_victims", "average_candidates", "positive_visible_events", "positive_detected_events", "positive_missed_no_candidate", "positive_missed_below_tau"}
	if err := w.Write(head); err != nil {
		return err
	}
	for _, metrics := range rows {
		row := []string{
			metrics.Method,
			metrics.Day,
			fmt.Sprintf("%.6f", metrics.Precision),
			fmt.Sprintf("%.6f", metrics.Recall),
			fmt.Sprintf("%.6f", metrics.F1),
			fmt.Sprintf("%.6f", metrics.FalseAlertsPerAccDay),
			fmt.Sprintf("%.6f", metrics.MeanLatencyMs),
			fmt.Sprintf("%.6f", metrics.ThroughputTPS),
			fmt.Sprintf("%.6f", metrics.LookupMeanMs),
			fmt.Sprintf("%d", metrics.TP),
			fmt.Sprintf("%d", metrics.FP),
			fmt.Sprintf("%d", metrics.FN),
			fmt.Sprintf("%d", metrics.TN),
			fmt.Sprintf("%d", metrics.TotalEvents),
			fmt.Sprintf("%d", metrics.ProtectedVictims),
			fmt.Sprintf("%.6f", metrics.AverageCandidates),
			fmt.Sprintf("%d", metrics.PositiveVisible),
			fmt.Sprintf("%d", metrics.PositiveDetected),
			fmt.Sprintf("%d", metrics.MissedNoCandidate),
			fmt.Sprintf("%d", metrics.MissedBelowTau),
		}
		if err := w.Write(row); err != nil {
			return err
		}
	}
	return nil
}

func safeDiv(a, b float64) float64 {
	if b == 0 {
		return 0
	}
	return a / b
}

func mean(xs []float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	sum := 0.0
	for _, x := range xs {
		sum += x
	}
	return sum / float64(len(xs))
}

func percentile(xs []float64, p float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	cp := make([]float64, len(xs))
	copy(cp, xs)
	sort.Float64s(cp)
	if len(cp) == 1 {
		return cp[0]
	}
	idx := (p / 100) * float64(len(cp)-1)
	low := int(math.Floor(idx))
	high := int(math.Ceil(idx))
	if low == high {
		return cp[low]
	}
	frac := idx - float64(low)
	return cp[low] + frac*(cp[high]-cp[low])
}

func estimateMemoryPer1k(cps []detector.Counterparty) float64 {
	if len(cps) == 0 {
		return 0
	}
	// rough estimate: address + metadata maps.
	bytesPerCounterparty := 40 + 40 + 40 + 64
	return float64(bytesPerCounterparty*1000) / 1024.0
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
