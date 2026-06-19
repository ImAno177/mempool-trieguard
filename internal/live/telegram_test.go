package live

import (
	"strings"
	"testing"
	"time"

	"mempool-trieguard/internal/detector"
)

func TestFormatTelegramAlert(t *testing.T) {
	alert := detector.Alert{
		TxHash:           "0xabc",
		Victim:           "0x111",
		Lookalike:        "0x222",
		MatchedRecipient: "0x333",
		ObservedAt:       time.Date(2026, 6, 19, 2, 0, 0, 0, time.UTC),
		MatchedPrefix:    4,
		MatchedSuffix:    5,
		Score:            detector.ScoreBreakdown{Total: 0.95},
		Reason:           "test reason",
	}
	msg := formatTelegramAlert(alert)
	for _, want := range []string{"Mempool-TrieGuard alert", "0xabc", "0x111", "0x222", "0x333", "0.950000", "test reason"} {
		if !strings.Contains(msg, want) {
			t.Fatalf("formatted alert missing %q: %s", want, msg)
		}
	}
}
