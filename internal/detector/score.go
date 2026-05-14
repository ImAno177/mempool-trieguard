package detector

import (
	"fmt"
	"math"
	"strings"
	"time"
)

func computeScore(cfg Config, metadata map[string]TokenMetadata, pending PendingTx, cp Counterparty, lookalike string, ap int, as int) ScoreBreakdown {
	addrScore := float64(min(ap, cfg.KP)+min(as, cfg.KS)) / float64(cfg.KP+cfg.KS)
	typeScore := transferTypeScore(cfg, pending)
	tokenScore := tokenContextScore(metadata, pending.TokenAddress, cp)
	delta := pending.ObservedAt.Sub(cp.LastSeen).Seconds()
	if delta < 0 {
		delta = 0
	}
	timeScore := math.Exp(-delta / cfg.Lambda)
	valueScore := valueRiskScore(cfg, normalizedValue(pending))
	total := cfg.Weights[0]*addrScore + cfg.Weights[1]*typeScore + cfg.Weights[2]*tokenScore + cfg.Weights[3]*timeScore + cfg.Weights[4]*valueScore
	return ScoreBreakdown{
		Address: addrScore,
		Type:    typeScore,
		Token:   tokenScore,
		Time:    timeScore,
		Value:   valueScore,
		Total:   total,
	}
}

func transferTypeScore(cfg Config, p PendingTx) float64 {
	value := normalizedValue(p)
	switch {
	case value == 0:
		return 1.0 // zero-value poisoning pattern
	case value > 0 && value <= cfg.TinyValue:
		return 0.8 // tiny-value poisoning pattern
	default:
		return 0.25
	}
}

func priorPoisoningRule(cfg Config, metadata map[string]TokenMetadata, pending PendingTx, cp Counterparty) bool {
	value := normalizedValue(pending)
	if value == 0 || (value > 0 && value <= cfg.TinyValue) {
		return true
	}
	return tokenContextScore(metadata, pending.TokenAddress, cp) >= 1.0
}

func tokenContextScore(metadata map[string]TokenMetadata, pendingToken string, cp Counterparty) float64 {
	p := normalizeTokenKey(pendingToken)
	t := normalizeTokenKey(cp.Token)
	if p == "" || t == "" {
		return 0.4
	}
	if p == t {
		return 0.2
	}
	pm, pok := metadata[p]
	cm := TokenMetadata{
		Address:         cp.Token,
		Decimals:        cp.TokenDecimals,
		Symbol:          cp.TokenSymbol,
		Name:            cp.TokenName,
		MetadataMissing: cp.MetadataMissing,
	}
	if normalizeTokenKey(cm.Address) == "" {
		cm, _ = metadata[t]
	}
	if pok && !pm.MetadataMissing && !cm.MetadataMissing {
		if sameNonEmpty(pm.Symbol, cm.Symbol) || sameNonEmpty(pm.Name, cm.Name) {
			return 1.0 // counterfeit token: same visible metadata, different contract.
		}
		return 0.7
	}
	return 0.7
}

func valueRiskScore(cfg Config, value float64) float64 {
	if value == 0 {
		return 1.0
	}
	if value <= cfg.TinyValue {
		return 0.7
	}
	// large value by itself is less suspicious for poisoning seeding tx
	v := 1.0 / (1.0 + math.Log10(1.0+value))
	if v < 0.1 {
		return 0.1
	}
	return v
}

func explainReason(ap int, as int, score ScoreBreakdown, cp Counterparty, p PendingTx) string {
	return fmt.Sprintf("prefix=%d suffix=%d score=%.3f token=%s trusted_token=%s value=%.6f at=%s", ap, as, score.Total, p.TokenAddress, cp.Token, normalizedValue(p), p.ObservedAt.UTC().Format(time.RFC3339))
}

func normalizeTokenKey(token string) string {
	return strings.TrimPrefix(strings.ToLower(strings.TrimSpace(token)), "0x")
}

func sameNonEmpty(a, b string) bool {
	a = strings.ToLower(strings.TrimSpace(a))
	b = strings.ToLower(strings.TrimSpace(b))
	return a != "" && b != "" && a == b
}

func normalizedValue(p PendingTx) float64 {
	if p.ValueNormalized != 0 {
		return p.ValueNormalized
	}
	return p.Value
}
