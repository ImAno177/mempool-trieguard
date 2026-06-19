package detector

import (
	"fmt"
	"math"
	"strings"
	"time"
)

func computeScore(cfg Config, metadata map[string]TokenMetadata, pending PendingTx, cp Counterparty, lookalike string, ap int, as int) ScoreBreakdown {
	rawAddrScore := float64(min(ap, cfg.KP)+min(as, cfg.KS)) / float64(cfg.KP+cfg.KS)
	addrScore := addressEvidenceScore(cfg, rawAddrScore, ap, as)
	typeScore := transferTypeScore(cfg, pending)
	tokenScore := tokenContextScore(metadata, pending.TokenAddress, cp)
	mode := strings.TrimSpace(strings.ToLower(cfg.ScoreMode))
	timeScore := 0.0
	valueScore := 0.0
	if mode != "logistic_lr" && mode != "logistic" {
		delta := pending.ObservedAt.Sub(cp.LastSeen).Seconds()
		if delta < 0 {
			delta = 0
		}
		timeScore = math.Exp(-delta / cfg.Lambda)
		valueScore = valueRiskScore(cfg, normalizedValue(pending))
	}
	total := scoreTotal(cfg, addrScore, typeScore, tokenScore, timeScore, valueScore)
	return ScoreBreakdown{
		Address: addrScore,
		Type:    typeScore,
		Token:   tokenScore,
		Time:    timeScore,
		Value:   valueScore,
		Total:   total,
	}
}

func addressEvidenceScore(cfg Config, rawAddrScore float64, ap int, as int) float64 {
	switch strings.TrimSpace(strings.ToLower(cfg.AddressScoreMode)) {
	case "prefix", "prefix_only":
		if cfg.KP <= 0 {
			return 0
		}
		return float64(min(ap, cfg.KP)) / float64(cfg.KP)
	case "suffix", "suffix_only":
		if cfg.KS <= 0 {
			return 0
		}
		return float64(min(as, cfg.KS)) / float64(cfg.KS)
	case "balanced", "balanced_sum", "balance":
		prefix := float64(min(ap, cfg.KP)) / float64(cfg.KP)
		suffix := float64(min(as, cfg.KS)) / float64(cfg.KS)
		mx := math.Max(prefix, suffix)
		if mx <= 0 {
			return 0
		}
		mn := math.Min(prefix, suffix)
		balance := mn / mx
		alpha := cfg.AddressBalanceAlpha
		if alpha < 0 || alpha > 1 {
			alpha = 0.50
		}
		gamma := cfg.AddressBalanceGamma
		if gamma <= 0 {
			gamma = 1.0
		}
		return rawAddrScore * (alpha + (1-alpha)*math.Pow(balance, gamma))
	default:
		return rawAddrScore
	}
}

func scoreTotal(cfg Config, addrScore, typeScore, tokenScore, timeScore, valueScore float64) float64 {
	switch strings.TrimSpace(strings.ToLower(cfg.ScoreMode)) {
	case "logistic_lr", "logistic":
		logit := cfg.LogisticIntercept +
			cfg.LogisticWeights[0]*addrScore +
			cfg.LogisticWeights[1]*addrScore*typeScore +
			cfg.LogisticWeights[2]*addrScore*tokenScore
		return 1.0 / (1.0 + math.Exp(-logit))
	case "context_gate", "context_gated_temporal":
		base := cfg.ContextGateBase
		if base <= 0 || base >= 1 {
			base = 0.30
		}
		weights := cfg.ContextWeights
		sum := 0.0
		for _, weight := range weights {
			sum += weight
		}
		if sum <= 0 {
			weights = [4]float64{0.65, 0.35, 0.0, 0.0}
			sum = 1.0
		}
		conditionedTime := timeScore * math.Max(typeScore, tokenScore)
		context := (weights[0]*typeScore + weights[1]*tokenScore + weights[2]*valueScore + weights[3]*conditionedTime) / sum
		if context < 0 {
			context = 0
		}
		if context > 1 {
			context = 1
		}
		return addrScore * (base + (1-base)*context)
	case "address_only":
		return addrScore
	default:
		return cfg.Weights[0]*addrScore + cfg.Weights[1]*typeScore + cfg.Weights[2]*tokenScore + cfg.Weights[3]*timeScore + cfg.Weights[4]*valueScore
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
