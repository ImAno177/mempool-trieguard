package live

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"mempool-trieguard/internal/detector"
)

func TestFormatTelegramAlert(t *testing.T) {
	detectedAt := time.Date(2026, 6, 19, 2, 0, 0, 123, time.UTC)
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
	msg := formatTelegramAlert(alert, detectedAt)
	for _, want := range []string{"Mempool-TrieGuard alert", "0xabc", "0x111", "0x222", "0x333", "0.950000", "detected:", "test reason"} {
		if !strings.Contains(msg, want) {
			t.Fatalf("formatted alert missing %q: %s", want, msg)
		}
	}
}

func TestTelegramNotifyAlertCapturesReceipt(t *testing.T) {
	var gotPayload map[string]interface{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/bottest-token/sendMessage" {
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
		if err := json.NewDecoder(r.Body).Decode(&gotPayload); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		_, _ = w.Write([]byte(`{"ok":true,"result":{"message_id":42,"date":1781834400}}`))
	}))
	defer server.Close()

	notifier := &telegramNotifier{
		token:      "test-token",
		chatID:     "123",
		http:       server.Client(),
		apiBaseURL: server.URL,
	}
	observedAt := time.Now().Add(-20 * time.Millisecond)
	detectedAt := time.Now().Add(-5 * time.Millisecond)
	alert := detector.Alert{
		TxHash:     "0xabc",
		Victim:     "0x111",
		Lookalike:  "0x222",
		ObservedAt: observedAt,
		Score:      detector.ScoreBreakdown{Total: 0.95},
	}

	receipt := notifier.NotifyAlert(context.Background(), alert, detectedAt)

	if !receipt.OK || receipt.TelegramMessageID != 42 || receipt.TelegramAPIDateUnix != 1781834400 {
		t.Fatalf("unexpected receipt: %+v", receipt)
	}
	if receipt.HTTPStatus != http.StatusOK || receipt.SendLatencyMs < 0 || receipt.DetectorToTelegramAcceptMs < 0 || receipt.PendingToTelegramAcceptMs < 0 {
		t.Fatalf("receipt timing/status invalid: %+v", receipt)
	}
	if gotPayload["chat_id"] != "123" || !strings.Contains(gotPayload["text"].(string), "detected:") {
		t.Fatalf("unexpected payload: %+v", gotPayload)
	}
}
