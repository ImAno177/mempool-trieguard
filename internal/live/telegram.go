package live

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"mempool-trieguard/internal/detector"
)

type telegramNotifier struct {
	token      string
	chatID     string
	http       *http.Client
	apiBaseURL string
}

type telegramSendReceipt struct {
	Enabled                    bool      `json:"enabled"`
	OK                         bool      `json:"ok"`
	SendStartedAt              time.Time `json:"send_started_at,omitempty"`
	SendCompletedAt            time.Time `json:"send_completed_at,omitempty"`
	SendLatencyMs              float64   `json:"send_latency_ms,omitempty"`
	DetectorToTelegramAcceptMs float64   `json:"detector_to_telegram_accept_ms,omitempty"`
	PendingToTelegramAcceptMs  float64   `json:"pending_to_telegram_accept_ms,omitempty"`
	HTTPStatus                 int       `json:"http_status,omitempty"`
	TelegramMessageID          int       `json:"telegram_message_id,omitempty"`
	TelegramAPIDateUnix        int64     `json:"telegram_api_date_unix,omitempty"`
	TelegramAPIDateAt          time.Time `json:"telegram_api_date_at,omitempty"`
	TelegramAPIDateSkewMs      float64   `json:"telegram_api_date_skew_ms,omitempty"`
	Error                      string    `json:"error,omitempty"`
}

type telegramSendMessageResponse struct {
	OK          bool   `json:"ok"`
	Description string `json:"description,omitempty"`
	Result      struct {
		MessageID int   `json:"message_id"`
		Date      int64 `json:"date"`
	} `json:"result"`
}

func newTelegramNotifierFromEnv() *telegramNotifier {
	token := strings.TrimSpace(os.Getenv("TELEGRAM_BOT_TOKEN"))
	chatID := strings.TrimSpace(os.Getenv("TELEGRAM_CHAT_ID"))
	if token == "" || chatID == "" {
		return nil
	}
	return &telegramNotifier{
		token:      token,
		chatID:     chatID,
		http:       &http.Client{Timeout: 5 * time.Second},
		apiBaseURL: "https://api.telegram.org",
	}
}

func (n *telegramNotifier) NotifyAlerts(ctx context.Context, alerts []detector.Alert, detectedAt time.Time) []telegramSendReceipt {
	receipts := make([]telegramSendReceipt, 0, len(alerts))
	if n == nil || len(alerts) == 0 {
		return receipts
	}
	for _, alert := range alerts {
		receipts = append(receipts, n.NotifyAlert(ctx, alert, detectedAt))
	}
	return receipts
}

func (n *telegramNotifier) NotifyAlert(ctx context.Context, alert detector.Alert, detectedAt time.Time) telegramSendReceipt {
	if n == nil {
		return telegramSendReceipt{}
	}
	return n.sendMessage(ctx, formatTelegramAlert(alert, detectedAt), alert.ObservedAt, detectedAt)
}

func (n *telegramNotifier) sendMessage(ctx context.Context, text string, pendingObservedAt, detectedAt time.Time) telegramSendReceipt {
	started := time.Now()
	receipt := telegramSendReceipt{
		Enabled:       true,
		SendStartedAt: started.UTC(),
	}
	complete := func(err error) telegramSendReceipt {
		completed := time.Now()
		receipt.SendCompletedAt = completed.UTC()
		receipt.SendLatencyMs = completed.Sub(started).Seconds() * 1000
		if !detectedAt.IsZero() {
			receipt.DetectorToTelegramAcceptMs = completed.Sub(detectedAt).Seconds() * 1000
		}
		if !pendingObservedAt.IsZero() {
			receipt.PendingToTelegramAcceptMs = completed.Sub(pendingObservedAt).Seconds() * 1000
		}
		if err != nil {
			receipt.Error = err.Error()
		}
		return receipt
	}

	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	payload := map[string]interface{}{
		"chat_id":                  n.chatID,
		"text":                     text,
		"disable_web_page_preview": true,
	}
	body, _ := json.Marshal(payload)
	url := strings.TrimRight(n.apiBaseURL, "/") + "/bot" + n.token + "/sendMessage"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return complete(err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := n.http.Do(req)
	if err != nil {
		return complete(err)
	}
	defer resp.Body.Close()
	receipt.HTTPStatus = resp.StatusCode
	respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if readErr != nil {
		return complete(readErr)
	}
	var parsed telegramSendMessageResponse
	if len(respBody) > 0 {
		if err := json.Unmarshal(respBody, &parsed); err != nil {
			return complete(err)
		}
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		if parsed.Description != "" {
			return complete(fmt.Errorf("telegram send failed: status %d: %s", resp.StatusCode, parsed.Description))
		}
		return complete(fmt.Errorf("telegram send failed: status %d", resp.StatusCode))
	}
	if !parsed.OK {
		if parsed.Description != "" {
			return complete(fmt.Errorf("telegram send failed: %s", parsed.Description))
		}
		return complete(fmt.Errorf("telegram send failed: ok=false"))
	}
	receipt.OK = true
	receipt.TelegramMessageID = parsed.Result.MessageID
	if parsed.Result.Date > 0 {
		receipt.TelegramAPIDateUnix = parsed.Result.Date
		receipt.TelegramAPIDateAt = time.Unix(parsed.Result.Date, 0).UTC()
		receipt.TelegramAPIDateSkewMs = time.Since(receipt.TelegramAPIDateAt).Seconds() * 1000
	}
	return complete(nil)
}

func formatTelegramAlert(a detector.Alert, detectedAt time.Time) string {
	detected := ""
	if !detectedAt.IsZero() {
		detected = detectedAt.UTC().Format(time.RFC3339Nano)
	}
	return fmt.Sprintf(
		"Mempool-TrieGuard alert\nscore: %.6f\ntx: %s\nvictim: %s\nlookalike: %s\nmatched: %s\nprefix/suffix: %d/%d\nobserved: %s\ndetected: %s\nreason: %s",
		a.Score.Total,
		a.TxHash,
		a.Victim,
		a.Lookalike,
		a.MatchedRecipient,
		a.MatchedPrefix,
		a.MatchedSuffix,
		a.ObservedAt.UTC().Format(time.RFC3339),
		detected,
		a.Reason,
	)
}
