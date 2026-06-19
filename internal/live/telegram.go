package live

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"mempool-trieguard/internal/detector"
)

type telegramNotifier struct {
	token  string
	chatID string
	http   *http.Client
}

func newTelegramNotifierFromEnv() *telegramNotifier {
	token := strings.TrimSpace(os.Getenv("TELEGRAM_BOT_TOKEN"))
	chatID := strings.TrimSpace(os.Getenv("TELEGRAM_CHAT_ID"))
	if token == "" || chatID == "" {
		return nil
	}
	return &telegramNotifier{
		token:  token,
		chatID: chatID,
		http:   &http.Client{Timeout: 5 * time.Second},
	}
}

func (n *telegramNotifier) NotifyAlerts(ctx context.Context, alerts []detector.Alert) error {
	if n == nil || len(alerts) == 0 {
		return nil
	}
	var lastErr error
	for _, alert := range alerts {
		if err := n.sendMessage(ctx, formatTelegramAlert(alert)); err != nil {
			lastErr = err
		}
	}
	return lastErr
}

func (n *telegramNotifier) sendMessage(ctx context.Context, text string) error {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	payload := map[string]interface{}{
		"chat_id":                  n.chatID,
		"text":                     text,
		"disable_web_page_preview": true,
	}
	body, _ := json.Marshal(payload)
	url := "https://api.telegram.org/bot" + n.token + "/sendMessage"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := n.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("telegram send failed: status %d", resp.StatusCode)
	}
	return nil
}

func formatTelegramAlert(a detector.Alert) string {
	return fmt.Sprintf(
		"Mempool-TrieGuard alert\nscore: %.6f\ntx: %s\nvictim: %s\nlookalike: %s\nmatched: %s\nprefix/suffix: %d/%d\nobserved: %s\nreason: %s",
		a.Score.Total,
		a.TxHash,
		a.Victim,
		a.Lookalike,
		a.MatchedRecipient,
		a.MatchedPrefix,
		a.MatchedSuffix,
		a.ObservedAt.UTC().Format(time.RFC3339),
		a.Reason,
	)
}
