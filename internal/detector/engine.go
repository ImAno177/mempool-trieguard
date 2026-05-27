package detector

import (
	"fmt"
	"sort"
	"sync"
	"time"
)

type victimIndex struct {
	Prefix     *Trie
	Suffix     *Trie
	Recipients map[string][]Counterparty // key: normalized trusted recipient
}

type Engine struct {
	cfg      Config
	indices  map[string]*victimIndex
	victims  map[string]struct{}
	metadata map[string]TokenMetadata
	mu       sync.RWMutex
}

type trieCandidateMode int

const (
	candidateUnion trieCandidateMode = iota
	candidateIntersection
	candidatePrefixOnly
	candidateSuffixOnly
)

func NewEngine(cfg Config) *Engine {
	return &Engine{
		cfg:      cfg,
		indices:  map[string]*victimIndex{},
		victims:  map[string]struct{}{},
		metadata: map[string]TokenMetadata{},
	}
}

func (e *Engine) SetTokenMetadata(metadata []TokenMetadata) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.metadata = map[string]TokenMetadata{}
	for _, md := range metadata {
		key := normalizeTokenKey(md.Address)
		if key == "" {
			continue
		}
		md.Address = key
		e.metadata[key] = md
	}
}

func (e *Engine) LoadCounterparties(counterparties []Counterparty) error {
	e.mu.Lock()
	defer e.mu.Unlock()

	e.indices = map[string]*victimIndex{}
	e.victims = map[string]struct{}{}

	for _, cp := range counterparties {
		victim, err := NormalizeAddress(cp.Victim)
		if err != nil {
			return fmt.Errorf("invalid victim %q: %w", cp.Victim, err)
		}
		recipient, err := NormalizeAddress(cp.Recipient)
		if err != nil {
			return fmt.Errorf("invalid recipient %q: %w", cp.Recipient, err)
		}
		if cp.LastSeen.IsZero() {
			cp.LastSeen = time.Unix(0, 0).UTC()
		}
		cp.Victim = victim
		cp.Recipient = recipient
		cp.Token = normalizeTokenKey(cp.Token)

		idx, ok := e.indices[victim]
		if !ok {
			idx = &victimIndex{
				Prefix:     NewTrie(),
				Suffix:     NewTrie(),
				Recipients: map[string][]Counterparty{},
			}
			e.indices[victim] = idx
			e.victims[victim] = struct{}{}
		}

		idx.Recipients[recipient] = append(idx.Recipients[recipient], cp)
		prefixKey := recipient[:min(e.cfg.KP, len(recipient))]
		suffixKey := recipient[len(recipient)-min(e.cfg.KS, len(recipient)):]
		idx.Prefix.Insert(prefixKey, recipient)
		idx.Suffix.Insert(reverseString(suffixKey), recipient)
	}
	return nil
}

func (e *Engine) ProtectedVictimCount() int {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return len(e.victims)
}

func (e *Engine) Detect(p PendingTx) ([]Alert, []PerfRecord) {
	return e.detectByTrieMode(p, candidateUnion, true)
}

func (e *Engine) MaxScore(p PendingTx) (ScoreResult, []PerfRecord) {
	return e.maxScoreByTrieMode(p, candidateUnion, true)
}

// DetectLegacy uses the previous deepest-node prefix/suffix retrieval policy.
func (e *Engine) DetectLegacy(p PendingTx) ([]Alert, []PerfRecord) {
	return e.detectByTrieMode(p, candidateUnion, false)
}

// DetectPriorRule applies a confirmed-chain style similarity rule inspired by
// prior address-poisoning studies: both visible sides must match and the
// transfer must look like a poisoning seed (zero/tiny/counterfeit token).
func (e *Engine) DetectPriorRule(p PendingTx) ([]Alert, []PerfRecord) {
	return e.detectPriorRuleByTrieMode(p, candidateIntersection)
}

// DetectIntersection uses the conservative prefix/suffix intersection retrieval policy.
func (e *Engine) DetectIntersection(p PendingTx) ([]Alert, []PerfRecord) {
	return e.detectByTrieMode(p, candidateIntersection, true)
}

func (e *Engine) MaxScoreIntersection(p PendingTx) (ScoreResult, []PerfRecord) {
	return e.maxScoreByTrieMode(p, candidateIntersection, true)
}

// DetectPrefixOnly uses only prefix-trie retrieval for ablation experiments.
func (e *Engine) DetectPrefixOnly(p PendingTx) ([]Alert, []PerfRecord) {
	return e.detectByTrieMode(p, candidatePrefixOnly, true)
}

func (e *Engine) MaxScorePrefixOnly(p PendingTx) (ScoreResult, []PerfRecord) {
	return e.maxScoreByTrieMode(p, candidatePrefixOnly, true)
}

// DetectSuffixOnly uses only suffix-trie retrieval for ablation experiments.
func (e *Engine) DetectSuffixOnly(p PendingTx) ([]Alert, []PerfRecord) {
	return e.detectByTrieMode(p, candidateSuffixOnly, true)
}

func (e *Engine) MaxScoreSuffixOnly(p PendingTx) (ScoreResult, []PerfRecord) {
	return e.maxScoreByTrieMode(p, candidateSuffixOnly, true)
}

func (e *Engine) detectByTrieMode(p PendingTx, mode trieCandidateMode, cumulative bool) ([]Alert, []PerfRecord) {
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

	candidates := [][2]string{}
	if _, ok := e.victims[from]; ok {
		candidates = append(candidates, [2]string{from, to})
	}
	if _, ok := e.victims[to]; ok {
		candidates = append(candidates, [2]string{to, from})
	}

	for _, c := range candidates {
		victim := c[0]
		lookalike := c[1]
		idx := e.indices[victim]
		if idx == nil {
			continue
		}
		candidateIDs := e.candidateIDs(idx, p, lookalike, mode, cumulative)

		scored := 0
		for r := range candidateIDs {
			if r == lookalike {
				continue
			}
			histories := idx.Recipients[r]
			ap := prefixMatchNibbles(lookalike, r)
			as := suffixMatchNibbles(lookalike, r)
			cp, score, ok, activeScored := e.bestScoringCounterparty(p, histories, lookalike, ap, as)
			scored += activeScored
			if ok && score.Total >= e.cfg.Tau {
				alerts = append(alerts, Alert{
					TxHash:           p.Hash,
					Victim:           victim,
					Lookalike:        lookalike,
					MatchedRecipient: r,
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

func (e *Engine) maxScoreByTrieMode(p PendingTx, mode trieCandidateMode, cumulative bool) (ScoreResult, []PerfRecord) {
	started := time.Now()
	best := ScoreResult{
		TxHash:     p.Hash,
		ObservedAt: p.ObservedAt,
	}
	perf := make([]PerfRecord, 0)

	from, err1 := NormalizeAddress(p.From)
	to, err2 := NormalizeAddress(p.To)
	if err1 != nil || err2 != nil {
		return best, perf
	}

	e.mu.RLock()
	defer e.mu.RUnlock()

	candidates := [][2]string{}
	if _, ok := e.victims[from]; ok {
		candidates = append(candidates, [2]string{from, to})
	}
	if _, ok := e.victims[to]; ok {
		candidates = append(candidates, [2]string{to, from})
	}

	bestLastSeen := time.Time{}
	totalScored := 0
	for _, c := range candidates {
		victim := c[0]
		lookalike := c[1]
		idx := e.indices[victim]
		if idx == nil {
			continue
		}
		candidateIDs := e.candidateIDs(idx, p, lookalike, mode, cumulative)

		scored := 0
		for r := range candidateIDs {
			if r == lookalike {
				continue
			}
			histories := idx.Recipients[r]
			ap := prefixMatchNibbles(lookalike, r)
			as := suffixMatchNibbles(lookalike, r)
			cp, score, ok, activeScored := e.bestScoringCounterparty(p, histories, lookalike, ap, as)
			scored += activeScored
			if !ok {
				continue
			}
			if !best.Found || score.Total > best.Score.Total || (score.Total == best.Score.Total && cp.LastSeen.After(bestLastSeen)) {
				best = ScoreResult{
					TxHash:           p.Hash,
					Victim:           victim,
					Lookalike:        lookalike,
					MatchedRecipient: r,
					ObservedAt:       p.ObservedAt,
					MatchedPrefix:    ap,
					MatchedSuffix:    as,
					Score:            score,
					Found:            true,
				}
				bestLastSeen = cp.LastSeen
			}
		}
		perf = append(perf, PerfRecord{
			TxHash:           p.Hash,
			Victim:           victim,
			LookupLatencyMs:  time.Since(started).Seconds() * 1000,
			CandidatesScored: scored,
		})
		totalScored += scored
	}
	best.CandidatesScored = totalScored
	return best, perf
}

func (e *Engine) candidateIDs(idx *victimIndex, p PendingTx, lookalike string, mode trieCandidateMode, cumulative bool) map[string]struct{} {
	prefKey := lookalike[:min(e.cfg.KP, len(lookalike))]
	suffKey := lookalike[len(lookalike)-min(e.cfg.KS, len(lookalike)):]

	var prefSet, suffSet map[string]int
	if cumulative {
		prefSet = idx.Prefix.CandidateIDsByDepthRange(prefKey, clampDepth(e.cfg.MinPrefixDepth, e.cfg.KP))
		suffSet = idx.Suffix.CandidateIDsByDepthRange(reverseString(suffKey), clampDepth(e.cfg.MinSuffixDepth, e.cfg.KS))
	} else {
		prefSet = idx.Prefix.CandidateIDsByPrefix(prefKey)
		suffSet = idx.Suffix.CandidateIDsByPrefix(reverseString(suffKey))
	}
	prefSet = e.pruneCandidates(p, idx, prefSet)
	suffSet = e.pruneCandidates(p, idx, suffSet)

	candidateIDs := map[string]struct{}{}
	switch mode {
	case candidateUnion:
		for r := range prefSet {
			candidateIDs[r] = struct{}{}
		}
		for r := range suffSet {
			candidateIDs[r] = struct{}{}
		}
	case candidatePrefixOnly:
		for r := range prefSet {
			candidateIDs[r] = struct{}{}
		}
	case candidateSuffixOnly:
		for r := range suffSet {
			candidateIDs[r] = struct{}{}
		}
	default:
		for r := range prefSet {
			if _, ok := suffSet[r]; ok {
				candidateIDs[r] = struct{}{}
			}
		}
	}
	return candidateIDs
}

func (e *Engine) pruneCandidates(p PendingTx, idx *victimIndex, candidates map[string]int) map[string]int {
	limit := e.cfg.MaxCandidatesPerSide
	if limit <= 0 || len(candidates) <= limit {
		return candidates
	}

	type rankedCandidate struct {
		id    string
		freq  int
		score float64
	}
	ranked := make([]rankedCandidate, 0, len(candidates))
	pendingToken := normalizeTokenKey(p.TokenAddress)
	for id, trieFreq := range candidates {
		histories := idx.Recipients[id]
		freq := trieFreq
		score := float64(freq)
		for _, cp := range histories {
			if cp.ObservedFreq > freq {
				freq = cp.ObservedFreq
			}
			if pendingToken != "" && normalizeTokenKey(cp.Token) == pendingToken {
				score += 1_000_000
			} else if tokenContextScore(e.metadata, pendingToken, cp) >= 1.0 {
				score += 500_000
			}
			if !cp.LastSeen.IsZero() && !p.ObservedAt.IsZero() && !cp.LastSeen.After(p.ObservedAt) {
				days := p.ObservedAt.Sub(cp.LastSeen).Hours() / 24
				score += 10_000 / (1 + days)
			}
		}
		ranked = append(ranked, rankedCandidate{id: id, freq: freq, score: score})
	}
	sort.Slice(ranked, func(i, j int) bool {
		if ranked[i].score != ranked[j].score {
			return ranked[i].score > ranked[j].score
		}
		if ranked[i].freq != ranked[j].freq {
			return ranked[i].freq > ranked[j].freq
		}
		return ranked[i].id < ranked[j].id
	})

	out := make(map[string]int, limit)
	for i := 0; i < limit && i < len(ranked); i++ {
		out[ranked[i].id] = candidates[ranked[i].id]
	}
	return out
}

func (e *Engine) bestScoringCounterparty(p PendingTx, histories []Counterparty, lookalike string, ap int, as int) (Counterparty, ScoreBreakdown, bool, int) {
	best := Counterparty{}
	bestScore := ScoreBreakdown{}
	found := false
	scored := 0
	for _, cp := range histories {
		if !e.counterpartyActive(p, cp) {
			continue
		}
		scored++
		score := computeScore(e.cfg, e.metadata, p, cp, lookalike, ap, as)
		if !found || score.Total > bestScore.Total || (score.Total == bestScore.Total && cp.LastSeen.After(best.LastSeen)) {
			best = cp
			bestScore = score
			found = true
		}
	}
	return best, bestScore, found, scored
}

func (e *Engine) bestPriorRuleCounterparty(p PendingTx, histories []Counterparty, lookalike string, ap int, as int) (Counterparty, ScoreBreakdown, bool, int) {
	best := Counterparty{}
	bestScore := ScoreBreakdown{}
	found := false
	scored := 0
	for _, cp := range histories {
		if !e.counterpartyActive(p, cp) {
			continue
		}
		scored++
		if !priorPoisoningRule(e.cfg, e.metadata, p, cp) {
			continue
		}
		score := computeScore(e.cfg, e.metadata, p, cp, lookalike, ap, as)
		if !found || score.Total > bestScore.Total || (score.Total == bestScore.Total && cp.LastSeen.After(best.LastSeen)) {
			best = cp
			bestScore = score
			found = true
		}
	}
	return best, bestScore, found, scored
}

func (e *Engine) detectPriorRuleByTrieMode(p PendingTx, mode trieCandidateMode) ([]Alert, []PerfRecord) {
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

	candidates := [][2]string{}
	if _, ok := e.victims[from]; ok {
		candidates = append(candidates, [2]string{from, to})
	}
	if _, ok := e.victims[to]; ok {
		candidates = append(candidates, [2]string{to, from})
	}

	for _, c := range candidates {
		victim := c[0]
		lookalike := c[1]
		idx := e.indices[victim]
		if idx == nil {
			continue
		}
		prefKey := lookalike[:min(e.cfg.KP, len(lookalike))]
		suffKey := lookalike[len(lookalike)-min(e.cfg.KS, len(lookalike)):]
		prefSet := idx.Prefix.CandidateIDsByPrefix(prefKey)
		suffSet := idx.Suffix.CandidateIDsByPrefix(reverseString(suffKey))
		candidateIDs := map[string]struct{}{}

		switch mode {
		case candidateUnion:
			for r := range prefSet {
				candidateIDs[r] = struct{}{}
			}
			for r := range suffSet {
				candidateIDs[r] = struct{}{}
			}
		case candidatePrefixOnly:
			for r := range prefSet {
				candidateIDs[r] = struct{}{}
			}
		case candidateSuffixOnly:
			for r := range suffSet {
				candidateIDs[r] = struct{}{}
			}
		default:
			for r := range prefSet {
				if _, ok := suffSet[r]; ok {
					candidateIDs[r] = struct{}{}
				}
			}
		}

		scored := 0
		for r := range candidateIDs {
			if r == lookalike {
				continue
			}
			histories := idx.Recipients[r]
			ap := prefixMatchNibbles(lookalike, r)
			as := suffixMatchNibbles(lookalike, r)
			if ap < e.cfg.ThetaP || as < e.cfg.ThetaS {
				continue
			}
			cp, score, ok, activeScored := e.bestPriorRuleCounterparty(p, histories, lookalike, ap, as)
			scored += activeScored
			if ok {
				alerts = append(alerts, Alert{
					TxHash:           p.Hash,
					Victim:           victim,
					Lookalike:        lookalike,
					MatchedRecipient: r,
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

func (e *Engine) counterpartyActive(p PendingTx, cp Counterparty) bool {
	if p.ObservedAt.IsZero() || cp.LastSeen.IsZero() || cp.LastSeen.Equal(time.Unix(0, 0).UTC()) {
		return true
	}
	if cp.LastSeen.After(p.ObservedAt) {
		return false
	}
	if e.cfg.WindowDays <= 0 {
		return true
	}
	return p.ObservedAt.Sub(cp.LastSeen) <= time.Duration(e.cfg.WindowDays)*24*time.Hour
}

func clampDepth(depth int, maxDepth int) int {
	if depth <= 0 {
		return maxDepth
	}
	if depth > maxDepth {
		return maxDepth
	}
	return depth
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
