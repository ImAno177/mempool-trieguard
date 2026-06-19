package live

import (
	"context"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"sort"
	"strconv"
	"strings"
	"time"

	"mempool-trieguard/internal/config"
	"mempool-trieguard/internal/detector"
	"mempool-trieguard/internal/rpc"
	"mempool-trieguard/internal/store"
)

const (
	liveBenchmarkWarmupDuration = 60 * time.Second
	liveBenchmarkWarmupBlocks   = 5
	liveBenchmarkMinBlocks      = 100
	liveBenchmarkStateRetention = 6 * time.Hour
	liveBenchmarkFlushInterval  = 30 * time.Second
)

type MicroBenchmarkSummary struct {
	StartedAt                       time.Time `json:"started_at"`
	EndedAt                         time.Time `json:"ended_at"`
	DurationSeconds                 float64   `json:"duration_seconds"`
	SubscriptionName                string    `json:"subscription_name"`
	SubscriptionID                  string    `json:"subscription_id"`
	SubscriptionIDs                 []string  `json:"subscription_ids,omitempty"`
	SubscriptionReconnects          int64     `json:"subscription_reconnects"`
	SubscriptionErrors              int64     `json:"subscription_errors"`
	WarmupDurationSeconds           float64   `json:"warmup_duration_seconds"`
	WarmupBlockCount                int       `json:"warmup_block_count"`
	StateRetentionSeconds           float64   `json:"state_retention_seconds"`
	ArtifactFlushIntervalSeconds    float64   `json:"artifact_flush_interval_seconds"`
	ArtifactFlushes                 int64     `json:"artifact_flushes"`
	WarmupCompleted                 bool      `json:"warmup_completed"`
	WarmupEndedAt                   time.Time `json:"warmup_ended_at,omitempty"`
	WarmupSkippedBlocks             int64     `json:"warmup_skipped_blocks"`
	AcceptanceMinBlocks             int       `json:"acceptance_min_blocks"`
	VisibilityValid                 bool      `json:"visibility_valid"`
	VisibilityInvalidReason         string    `json:"visibility_invalid_reason,omitempty"`
	PendingMessages                 int64     `json:"pending_messages"`
	PendingMessagesPerSecond        float64   `json:"pending_messages_per_second"`
	PendingInterarrivalMeanMs       float64   `json:"pending_interarrival_mean_ms"`
	PendingInterarrivalP50Ms        float64   `json:"pending_interarrival_p50_ms"`
	PendingInterarrivalP95Ms        float64   `json:"pending_interarrival_p95_ms"`
	PendingInterarrivalP99Ms        float64   `json:"pending_interarrival_p99_ms"`
	HashPayloadMessages             int64     `json:"hash_payload_messages"`
	ObjectPayloadMessages           int64     `json:"object_payload_messages"`
	DecodeErrors                    int64     `json:"decode_errors"`
	SubscriptionDroppedMessages     int64     `json:"subscription_dropped_messages"`
	PendingStateRetained            int64     `json:"pending_state_retained"`
	PendingStatePruned              int64     `json:"pending_state_pruned"`
	FetchAttempted                  int64     `json:"fetch_attempted"`
	FetchSuccess                    int64     `json:"fetch_success"`
	FetchFailed                     int64     `json:"fetch_failed"`
	FetchLatencyMeanMs              float64   `json:"fetch_latency_mean_ms"`
	FetchLatencyP50Ms               float64   `json:"fetch_latency_p50_ms"`
	FetchLatencyP95Ms               float64   `json:"fetch_latency_p95_ms"`
	FetchLatencyP99Ms               float64   `json:"fetch_latency_p99_ms"`
	ERC20TransferCalls              int64     `json:"erc20_transfer_calls"`
	ERC20TransferCallsPerSecond     float64   `json:"erc20_transfer_calls_per_second"`
	DetectorEvents                  int64     `json:"detector_events"`
	DetectorEventsPerSecond         float64   `json:"detector_events_per_second"`
	DetectorAlerts                  int64     `json:"detector_alerts"`
	TelegramConfigured              bool      `json:"telegram_configured"`
	TelegramAlertsAttempted         int64     `json:"telegram_alerts_attempted"`
	TelegramAlertsSent              int64     `json:"telegram_alerts_sent"`
	TelegramAlertsFailed            int64     `json:"telegram_alerts_failed"`
	TelegramSendLatencyMeanMs       float64   `json:"telegram_send_latency_mean_ms"`
	TelegramSendLatencyP50Ms        float64   `json:"telegram_send_latency_p50_ms"`
	TelegramSendLatencyP95Ms        float64   `json:"telegram_send_latency_p95_ms"`
	TelegramSendLatencyP99Ms        float64   `json:"telegram_send_latency_p99_ms"`
	DetectorToTelegramMeanMs        float64   `json:"detector_to_telegram_accept_mean_ms"`
	DetectorToTelegramP50Ms         float64   `json:"detector_to_telegram_accept_p50_ms"`
	DetectorToTelegramP95Ms         float64   `json:"detector_to_telegram_accept_p95_ms"`
	DetectorToTelegramP99Ms         float64   `json:"detector_to_telegram_accept_p99_ms"`
	PendingToTelegramMeanMs         float64   `json:"pending_to_telegram_accept_mean_ms"`
	PendingToTelegramP50Ms          float64   `json:"pending_to_telegram_accept_p50_ms"`
	PendingToTelegramP95Ms          float64   `json:"pending_to_telegram_accept_p95_ms"`
	PendingToTelegramP99Ms          float64   `json:"pending_to_telegram_accept_p99_ms"`
	DetectorLatencyMeanMs           float64   `json:"detector_latency_mean_ms"`
	DetectorLatencyP50Ms            float64   `json:"detector_latency_p50_ms"`
	DetectorLatencyP95Ms            float64   `json:"detector_latency_p95_ms"`
	DetectorLatencyP99Ms            float64   `json:"detector_latency_p99_ms"`
	DetectorLatencyMeanUs           float64   `json:"detector_latency_mean_us"`
	DetectorLatencyP50Us            float64   `json:"detector_latency_p50_us"`
	DetectorLatencyP95Us            float64   `json:"detector_latency_p95_us"`
	DetectorLatencyP99Us            float64   `json:"detector_latency_p99_us"`
	DetectorLatencyMeanNs           float64   `json:"detector_latency_mean_ns"`
	DetectorLatencyP50Ns            float64   `json:"detector_latency_p50_ns"`
	DetectorLatencyP95Ns            float64   `json:"detector_latency_p95_ns"`
	DetectorLatencyP99Ns            float64   `json:"detector_latency_p99_ns"`
	LookupLatencyMeanMs             float64   `json:"lookup_latency_mean_ms"`
	LookupLatencyP50Ms              float64   `json:"lookup_latency_p50_ms"`
	LookupLatencyP95Ms              float64   `json:"lookup_latency_p95_ms"`
	LookupLatencyP99Ms              float64   `json:"lookup_latency_p99_ms"`
	LookupLatencyMeanUs             float64   `json:"lookup_latency_mean_us"`
	LookupLatencyP50Us              float64   `json:"lookup_latency_p50_us"`
	LookupLatencyP95Us              float64   `json:"lookup_latency_p95_us"`
	LookupLatencyP99Us              float64   `json:"lookup_latency_p99_us"`
	LookupLatencyMeanNs             float64   `json:"lookup_latency_mean_ns"`
	LookupLatencyP50Ns              float64   `json:"lookup_latency_p50_ns"`
	LookupLatencyP95Ns              float64   `json:"lookup_latency_p95_ns"`
	LookupLatencyP99Ns              float64   `json:"lookup_latency_p99_ns"`
	CandidatesScoredMean            float64   `json:"candidates_scored_mean"`
	SenderNonceGroups               int64     `json:"sender_nonce_groups"`
	SenderNonceGroupsRetained       int64     `json:"sender_nonce_groups_retained"`
	SenderNonceGroupsPruned         int64     `json:"sender_nonce_groups_pruned"`
	ReplacementCandidateMessages    int64     `json:"replacement_candidate_messages"`
	ReplacementCandidateGroups      int64     `json:"replacement_candidate_groups"`
	BlockPolls                      int64     `json:"block_polls"`
	BlockPollErrors                 int64     `json:"block_poll_errors"`
	SequentialBlockFetches          int64     `json:"sequential_block_fetches"`
	BlocksObserved                  int64     `json:"blocks_observed"`
	IncludedTransactions            int64     `json:"included_transactions"`
	IncludedSeenPending             int64     `json:"included_seen_pending"`
	IncludedSeenPendingRate         float64   `json:"included_seen_pending_rate"`
	IncludedVisibilityLossRate      float64   `json:"included_visibility_loss_rate"`
	IncludedERC20Transfers          int64     `json:"included_erc20_transfers"`
	IncludedERC20SeenPending        int64     `json:"included_erc20_seen_pending"`
	IncludedERC20SeenPendingRate    float64   `json:"included_erc20_seen_pending_rate"`
	IncludedERC20VisibilityLossRate float64   `json:"included_erc20_visibility_loss_rate"`
	PendingToBlockLeadMeanMs        float64   `json:"pending_to_block_timestamp_lead_mean_ms"`
	PendingToBlockLeadP50Ms         float64   `json:"pending_to_block_timestamp_lead_p50_ms"`
	PendingToBlockLeadP95Ms         float64   `json:"pending_to_block_timestamp_lead_p95_ms"`
	PendingToBlockLeadP99Ms         float64   `json:"pending_to_block_timestamp_lead_p99_ms"`
	Artifacts                       []string  `json:"artifacts"`
}

type microEventRow struct {
	ObservedAt            time.Time
	TxHash                string
	From                  string
	Nonce                 string
	PayloadKind           string
	FetchOK               bool
	FetchAttempts         int
	FetchLatencyMs        float64
	ERC20                 bool
	ERC20Method           string
	DetectorCompletedAt   time.Time
	DetectorLatencyMs     float64
	DetectorLatencyUs     float64
	DetectorLatencyNs     int64
	LookupMeanMs          float64
	LookupMeanUs          float64
	LookupMeanNs          int64
	LookupP95Ms           float64
	LookupP95Us           float64
	LookupP95Ns           int64
	PendingInterarrivalMs float64
	CandidatesScored      int
	Alerts                int
	ReplacementCandidate  bool
	Gas                   string
	GasPrice              string
	MaxFeePerGas          string
	MaxPriorityFeePerGas  string
	Error                 string
}

type microBlockRow struct {
	ObservedAt               time.Time
	BlockTimestamp           time.Time
	BlockNumber              uint64
	BlockHash                string
	TxTotal                  int
	TxSeenPending            int
	ERC20TransferTotal       int
	ERC20TransferSeenPending int
	PendingToBlockLeadMeanMs float64
	PendingToBlockLeadP50Ms  float64
	PendingToBlockLeadP95Ms  float64
	PendingToBlockLeadP99Ms  float64
	pendingToBlockLeadMs     []float64
}

type liveAlertRecord struct {
	Alert               detector.Alert       `json:"alert"`
	DetectorCompletedAt time.Time            `json:"detector_completed_at"`
	DetectorLatencyMs   float64              `json:"detector_latency_ms"`
	Telegram            *telegramSendReceipt `json:"telegram,omitempty"`
}

type runManifest struct {
	StartedAt                  time.Time `json:"started_at"`
	EndedAt                    time.Time `json:"ended_at"`
	DurationSeconds            float64   `json:"duration_seconds"`
	ProviderHTTPHost           string    `json:"provider_http_host,omitempty"`
	ProviderWSSHost            string    `json:"provider_wss_host,omitempty"`
	SubscriptionName           string    `json:"subscription_name"`
	SubscriptionID             string    `json:"subscription_id"`
	SubscriptionIDs            []string  `json:"subscription_ids,omitempty"`
	SubscriptionReconnects     int64     `json:"subscription_reconnects"`
	SubscriptionErrors         int64     `json:"subscription_errors"`
	RunRegion                  string    `json:"run_region,omitempty"`
	HostName                   string    `json:"host_name,omitempty"`
	GoVersion                  string    `json:"go_version"`
	GOOS                       string    `json:"goos"`
	GOARCH                     string    `json:"goarch"`
	GitRevision                string    `json:"git_revision,omitempty"`
	GitModified                string    `json:"git_modified,omitempty"`
	ConfigHashSHA256           string    `json:"config_hash_sha256"`
	ProtectedAccountsPath      string    `json:"protected_accounts_path"`
	ProtectedAccountsSHA256    string    `json:"protected_accounts_sha256,omitempty"`
	ProtectedAccountsHashError string    `json:"protected_accounts_hash_error,omitempty"`
	WarmupDurationSeconds      float64   `json:"warmup_duration_seconds"`
	WarmupBlockCount           int       `json:"warmup_block_count"`
	StateRetentionSeconds      float64   `json:"state_retention_seconds"`
	ArtifactFlushIntervalSecs  float64   `json:"artifact_flush_interval_seconds"`
	AcceptanceMinBlocks        int       `json:"acceptance_min_blocks"`
	VisibilityValid            bool      `json:"visibility_valid"`
	VisibilityInvalidReason    string    `json:"visibility_invalid_reason,omitempty"`
	TelegramConfigured         bool      `json:"telegram_configured"`
	TelegramReceiptSemantics   string    `json:"telegram_receipt_semantics,omitempty"`
}

type senderNonceKey struct {
	from  string
	nonce string
}

type senderNonceObservation struct {
	hash       string
	observedAt time.Time
}

func RunMicroBenchmark(ctx context.Context, cfg config.AppConfig, st *store.Store, duration time.Duration, outDir string) (MicroBenchmarkSummary, error) {
	if duration <= 0 {
		duration = 10 * time.Minute
	}
	if outDir == "" {
		outDir = filepath.Join("results", "live_mempool_"+time.Now().UTC().Format("20060102_150405"))
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return MicroBenchmarkSummary{}, err
	}

	eng, err := buildEngineFromFile(cfg)
	if err != nil {
		return MicroBenchmarkSummary{}, err
	}
	client := rpc.NewClient(cfg.DRPC.HTTPURL, cfg.DRPC.WSSURL, cfg.DRPC.Key)

	setupCtx, setupCancel := context.WithTimeout(ctx, 30*time.Second)
	sub, err := client.Subscribe(setupCtx, cfg.Live.SubscriptionName)
	setupCancel()
	if err != nil {
		return MicroBenchmarkSummary{}, err
	}
	defer func() {
		if sub != nil {
			sub.Close()
		}
	}()

	eventsPath := filepath.Join(outDir, "live_mempool_events.csv")
	blocksPath := filepath.Join(outDir, "live_mempool_blocks.csv")
	alertsPath := filepath.Join(outDir, "live_mempool_alerts.jsonl")
	manifestPath := filepath.Join(outDir, "run_manifest.json")
	eventsFile, err := os.Create(eventsPath)
	if err != nil {
		return MicroBenchmarkSummary{}, err
	}
	defer eventsFile.Close()
	blocksFile, err := os.Create(blocksPath)
	if err != nil {
		return MicroBenchmarkSummary{}, err
	}
	defer blocksFile.Close()
	alertsFile, err := os.Create(alertsPath)
	if err != nil {
		return MicroBenchmarkSummary{}, err
	}
	defer alertsFile.Close()

	eventCSV := csv.NewWriter(eventsFile)
	blockCSV := csv.NewWriter(blocksFile)
	defer eventCSV.Flush()
	defer blockCSV.Flush()
	writeEventHeader(eventCSV)
	writeBlockHeader(blockCSV)
	alertEncoder := json.NewEncoder(alertsFile)
	notifier := newTelegramNotifierFromEnv()
	telegramConfigured := notifier != nil

	started := time.Now().UTC()
	runCtx, cancel := context.WithTimeout(ctx, duration)
	defer cancel()
	summary := MicroBenchmarkSummary{
		StartedAt:                    started,
		SubscriptionName:             cfg.Live.SubscriptionName,
		SubscriptionID:               sub.SubscriptionID,
		SubscriptionIDs:              []string{sub.SubscriptionID},
		WarmupDurationSeconds:        liveBenchmarkWarmupDuration.Seconds(),
		WarmupBlockCount:             liveBenchmarkWarmupBlocks,
		StateRetentionSeconds:        liveBenchmarkStateRetention.Seconds(),
		ArtifactFlushIntervalSeconds: liveBenchmarkFlushInterval.Seconds(),
		AcceptanceMinBlocks:          liveBenchmarkMinBlocks,
		TelegramConfigured:           telegramConfigured,
		Artifacts:                    []string{eventsPath, blocksPath, alertsPath, manifestPath},
	}
	seenPending := map[string]time.Time{}
	seenBlocks := map[string]struct{}{}
	senderNonceFirstHash := map[senderNonceKey]senderNonceObservation{}
	replacementGroups := map[senderNonceKey]struct{}{}
	fetchLatencies := []float64{}
	detectLatencies := []float64{}
	lookupLatencies := []float64{}
	pendingInterarrivals := []float64{}
	pendingToBlockLeadTimes := []float64{}
	candidates := []float64{}
	telegramSendLatencies := []float64{}
	detectorToTelegramLatencies := []float64{}
	pendingToTelegramLatencies := []float64{}
	var lastPendingAt time.Time
	var lastBlockNumber uint64
	var allBlockOrdinal int64
	blockTicker := time.NewTicker(5 * time.Second)
	defer blockTicker.Stop()
	flushTicker := time.NewTicker(liveBenchmarkFlushInterval)
	defer flushTicker.Stop()
	var cumulativeDropped int64

	for {
		select {
		case <-runCtx.Done():
			summary.EndedAt = time.Now().UTC()
			summary.DurationSeconds = summary.EndedAt.Sub(started).Seconds()
			summary.SubscriptionDroppedMessages = cumulativeDropped + sub.DroppedCount()
			summary.ReplacementCandidateGroups = int64(len(replacementGroups))
			summary.PendingStateRetained = int64(len(seenPending))
			summary.SenderNonceGroupsRetained = int64(len(senderNonceFirstHash))
			finalizeMicroSummary(&summary, fetchLatencies, detectLatencies, lookupLatencies, candidates, pendingInterarrivals, pendingToBlockLeadTimes, telegramSendLatencies, detectorToTelegramLatencies, pendingToTelegramLatencies)
			summaryPath := filepath.Join(outDir, "live_mempool_metrics.json")
			summary.Artifacts = append(summary.Artifacts, summaryPath)
			if err := flushMicroCSVs(eventCSV, blockCSV); err != nil {
				return summary, err
			}
			manifest := buildRunManifest(cfg, summary)
			mb, _ := json.MarshalIndent(manifest, "", "  ")
			if err := os.WriteFile(manifestPath, mb, 0o644); err != nil {
				return summary, err
			}
			b, _ := json.MarshalIndent(summary, "", "  ")
			if err := os.WriteFile(summaryPath, b, 0o644); err != nil {
				return summary, err
			}
			if err := saveMicroRun(st, summary, outDir); err != nil {
				return summary, err
			}
			return summary, nil
		case err := <-sub.Errors:
			if err != nil {
				if runCtx.Err() != nil || contextEndingSoon(runCtx, 2*time.Second) {
					continue
				}
				nextSub, dropped, err := reconnectMicroSubscription(runCtx, client, cfg.Live.SubscriptionName, sub)
				cumulativeDropped += dropped
				summary.SubscriptionErrors++
				if err != nil {
					return summary, err
				}
				sub = nextSub
				summary.SubscriptionReconnects++
				summary.SubscriptionIDs = append(summary.SubscriptionIDs, sub.SubscriptionID)
				continue
			}
		case raw, ok := <-sub.RawMessages:
			if !ok {
				if runCtx.Err() != nil || contextEndingSoon(runCtx, 2*time.Second) {
					continue
				}
				nextSub, dropped, err := reconnectMicroSubscription(runCtx, client, cfg.Live.SubscriptionName, sub)
				cumulativeDropped += dropped
				summary.SubscriptionErrors++
				if err != nil {
					return summary, err
				}
				sub = nextSub
				summary.SubscriptionReconnects++
				summary.SubscriptionIDs = append(summary.SubscriptionIDs, sub.SubscriptionID)
				continue
			}
			row, alerts := processMicroPending(runCtx, client, eng, raw, sub.SubscriptionID)
			if !lastPendingAt.IsZero() {
				row.PendingInterarrivalMs = row.ObservedAt.Sub(lastPendingAt).Seconds() * 1000
				if row.PendingInterarrivalMs >= 0 {
					pendingInterarrivals = append(pendingInterarrivals, row.PendingInterarrivalMs)
				}
			}
			lastPendingAt = row.ObservedAt
			summary.PendingMessages++
			if row.PayloadKind == "hash" {
				summary.HashPayloadMessages++
			} else if row.PayloadKind == "object" {
				summary.ObjectPayloadMessages++
			}
			if row.Error != "" {
				if row.TxHash == "" {
					summary.DecodeErrors++
				}
				if !row.FetchOK && row.FetchAttempts > 0 {
					summary.FetchFailed++
				}
			}
			if row.FetchAttempts > 0 {
				summary.FetchAttempted++
				if row.FetchOK {
					summary.FetchSuccess++
					fetchLatencies = append(fetchLatencies, row.FetchLatencyMs)
				}
			}
			if row.ERC20 {
				summary.ERC20TransferCalls++
			}
			if row.DetectorLatencyMs > 0 || row.Alerts > 0 {
				summary.DetectorEvents++
				detectLatencies = append(detectLatencies, row.DetectorLatencyMs)
				if row.LookupMeanMs > 0 {
					lookupLatencies = append(lookupLatencies, row.LookupMeanMs)
				}
				candidates = append(candidates, float64(row.CandidatesScored))
			}
			summary.DetectorAlerts += int64(row.Alerts)
			if row.TxHash != "" {
				seenPending[normalizeHash(row.TxHash)] = row.ObservedAt
			}
			if row.From != "" && row.Nonce != "" && row.TxHash != "" {
				key := senderNonceKey{from: normalizeAddress(row.From), nonce: normalizeHexQuantity(row.Nonce)}
				if key.from != "" && key.nonce != "" {
					hash := normalizeHash(row.TxHash)
					if first, ok := senderNonceFirstHash[key]; !ok {
						senderNonceFirstHash[key] = senderNonceObservation{hash: hash, observedAt: row.ObservedAt}
						summary.SenderNonceGroups++
					} else if first.hash != hash {
						row.ReplacementCandidate = true
						summary.ReplacementCandidateMessages++
						replacementGroups[key] = struct{}{}
					}
				}
			}
			writeEventRow(eventCSV, row)
			if len(alerts) > 0 {
				if st != nil {
					_ = st.SaveAlerts(alerts)
				}
				for _, alert := range alerts {
					record := liveAlertRecord{
						Alert:               alert,
						DetectorCompletedAt: row.DetectorCompletedAt.UTC(),
						DetectorLatencyMs:   row.DetectorLatencyMs,
					}
					if notifier != nil {
						receipt := notifier.NotifyAlert(runCtx, alert, row.DetectorCompletedAt)
						record.Telegram = &receipt
						summary.TelegramAlertsAttempted++
						if receipt.OK {
							summary.TelegramAlertsSent++
							telegramSendLatencies = append(telegramSendLatencies, receipt.SendLatencyMs)
							detectorToTelegramLatencies = append(detectorToTelegramLatencies, receipt.DetectorToTelegramAcceptMs)
							pendingToTelegramLatencies = append(pendingToTelegramLatencies, receipt.PendingToTelegramAcceptMs)
						} else {
							summary.TelegramAlertsFailed++
						}
					}
					_ = alertEncoder.Encode(record)
				}
			}
		case <-blockTicker.C:
			summary.BlockPolls++
			blockCtx, cancel := context.WithTimeout(runCtx, 15*time.Second)
			blocks, nextLastBlockNumber, fetchErrs := fetchSequentialBlocks(blockCtx, client, lastBlockNumber)
			cancel()
			if fetchErrs > 0 {
				summary.BlockPollErrors += int64(fetchErrs)
			}
			if len(blocks) == 0 {
				continue
			}
			lastBlockNumber = nextLastBlockNumber
			summary.SequentialBlockFetches += int64(len(blocks))
			for _, block := range blocks {
				if block.Hash == "" {
					continue
				}
				if _, ok := seenBlocks[block.Hash]; ok {
					continue
				}
				seenBlocks[block.Hash] = struct{}{}
				row := summarizeBlock(block, seenPending)
				allBlockOrdinal++
				if shouldSkipWarmupBlock(started, row.ObservedAt, allBlockOrdinal) {
					summary.WarmupSkippedBlocks++
					continue
				}
				if !summary.WarmupCompleted {
					summary.WarmupCompleted = true
					summary.WarmupEndedAt = row.ObservedAt
				}
				recordVisibilityBlock(&summary, row, &pendingToBlockLeadTimes)
				writeBlockRow(blockCSV, row)
			}
			pruneMicroState(time.Now().UTC(), seenPending, senderNonceFirstHash, &summary)
		case <-flushTicker.C:
			if err := flushMicroCSVs(eventCSV, blockCSV); err != nil {
				return summary, err
			}
			summary.ArtifactFlushes++
		}
	}
}

func processMicroPending(ctx context.Context, client *rpc.Client, eng *detector.Engine, raw json.RawMessage, subID string) (microEventRow, []detector.Alert) {
	row := microEventRow{ObservedAt: time.Now().UTC()}
	hash, txObj, hasObj, err := rpc.DecodePendingResult(raw)
	if err != nil {
		row.Error = err.Error()
		return row, nil
	}
	row.TxHash = hash
	tx := txObj
	if hasObj {
		row.PayloadKind = "object"
	} else {
		row.PayloadKind = "hash"
		fetched, attempts, latencyMs, err := getPendingTransactionWithRetry(ctx, client, hash)
		row.FetchAttempts = attempts
		row.FetchLatencyMs = latencyMs
		if err != nil {
			row.Error = err.Error()
			return row, nil
		}
		row.FetchOK = true
		tx = fetched
	}
	if tx.Hash != "" {
		row.TxHash = tx.Hash
	}
	row.From = tx.From
	row.Nonce = tx.Nonce
	row.Gas = tx.Gas
	row.GasPrice = tx.GasPrice
	row.MaxFeePerGas = tx.MaxFeePerGas
	row.MaxPriorityFeePerGas = tx.MaxPriorityFeePerGas
	pending, erc20, method := pendingFromRPCTransaction(tx, row.ObservedAt)
	row.ERC20 = erc20
	row.ERC20Method = method
	started := time.Now()
	alerts, perf := eng.Detect(pending)
	completed := time.Now()
	elapsed := completed.Sub(started)
	row.DetectorCompletedAt = completed
	row.DetectorLatencyNs = elapsed.Nanoseconds()
	row.DetectorLatencyUs = float64(row.DetectorLatencyNs) / 1000
	row.DetectorLatencyMs = float64(row.DetectorLatencyNs) / 1_000_000
	row.LookupMeanMs, row.LookupP95Ms, row.CandidatesScored = summarizePerf(perf)
	row.LookupMeanNs = msToNs(row.LookupMeanMs)
	row.LookupMeanUs = nsToUs(row.LookupMeanNs)
	row.LookupP95Ns = msToNs(row.LookupP95Ms)
	row.LookupP95Us = nsToUs(row.LookupP95Ns)
	row.Alerts = len(alerts)
	for i := range alerts {
		alerts[i].SubscriptionTrace = subID
	}
	return row, alerts
}

func getPendingTransactionWithRetry(ctx context.Context, client *rpc.Client, hash string) (rpc.RPCTransaction, int, float64, error) {
	started := time.Now()
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		tx, err := client.GetTransactionByHash(ctx, hash)
		if err == nil && tx.Hash != "" {
			return tx, attempt + 1, time.Since(started).Seconds() * 1000, nil
		}
		if err != nil {
			lastErr = err
		} else {
			lastErr = fmt.Errorf("empty tx result")
		}
		select {
		case <-ctx.Done():
			return rpc.RPCTransaction{}, attempt + 1, time.Since(started).Seconds() * 1000, ctx.Err()
		case <-time.After(time.Duration(150*(attempt+1)) * time.Millisecond):
		}
	}
	return rpc.RPCTransaction{}, 3, time.Since(started).Seconds() * 1000, lastErr
}

func summarizePerf(perf []detector.PerfRecord) (float64, float64, int) {
	if len(perf) == 0 {
		return 0, 0, 0
	}
	latencies := make([]float64, 0, len(perf))
	candidates := 0
	for _, rec := range perf {
		latencies = append(latencies, rec.LookupLatencyMs)
		candidates += rec.CandidatesScored
	}
	return mean(latencies), percentile(latencies, 95), candidates
}

func summarizeBlock(block rpc.RPCBlock, seenPending map[string]time.Time) microBlockRow {
	number, _ := parseHexUint64Local(block.Number)
	timestamp, _ := parseHexUint64Local(block.Timestamp)
	row := microBlockRow{ObservedAt: time.Now().UTC(), BlockNumber: number, BlockHash: block.Hash, TxTotal: len(block.Transactions)}
	if timestamp > 0 {
		row.BlockTimestamp = time.Unix(int64(timestamp), 0).UTC()
	}
	for _, tx := range block.Transactions {
		seen := false
		if seenAt, ok := seenPending[normalizeHash(tx.Hash)]; ok {
			row.TxSeenPending++
			seen = true
			if !row.BlockTimestamp.IsZero() {
				row.pendingToBlockLeadMs = append(row.pendingToBlockLeadMs, row.BlockTimestamp.Sub(seenAt).Seconds()*1000)
			}
		}
		if _, ok := rpc.ParseERC20TransferCall(tx.Input); ok {
			row.ERC20TransferTotal++
			if seen {
				row.ERC20TransferSeenPending++
			}
		}
	}
	row.PendingToBlockLeadMeanMs = mean(row.pendingToBlockLeadMs)
	row.PendingToBlockLeadP50Ms = percentile(row.pendingToBlockLeadMs, 50)
	row.PendingToBlockLeadP95Ms = percentile(row.pendingToBlockLeadMs, 95)
	row.PendingToBlockLeadP99Ms = percentile(row.pendingToBlockLeadMs, 99)
	return row
}

func fetchSequentialBlocks(ctx context.Context, client *rpc.Client, lastBlockNumber uint64) ([]rpc.RPCBlock, uint64, int) {
	latest, err := client.GetBlockByNumber(ctx, "latest", true)
	if err != nil || latest.Hash == "" {
		return nil, lastBlockNumber, 1
	}
	latestNumber, err := parseHexUint64Local(latest.Number)
	if err != nil {
		return nil, lastBlockNumber, 1
	}
	if lastBlockNumber == 0 {
		return []rpc.RPCBlock{latest}, latestNumber, 0
	}
	numbers := sequentialBlockNumbers(lastBlockNumber, latestNumber)
	if len(numbers) == 0 {
		return nil, lastBlockNumber, 0
	}
	blocks := make([]rpc.RPCBlock, 0, len(numbers))
	lastProcessed := lastBlockNumber
	errs := 0
	for _, number := range numbers {
		block := latest
		if number != latestNumber {
			block, err = client.GetBlockByNumber(ctx, formatHexUint64(number), true)
			if err != nil || block.Hash == "" {
				errs++
				break
			}
		}
		blocks = append(blocks, block)
		lastProcessed = number
	}
	return blocks, lastProcessed, errs
}

func sequentialBlockNumbers(lastBlockNumber, latestNumber uint64) []uint64 {
	if latestNumber <= lastBlockNumber {
		return nil
	}
	numbers := make([]uint64, 0, latestNumber-lastBlockNumber)
	for number := lastBlockNumber + 1; number <= latestNumber; number++ {
		numbers = append(numbers, number)
	}
	return numbers
}

func shouldSkipWarmupBlock(startedAt, observedAt time.Time, blockOrdinal int64) bool {
	if blockOrdinal <= liveBenchmarkWarmupBlocks {
		return true
	}
	return observedAt.Sub(startedAt) < liveBenchmarkWarmupDuration
}

func recordVisibilityBlock(summary *MicroBenchmarkSummary, row microBlockRow, pendingToBlockLeadTimes *[]float64) {
	summary.BlocksObserved++
	summary.IncludedTransactions += int64(row.TxTotal)
	summary.IncludedSeenPending += int64(row.TxSeenPending)
	summary.IncludedERC20Transfers += int64(row.ERC20TransferTotal)
	summary.IncludedERC20SeenPending += int64(row.ERC20TransferSeenPending)
	*pendingToBlockLeadTimes = append(*pendingToBlockLeadTimes, row.pendingToBlockLeadMs...)
}

func reconnectMicroSubscription(ctx context.Context, client *rpc.Client, subscriptionName string, current *rpc.Subscription) (*rpc.Subscription, int64, error) {
	dropped := int64(0)
	if current != nil {
		dropped = current.DroppedCount()
		current.Close()
	}
	sub, err := client.Subscribe(ctx, subscriptionName)
	if err != nil {
		return nil, dropped, err
	}
	return sub, dropped, nil
}

func contextEndingSoon(ctx context.Context, threshold time.Duration) bool {
	deadline, ok := ctx.Deadline()
	return ok && time.Until(deadline) <= threshold
}

func pruneMicroState(now time.Time, seenPending map[string]time.Time, senderNonceFirstHash map[senderNonceKey]senderNonceObservation, summary *MicroBenchmarkSummary) {
	cutoff := now.Add(-liveBenchmarkStateRetention)
	for hash, seenAt := range seenPending {
		if seenAt.Before(cutoff) {
			delete(seenPending, hash)
			summary.PendingStatePruned++
		}
	}
	for key, seen := range senderNonceFirstHash {
		if seen.observedAt.Before(cutoff) {
			delete(senderNonceFirstHash, key)
			summary.SenderNonceGroupsPruned++
		}
	}
}

func finalizeMicroSummary(summary *MicroBenchmarkSummary, fetchLatencies, detectLatencies, lookupLatencies, candidates, pendingInterarrivals, pendingToBlockLeadTimes, telegramSendLatencies, detectorToTelegramLatencies, pendingToTelegramLatencies []float64) {
	if summary.DurationSeconds > 0 {
		summary.PendingMessagesPerSecond = float64(summary.PendingMessages) / summary.DurationSeconds
		summary.DetectorEventsPerSecond = float64(summary.DetectorEvents) / summary.DurationSeconds
		summary.ERC20TransferCallsPerSecond = float64(summary.ERC20TransferCalls) / summary.DurationSeconds
	}
	summary.PendingInterarrivalMeanMs = mean(pendingInterarrivals)
	summary.PendingInterarrivalP50Ms = percentile(pendingInterarrivals, 50)
	summary.PendingInterarrivalP95Ms = percentile(pendingInterarrivals, 95)
	summary.PendingInterarrivalP99Ms = percentile(pendingInterarrivals, 99)
	summary.FetchLatencyMeanMs = mean(fetchLatencies)
	summary.FetchLatencyP50Ms = percentile(fetchLatencies, 50)
	summary.FetchLatencyP95Ms = percentile(fetchLatencies, 95)
	summary.FetchLatencyP99Ms = percentile(fetchLatencies, 99)
	summary.TelegramSendLatencyMeanMs = mean(telegramSendLatencies)
	summary.TelegramSendLatencyP50Ms = percentile(telegramSendLatencies, 50)
	summary.TelegramSendLatencyP95Ms = percentile(telegramSendLatencies, 95)
	summary.TelegramSendLatencyP99Ms = percentile(telegramSendLatencies, 99)
	summary.DetectorToTelegramMeanMs = mean(detectorToTelegramLatencies)
	summary.DetectorToTelegramP50Ms = percentile(detectorToTelegramLatencies, 50)
	summary.DetectorToTelegramP95Ms = percentile(detectorToTelegramLatencies, 95)
	summary.DetectorToTelegramP99Ms = percentile(detectorToTelegramLatencies, 99)
	summary.PendingToTelegramMeanMs = mean(pendingToTelegramLatencies)
	summary.PendingToTelegramP50Ms = percentile(pendingToTelegramLatencies, 50)
	summary.PendingToTelegramP95Ms = percentile(pendingToTelegramLatencies, 95)
	summary.PendingToTelegramP99Ms = percentile(pendingToTelegramLatencies, 99)
	summary.DetectorLatencyMeanMs = mean(detectLatencies)
	summary.DetectorLatencyP50Ms = percentile(detectLatencies, 50)
	summary.DetectorLatencyP95Ms = percentile(detectLatencies, 95)
	summary.DetectorLatencyP99Ms = percentile(detectLatencies, 99)
	summary.DetectorLatencyMeanUs = msToUs(summary.DetectorLatencyMeanMs)
	summary.DetectorLatencyP50Us = msToUs(summary.DetectorLatencyP50Ms)
	summary.DetectorLatencyP95Us = msToUs(summary.DetectorLatencyP95Ms)
	summary.DetectorLatencyP99Us = msToUs(summary.DetectorLatencyP99Ms)
	summary.DetectorLatencyMeanNs = msToNsFloat(summary.DetectorLatencyMeanMs)
	summary.DetectorLatencyP50Ns = msToNsFloat(summary.DetectorLatencyP50Ms)
	summary.DetectorLatencyP95Ns = msToNsFloat(summary.DetectorLatencyP95Ms)
	summary.DetectorLatencyP99Ns = msToNsFloat(summary.DetectorLatencyP99Ms)
	summary.LookupLatencyMeanMs = mean(lookupLatencies)
	summary.LookupLatencyP50Ms = percentile(lookupLatencies, 50)
	summary.LookupLatencyP95Ms = percentile(lookupLatencies, 95)
	summary.LookupLatencyP99Ms = percentile(lookupLatencies, 99)
	summary.LookupLatencyMeanUs = msToUs(summary.LookupLatencyMeanMs)
	summary.LookupLatencyP50Us = msToUs(summary.LookupLatencyP50Ms)
	summary.LookupLatencyP95Us = msToUs(summary.LookupLatencyP95Ms)
	summary.LookupLatencyP99Us = msToUs(summary.LookupLatencyP99Ms)
	summary.LookupLatencyMeanNs = msToNsFloat(summary.LookupLatencyMeanMs)
	summary.LookupLatencyP50Ns = msToNsFloat(summary.LookupLatencyP50Ms)
	summary.LookupLatencyP95Ns = msToNsFloat(summary.LookupLatencyP95Ms)
	summary.LookupLatencyP99Ns = msToNsFloat(summary.LookupLatencyP99Ms)
	summary.CandidatesScoredMean = mean(candidates)
	summary.PendingToBlockLeadMeanMs = mean(pendingToBlockLeadTimes)
	summary.PendingToBlockLeadP50Ms = percentile(pendingToBlockLeadTimes, 50)
	summary.PendingToBlockLeadP95Ms = percentile(pendingToBlockLeadTimes, 95)
	summary.PendingToBlockLeadP99Ms = percentile(pendingToBlockLeadTimes, 99)
	summary.IncludedSeenPendingRate = safeDiv(float64(summary.IncludedSeenPending), float64(summary.IncludedTransactions))
	summary.IncludedVisibilityLossRate = 1 - summary.IncludedSeenPendingRate
	if summary.IncludedTransactions == 0 {
		summary.IncludedVisibilityLossRate = 0
	}
	summary.IncludedERC20SeenPendingRate = safeDiv(float64(summary.IncludedERC20SeenPending), float64(summary.IncludedERC20Transfers))
	summary.IncludedERC20VisibilityLossRate = 1 - summary.IncludedERC20SeenPendingRate
	if summary.IncludedERC20Transfers == 0 {
		summary.IncludedERC20VisibilityLossRate = 0
	}
	validateMicroVisibility(summary)
}

func flushMicroCSVs(eventCSV, blockCSV *csv.Writer) error {
	eventCSV.Flush()
	if err := eventCSV.Error(); err != nil {
		return err
	}
	blockCSV.Flush()
	if err := blockCSV.Error(); err != nil {
		return err
	}
	return nil
}

func validateMicroVisibility(summary *MicroBenchmarkSummary) {
	reasons := []string{}
	if !summary.WarmupCompleted {
		reasons = append(reasons, "warmup incomplete")
	}
	if summary.BlocksObserved < int64(summary.AcceptanceMinBlocks) {
		reasons = append(reasons, fmt.Sprintf("observed blocks %d < %d", summary.BlocksObserved, summary.AcceptanceMinBlocks))
	}
	if summary.SubscriptionDroppedMessages > 0 {
		reasons = append(reasons, fmt.Sprintf("subscription dropped messages %d", summary.SubscriptionDroppedMessages))
	}
	summary.VisibilityValid = len(reasons) == 0
	summary.VisibilityInvalidReason = strings.Join(reasons, "; ")
}

func saveMicroRun(st *store.Store, summary MicroBenchmarkSummary, outDir string) error {
	if st == nil {
		return nil
	}
	metrics, _ := json.Marshal(summary)
	artifacts, _ := json.Marshal(map[string]string{"out_dir": outDir})
	return st.SaveRun("live-mempool", "mempool_trieguard", "complete", string(metrics), string(artifacts), "live mempool micro-benchmark")
}

func writeEventHeader(w *csv.Writer) {
	_ = w.Write([]string{
		"observed_at", "tx_hash", "from", "nonce", "payload_kind",
		"fetch_ok", "fetch_attempts", "fetch_latency_ms",
		"erc20", "erc20_method",
		"detector_completed_at", "detector_latency_ms", "detector_latency_us", "detector_latency_ns",
		"lookup_mean_ms", "lookup_mean_us", "lookup_mean_ns",
		"lookup_p95_ms", "lookup_p95_us", "lookup_p95_ns",
		"pending_interarrival_ms", "candidates_scored", "alerts",
		"replacement_candidate", "gas", "gas_price", "max_fee_per_gas", "max_priority_fee_per_gas", "error",
	})
}

func writeEventRow(w *csv.Writer, row microEventRow) {
	_ = w.Write([]string{
		row.ObservedAt.Format(time.RFC3339Nano),
		row.TxHash,
		row.From,
		row.Nonce,
		row.PayloadKind,
		strconv.FormatBool(row.FetchOK),
		strconv.Itoa(row.FetchAttempts),
		fmt.Sprintf("%.6f", row.FetchLatencyMs),
		strconv.FormatBool(row.ERC20),
		row.ERC20Method,
		formatOptionalTime(row.DetectorCompletedAt),
		fmt.Sprintf("%.6f", row.DetectorLatencyMs),
		fmt.Sprintf("%.3f", row.DetectorLatencyUs),
		strconv.FormatInt(row.DetectorLatencyNs, 10),
		fmt.Sprintf("%.6f", row.LookupMeanMs),
		fmt.Sprintf("%.3f", row.LookupMeanUs),
		strconv.FormatInt(row.LookupMeanNs, 10),
		fmt.Sprintf("%.6f", row.LookupP95Ms),
		fmt.Sprintf("%.3f", row.LookupP95Us),
		strconv.FormatInt(row.LookupP95Ns, 10),
		fmt.Sprintf("%.6f", row.PendingInterarrivalMs),
		strconv.Itoa(row.CandidatesScored),
		strconv.Itoa(row.Alerts),
		strconv.FormatBool(row.ReplacementCandidate),
		row.Gas,
		row.GasPrice,
		row.MaxFeePerGas,
		row.MaxPriorityFeePerGas,
		row.Error,
	})
}

func formatOptionalTime(value time.Time) string {
	if value.IsZero() {
		return ""
	}
	return value.UTC().Format(time.RFC3339Nano)
}

func writeBlockHeader(w *csv.Writer) {
	_ = w.Write([]string{
		"observed_at", "block_timestamp", "block_number", "block_hash",
		"tx_total", "tx_seen_pending",
		"erc20_transfer_total", "erc20_transfer_seen_pending",
		"pending_to_block_timestamp_lead_mean_ms",
		"pending_to_block_timestamp_lead_p50_ms",
		"pending_to_block_timestamp_lead_p95_ms",
		"pending_to_block_timestamp_lead_p99_ms",
	})
}

func writeBlockRow(w *csv.Writer, row microBlockRow) {
	blockTimestamp := ""
	if !row.BlockTimestamp.IsZero() {
		blockTimestamp = row.BlockTimestamp.Format(time.RFC3339Nano)
	}
	_ = w.Write([]string{
		row.ObservedAt.Format(time.RFC3339Nano),
		blockTimestamp,
		strconv.FormatUint(row.BlockNumber, 10),
		row.BlockHash,
		strconv.Itoa(row.TxTotal),
		strconv.Itoa(row.TxSeenPending),
		strconv.Itoa(row.ERC20TransferTotal),
		strconv.Itoa(row.ERC20TransferSeenPending),
		fmt.Sprintf("%.6f", row.PendingToBlockLeadMeanMs),
		fmt.Sprintf("%.6f", row.PendingToBlockLeadP50Ms),
		fmt.Sprintf("%.6f", row.PendingToBlockLeadP95Ms),
		fmt.Sprintf("%.6f", row.PendingToBlockLeadP99Ms),
	})
}

func buildRunManifest(cfg config.AppConfig, summary MicroBenchmarkSummary) runManifest {
	hostName, _ := os.Hostname()
	protectedHash, protectedErr := hashFile(cfg.ProtectedAccounts)
	gitRevision, gitModified := buildVCSInfo()
	manifest := runManifest{
		StartedAt:                 summary.StartedAt,
		EndedAt:                   summary.EndedAt,
		DurationSeconds:           summary.DurationSeconds,
		ProviderHTTPHost:          providerHost(cfg.DRPC.HTTPURL),
		ProviderWSSHost:           providerHost(cfg.DRPC.WSSURL),
		SubscriptionName:          summary.SubscriptionName,
		SubscriptionID:            summary.SubscriptionID,
		SubscriptionIDs:           summary.SubscriptionIDs,
		SubscriptionReconnects:    summary.SubscriptionReconnects,
		SubscriptionErrors:        summary.SubscriptionErrors,
		RunRegion:                 firstEnv("LIVE_BENCHMARK_REGION", "VPS_REGION", "REGION"),
		HostName:                  hostName,
		GoVersion:                 runtime.Version(),
		GOOS:                      runtime.GOOS,
		GOARCH:                    runtime.GOARCH,
		GitRevision:               gitRevision,
		GitModified:               gitModified,
		ConfigHashSHA256:          benchmarkConfigHash(cfg),
		ProtectedAccountsPath:     cfg.ProtectedAccounts,
		ProtectedAccountsSHA256:   protectedHash,
		WarmupDurationSeconds:     summary.WarmupDurationSeconds,
		WarmupBlockCount:          summary.WarmupBlockCount,
		StateRetentionSeconds:     summary.StateRetentionSeconds,
		ArtifactFlushIntervalSecs: summary.ArtifactFlushIntervalSeconds,
		AcceptanceMinBlocks:       summary.AcceptanceMinBlocks,
		VisibilityValid:           summary.VisibilityValid,
		VisibilityInvalidReason:   summary.VisibilityInvalidReason,
		TelegramConfigured:        summary.TelegramConfigured,
		TelegramReceiptSemantics:  "Bot API sendMessage acceptance proxy: message_id/date and local HTTP round-trip; Telegram does not expose end-user device notification or read receipt timing.",
	}
	if protectedErr != nil {
		manifest.ProtectedAccountsHashError = protectedErr.Error()
	}
	return manifest
}

func benchmarkConfigHash(cfg config.AppConfig) string {
	redacted := struct {
		Mode              string                `json:"mode"`
		ProtectedAccounts string                `json:"protected_accounts_path"`
		MaxAlertsInMemory int                   `json:"max_alerts_in_memory"`
		Detector          config.DetectorConfig `json:"detector"`
		Benchmark         config.BenchConfig    `json:"benchmark"`
		Live              config.LiveConfig     `json:"live"`
	}{
		Mode:              cfg.Mode,
		ProtectedAccounts: cfg.ProtectedAccounts,
		MaxAlertsInMemory: cfg.MaxAlertsInMemory,
		Detector:          cfg.Detector,
		Benchmark:         cfg.Benchmark,
		Live:              cfg.Live,
	}
	b, _ := json.Marshal(redacted)
	return hashBytes(b)
}

func hashFile(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return hashBytes(b), nil
}

func hashBytes(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func providerHost(rawURL string) string {
	if rawURL == "" {
		return ""
	}
	u, err := url.Parse(rawURL)
	if err != nil {
		return ""
	}
	return u.Host
}

func buildVCSInfo() (string, string) {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return "", ""
	}
	var revision, modified string
	for _, setting := range info.Settings {
		switch setting.Key {
		case "vcs.revision":
			revision = setting.Value
		case "vcs.modified":
			modified = setting.Value
		}
	}
	return revision, modified
}

func firstEnv(keys ...string) string {
	for _, key := range keys {
		if value := strings.TrimSpace(os.Getenv(key)); value != "" {
			return value
		}
	}
	return ""
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

func percentile(xs []float64, q float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	ys := append([]float64(nil), xs...)
	sort.Float64s(ys)
	if len(ys) == 1 {
		return ys[0]
	}
	pos := (q / 100) * float64(len(ys)-1)
	lo := int(math.Floor(pos))
	hi := int(math.Ceil(pos))
	if lo == hi {
		return ys[lo]
	}
	frac := pos - float64(lo)
	return ys[lo] + (ys[hi]-ys[lo])*frac
}

func safeDiv(a, b float64) float64 {
	if b == 0 {
		return 0
	}
	return a / b
}

func msToUs(ms float64) float64 {
	return ms * 1000
}

func msToNs(ms float64) int64 {
	return int64(math.Round(ms * 1_000_000))
}

func msToNsFloat(ms float64) float64 {
	return ms * 1_000_000
}

func nsToUs(ns int64) float64 {
	return float64(ns) / 1000
}

func normalizeHash(hash string) string {
	hash = strings.ToLower(strings.TrimSpace(hash))
	if len(hash) >= 2 && hash[:2] == "0x" {
		return hash
	}
	if hash == "" {
		return ""
	}
	return "0x" + hash
}

func normalizeAddress(address string) string {
	address = strings.ToLower(strings.TrimSpace(address))
	if address == "" {
		return ""
	}
	if strings.HasPrefix(address, "0x") {
		return address
	}
	return "0x" + address
}

func normalizeHexQuantity(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	value = strings.TrimPrefix(value, "0x")
	value = strings.TrimLeft(value, "0")
	if value == "" {
		value = "0"
	}
	return "0x" + value
}

func formatHexUint64(value uint64) string {
	return "0x" + strconv.FormatUint(value, 16)
}

func parseHexUint64Local(s string) (uint64, error) {
	s = strings.TrimSpace(strings.TrimPrefix(strings.ToLower(s), "0x"))
	if s == "" {
		return 0, nil
	}
	return strconv.ParseUint(s, 16, 64)
}
