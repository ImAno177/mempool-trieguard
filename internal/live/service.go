package live

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"

	"mempool-trieguard/internal/config"
	"mempool-trieguard/internal/detector"
	"mempool-trieguard/internal/rpc"
	"mempool-trieguard/internal/store"
)

type Status struct {
	Running        bool      `json:"running"`
	Connected      bool      `json:"connected"`
	SubscriptionID string    `json:"subscription_id"`
	LastError      string    `json:"last_error"`
	LastMessageAt  time.Time `json:"last_message_at"`
	TotalMessages  int64     `json:"total_messages"`
	TotalAlerts    int64     `json:"total_alerts"`
}

type Service struct {
	cfg    config.AppConfig
	rpc    *rpc.Client
	st     *store.Store
	engine *detector.Engine
	alerts []detector.Alert
	status Status

	mu     sync.RWMutex
	cancel context.CancelFunc
}

func NewService(cfg config.AppConfig, st *store.Store) (*Service, error) {
	eng, err := buildEngineFromFile(cfg)
	if err != nil {
		return nil, err
	}
	return &Service{
		cfg:    cfg,
		rpc:    rpc.NewClient(cfg.DRPC.HTTPURL, cfg.DRPC.WSSURL, cfg.DRPC.Key),
		st:     st,
		engine: eng,
		alerts: make([]detector.Alert, 0, cfg.MaxAlertsInMemory),
	}, nil
}

func buildEngineFromFile(cfg config.AppConfig) (*detector.Engine, error) {
	b, err := os.ReadFile(cfg.ProtectedAccounts)
	if err != nil {
		return nil, fmt.Errorf("read protected accounts: %w", err)
	}
	var cps []detector.Counterparty
	if err := json.Unmarshal(b, &cps); err != nil {
		return nil, fmt.Errorf("parse protected accounts json: %w", err)
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
	eng := detector.NewEngine(dcfg)
	if err := eng.LoadCounterparties(cps); err != nil {
		return nil, err
	}
	return eng, nil
}

func (s *Service) Start(ctx context.Context) error {
	s.mu.Lock()
	if s.status.Running {
		s.mu.Unlock()
		return nil
	}
	ctxRun, cancel := context.WithCancel(ctx)
	s.cancel = cancel
	s.status.Running = true
	s.status.LastError = ""
	s.mu.Unlock()

	sub, err := s.rpc.Subscribe(ctxRun, s.cfg.Live.SubscriptionName)
	if err != nil {
		s.mu.Lock()
		s.status.Running = false
		s.status.LastError = err.Error()
		s.mu.Unlock()
		return err
	}
	go s.runLoop(ctxRun, sub)
	return nil
}

func (s *Service) runLoop(ctx context.Context, sub *rpc.Subscription) {
	defer sub.Close()
	s.mu.Lock()
	s.status.Connected = true
	s.status.SubscriptionID = sub.SubscriptionID
	s.mu.Unlock()

	for {
		select {
		case <-ctx.Done():
			s.mu.Lock()
			s.status.Running = false
			s.status.Connected = false
			s.mu.Unlock()
			return
		case err := <-sub.Errors:
			s.mu.Lock()
			s.status.LastError = err.Error()
			s.status.Connected = false
			s.status.Running = false
			s.mu.Unlock()
			return
		case raw, ok := <-sub.RawMessages:
			if !ok {
				s.mu.Lock()
				s.status.Running = false
				s.status.Connected = false
				s.mu.Unlock()
				return
			}
			s.processPendingRaw(ctx, raw, sub.SubscriptionID)
		}
	}
}

func (s *Service) processPendingRaw(ctx context.Context, raw json.RawMessage, subID string) {
	hash, txObj, hasObj, err := rpc.DecodePendingResult(raw)
	if err != nil {
		s.setError(err)
		return
	}
	tx := txObj
	if !hasObj {
		fetched, err := s.getPendingTransactionWithRetry(ctx, hash)
		if err != nil {
			s.setError(fmt.Errorf("get tx by hash %s: %w", hash, err))
			return
		}
		tx = fetched
	}

	pending := detector.PendingTx{
		Hash:       tx.Hash,
		From:       tx.From,
		To:         tx.To,
		ObservedAt: time.Now().UTC(),
		Value:      rpc.HexToFloat(tx.Value),
	}
	if call, ok := rpc.ParseERC20TransferCall(tx.Input); ok {
		pending.TokenAddress = tx.To
		if call.From != "" {
			pending.From = call.From
		}
		pending.To = call.To
		pending.Value = call.Value
		pending.ValueRaw = call.Value
	}

	alerts, _ := s.engine.Detect(pending)
	if len(alerts) == 0 {
		s.mu.Lock()
		s.status.TotalMessages++
		s.status.LastMessageAt = pending.ObservedAt
		s.mu.Unlock()
		return
	}
	for i := range alerts {
		alerts[i].SubscriptionTrace = subID
	}
	_ = s.st.SaveAlerts(alerts)

	s.mu.Lock()
	s.status.TotalMessages++
	s.status.TotalAlerts += int64(len(alerts))
	s.status.LastMessageAt = pending.ObservedAt
	for _, a := range alerts {
		s.alerts = append([]detector.Alert{a}, s.alerts...)
	}
	if len(s.alerts) > s.cfg.MaxAlertsInMemory {
		s.alerts = s.alerts[:s.cfg.MaxAlertsInMemory]
	}
	s.mu.Unlock()
}

func (s *Service) Stop() {
	s.mu.Lock()
	cancel := s.cancel
	s.cancel = nil
	s.status.Running = false
	s.status.Connected = false
	s.mu.Unlock()
	if cancel != nil {
		cancel()
	}
}

func (s *Service) getPendingTransactionWithRetry(ctx context.Context, hash string) (rpc.RPCTransaction, error) {
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		tx, err := s.rpc.GetTransactionByHash(ctx, hash)
		if err == nil && tx.Hash != "" {
			return tx, nil
		}
		if err != nil {
			lastErr = err
		} else {
			lastErr = fmt.Errorf("empty tx result")
		}
		select {
		case <-ctx.Done():
			return rpc.RPCTransaction{}, ctx.Err()
		case <-time.After(time.Duration(150*(attempt+1)) * time.Millisecond):
		}
	}
	return rpc.RPCTransaction{}, lastErr
}

func (s *Service) setError(err error) {
	s.mu.Lock()
	s.status.LastError = err.Error()
	s.mu.Unlock()
}

func (s *Service) Status() Status {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.status
}

func (s *Service) Alerts(limit int) []detector.Alert {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if limit <= 0 || limit > len(s.alerts) {
		limit = len(s.alerts)
	}
	out := make([]detector.Alert, limit)
	copy(out, s.alerts[:limit])
	return out
}

func (s *Service) ApplyDetectorConfig(dc config.DetectorConfig) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.cfg.Detector = dc
	eng, err := buildEngineFromFile(s.cfg)
	if err != nil {
		return err
	}
	s.engine = eng
	return nil
}
