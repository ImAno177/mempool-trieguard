package detector

import "time"

// DetectLinear is a baseline that scans all trusted counterparties for each victim.
func (e *Engine) DetectLinear(p PendingTx) ([]Alert, []PerfRecord) {
	started := time.Now()
	alerts := make([]Alert, 0)
	perf := make([]PerfRecord, 0)

	from, err1 := NormalizeAddress(p.From)
	to, err2 := NormalizeAddress(p.To)
	if err1 != nil || err2 != nil {
		return alerts, perf
	}

	e.mu.RLock()
	defer e.mu.RUnlock()

	pairs := [][2]string{}
	if _, ok := e.victims[from]; ok {
		pairs = append(pairs, [2]string{from, to})
	}
	if _, ok := e.victims[to]; ok {
		pairs = append(pairs, [2]string{to, from})
	}

	for _, pair := range pairs {
		victim, lookalike := pair[0], pair[1]
		idx := e.indices[victim]
		if idx == nil {
			continue
		}
		scored := 0
		for recipient, histories := range idx.Recipients {
			if recipient == lookalike {
				continue
			}
			ap := prefixMatchNibbles(lookalike, recipient)
			as := suffixMatchNibbles(lookalike, recipient)
			cp, score, ok, activeScored := e.bestScoringCounterparty(p, histories, lookalike, ap, as)
			scored += activeScored
			if ok && score.Total >= e.cfg.Tau {
				alerts = append(alerts, Alert{
					TxHash:           p.Hash,
					Victim:           victim,
					Lookalike:        lookalike,
					MatchedRecipient: recipient,
					ObservedAt:       p.ObservedAt,
					MatchedPrefix:    ap,
					MatchedSuffix:    as,
					Score:            score,
					Reason:           explainReason(ap, as, score, cp, p),
				})
			}
		}
		perf = append(perf, PerfRecord{
			TxHash:           p.Hash,
			Victim:           victim,
			LookupLatencyMs:  time.Since(started).Seconds() * 1000,
			CandidatesScored: scored,
		})
	}
	return alerts, perf
}
