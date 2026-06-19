package live

import (
	"math"
	"testing"
	"time"

	"mempool-trieguard/internal/rpc"
)

func TestSummarizeBlockCountsSeenPendingERC20Transfers(t *testing.T) {
	blockTime := time.Unix(1_700_000_000, 0).UTC()
	seen := map[string]time.Time{
		"0xaaa": blockTime.Add(-2 * time.Second),
		"0xbbb": blockTime.Add(-1 * time.Second),
	}
	block := rpc.RPCBlock{
		Number:    "0x10",
		Hash:      "0xblock",
		Timestamp: "0x6553f100",
		Transactions: []rpc.RPCTransaction{
			{Hash: "0xaaa", Input: "0xa9059cbb00000000000000000000000011111111111111111111111111111111111111110000000000000000000000000000000000000000000000000000000000000001"},
			{Hash: "0xccc", Input: "0xa9059cbb00000000000000000000000022222222222222222222222222222222222222220000000000000000000000000000000000000000000000000000000000000002"},
			{Hash: "0xbbb", Input: "0x"},
		},
	}

	row := summarizeBlock(block, seen)
	if row.BlockNumber != 16 {
		t.Fatalf("block number = %d, want 16", row.BlockNumber)
	}
	if row.TxTotal != 3 || row.TxSeenPending != 2 {
		t.Fatalf("tx counts = total %d seen %d, want total 3 seen 2", row.TxTotal, row.TxSeenPending)
	}
	if row.ERC20TransferTotal != 2 || row.ERC20TransferSeenPending != 1 {
		t.Fatalf("erc20 counts = total %d seen %d, want total 2 seen 1", row.ERC20TransferTotal, row.ERC20TransferSeenPending)
	}
	if !closeFloat(row.PendingToBlockLeadMeanMs, 1500) {
		t.Fatalf("lead mean = %.3f, want 1500", row.PendingToBlockLeadMeanMs)
	}
}

func TestFinalizeMicroSummaryVisibilityRates(t *testing.T) {
	summary := MicroBenchmarkSummary{
		WarmupCompleted:          true,
		AcceptanceMinBlocks:      2,
		BlocksObserved:           2,
		IncludedTransactions:     10,
		IncludedSeenPending:      8,
		IncludedERC20Transfers:   4,
		IncludedERC20SeenPending: 3,
		DurationSeconds:          2,
		PendingMessages:          8,
		DetectorEvents:           4,
		ERC20TransferCalls:       2,
	}
	finalizeMicroSummary(&summary, []float64{1, 3}, []float64{0.1, 0.3}, []float64{0.01, 0.03}, []float64{2, 4}, []float64{10, 20}, []float64{1000, 2000}, []float64{30, 50}, []float64{40, 80}, []float64{100, 200})

	if !closeFloat(summary.IncludedSeenPendingRate, 0.8) || !closeFloat(summary.IncludedVisibilityLossRate, 0.2) {
		t.Fatalf("visibility rates = %.3f %.3f, want 0.8 0.2", summary.IncludedSeenPendingRate, summary.IncludedVisibilityLossRate)
	}
	if !closeFloat(summary.IncludedERC20SeenPendingRate, 0.75) || !closeFloat(summary.IncludedERC20VisibilityLossRate, 0.25) {
		t.Fatalf("erc20 visibility rates = %.3f %.3f, want 0.75 0.25", summary.IncludedERC20SeenPendingRate, summary.IncludedERC20VisibilityLossRate)
	}
	if !summary.VisibilityValid {
		t.Fatalf("visibility should be valid: %s", summary.VisibilityInvalidReason)
	}
	if !closeFloat(summary.PendingMessagesPerSecond, 4) || !closeFloat(summary.DetectorEventsPerSecond, 2) || !closeFloat(summary.ERC20TransferCallsPerSecond, 1) {
		t.Fatalf("rates = %.3f %.3f %.3f", summary.PendingMessagesPerSecond, summary.DetectorEventsPerSecond, summary.ERC20TransferCallsPerSecond)
	}
	if !closeFloat(summary.DetectorLatencyP50Us, 200) || !closeFloat(summary.LookupLatencyP95Ns, 29000) {
		t.Fatalf("latency conversions detector_p50_us=%.3f lookup_p95_ns=%.3f", summary.DetectorLatencyP50Us, summary.LookupLatencyP95Ns)
	}
	if !closeFloat(summary.TelegramSendLatencyP50Ms, 40) || !closeFloat(summary.DetectorToTelegramP95Ms, 78) || !closeFloat(summary.PendingToTelegramP99Ms, 199) {
		t.Fatalf("telegram latencies send_p50=%.3f detector_p95=%.3f pending_p99=%.3f", summary.TelegramSendLatencyP50Ms, summary.DetectorToTelegramP95Ms, summary.PendingToTelegramP99Ms)
	}
}

func TestWarmupExclusionRequiresTimeAndBlocks(t *testing.T) {
	started := time.Unix(100, 0).UTC()
	if !shouldSkipWarmupBlock(started, started.Add(2*time.Minute), 5) {
		t.Fatalf("first five blocks should be warmup even after duration")
	}
	if !shouldSkipWarmupBlock(started, started.Add(30*time.Second), 6) {
		t.Fatalf("blocks inside warmup duration should be skipped")
	}
	if shouldSkipWarmupBlock(started, started.Add(61*time.Second), 6) {
		t.Fatalf("block after warmup duration and count should be counted")
	}
}

func TestSequentialBlockNumbers(t *testing.T) {
	got := sequentialBlockNumbers(10, 13)
	want := []uint64{11, 12, 13}
	if len(got) != len(want) {
		t.Fatalf("len=%d, want %d", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got[%d]=%d, want %d", i, got[i], want[i])
		}
	}
	if got := sequentialBlockNumbers(13, 13); len(got) != 0 {
		t.Fatalf("expected empty range, got %v", got)
	}
}

func TestRecordVisibilityBlockUsesPostWarmupDenominators(t *testing.T) {
	summary := MicroBenchmarkSummary{}
	lead := []float64{}
	recordVisibilityBlock(&summary, microBlockRow{
		TxTotal:                  5,
		TxSeenPending:            3,
		ERC20TransferTotal:       2,
		ERC20TransferSeenPending: 1,
		pendingToBlockLeadMs:     []float64{100, 200},
	}, &lead)

	if summary.BlocksObserved != 1 || summary.IncludedTransactions != 5 || summary.IncludedSeenPending != 3 {
		t.Fatalf("summary counts = %+v", summary)
	}
	if len(lead) != 2 || lead[0] != 100 || lead[1] != 200 {
		t.Fatalf("lead times = %v", lead)
	}
}

func TestReplacementCandidateGrouping(t *testing.T) {
	keyA := senderNonceKey{from: normalizeAddress("ABC"), nonce: normalizeHexQuantity("0x000a")}
	keyB := senderNonceKey{from: "0xabc", nonce: "0xa"}
	if keyA != keyB {
		t.Fatalf("normalized keys differ: %+v %+v", keyA, keyB)
	}
	if normalizeHash("abc") != "0xabc" {
		t.Fatalf("normalizeHash failed")
	}
}

func TestPruneMicroStateBoundsLongRunMaps(t *testing.T) {
	now := time.Unix(1_700_000_000, 0).UTC()
	old := now.Add(-liveBenchmarkStateRetention - time.Second)
	fresh := now.Add(-liveBenchmarkStateRetention + time.Second)
	seenPending := map[string]time.Time{
		"0xold":   old,
		"0xfresh": fresh,
	}
	oldKey := senderNonceKey{from: "0xaaa", nonce: "0x1"}
	freshKey := senderNonceKey{from: "0xbbb", nonce: "0x2"}
	senderNonce := map[senderNonceKey]senderNonceObservation{
		oldKey:   {hash: "0xold", observedAt: old},
		freshKey: {hash: "0xfresh", observedAt: fresh},
	}
	summary := MicroBenchmarkSummary{}

	pruneMicroState(now, seenPending, senderNonce, &summary)

	if _, ok := seenPending["0xold"]; ok {
		t.Fatalf("old pending entry was not pruned")
	}
	if _, ok := seenPending["0xfresh"]; !ok {
		t.Fatalf("fresh pending entry was pruned")
	}
	if _, ok := senderNonce[oldKey]; ok {
		t.Fatalf("old sender/nonce entry was not pruned")
	}
	if _, ok := senderNonce[freshKey]; !ok {
		t.Fatalf("fresh sender/nonce entry was pruned")
	}
	if summary.PendingStatePruned != 1 || summary.SenderNonceGroupsPruned != 1 {
		t.Fatalf("prune counters = pending %d sender %d", summary.PendingStatePruned, summary.SenderNonceGroupsPruned)
	}
}

func closeFloat(a, b float64) bool {
	return math.Abs(a-b) < 1e-9
}
