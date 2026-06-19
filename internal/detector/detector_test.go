package detector

import (
	"math"
	"testing"
	"time"
)

func TestNormalizeAddress(t *testing.T) {
	addr, err := NormalizeAddress("0xA0b86991c6218b36c1d19D4a2E9eb0cE3606eB48")
	if err != nil {
		t.Fatalf("normalize failed: %v", err)
	}
	if addr != "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48" {
		t.Fatalf("unexpected normalized address: %s", addr)
	}
}

func TestTrieInsertLookup(t *testing.T) {
	tr := NewTrie()
	tr.Insert("abc123", "r1")
	tr.Insert("abc456", "r2")
	ids := tr.CandidateIDsByPrefix("abc999")
	if len(ids) != 2 {
		t.Fatalf("expected 2 ids, got %d", len(ids))
	}
}

func TestDetectFindsAlert(t *testing.T) {
	cfg := Config{
		KP:        6,
		KS:        6,
		ThetaP:    3,
		ThetaS:    4,
		Tau:       0.3,
		Lambda:    3600,
		Weights:   [5]float64{0.4, 0.2, 0.2, 0.1, 0.1},
		TinyValue: 10,
	}
	eng := NewEngine(cfg)
	cp := []Counterparty{{
		Victim:       "0xccb720974f3809b8fc33c68f51bba62ba8e4bb6e",
		Recipient:    "0x12e49c72b0aca9b163fcf4025114e02907475b4a",
		Token:        "0xdac17f958d2ee523a2206206994597c13d831ec7",
		LastSeen:     time.Now().Add(-30 * time.Minute),
		ObservedFreq: 2,
	}}
	if err := eng.LoadCounterparties(cp); err != nil {
		t.Fatalf("load cps failed: %v", err)
	}

	pending := PendingTx{
		Hash:         "0xabc",
		From:         "0xccb720974f3809b8fc33c68f51bba62ba8e4bb6e",
		To:           "0x12e55e286e6557ffc194d0497a773dddab475b4a",
		TokenAddress: "0xdac17f958d2ee523a2206206994597c13d831ec7",
		Value:        0,
		ObservedAt:   time.Now(),
	}
	alerts, _ := eng.Detect(pending)
	if len(alerts) == 0 {
		t.Fatalf("expected alert but got none")
	}
}

func TestDetectIsScoreFirstWithoutThetaHardGate(t *testing.T) {
	cfg := Config{
		KP:        4,
		KS:        4,
		ThetaP:    8,
		ThetaS:    8,
		Tau:       0.55,
		Lambda:    3600,
		Weights:   [5]float64{0.2, 0.3, 0.2, 0.2, 0.1},
		TinyValue: 10,
	}
	eng := NewEngine(cfg)
	now := time.Now().UTC()
	cps := []Counterparty{{
		Victim:       "0x1111111111111111111111111111111111111111",
		Recipient:    "0xaaaa00000000000000000000000000000000bbbb",
		Token:        "0x2222222222222222222222222222222222222222",
		LastSeen:     now.Add(-10 * time.Minute),
		ObservedFreq: 1,
	}}
	if err := eng.LoadCounterparties(cps); err != nil {
		t.Fatalf("load cps failed: %v", err)
	}
	alerts, _ := eng.Detect(PendingTx{
		Hash:         "0xscorefirst",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xaaaa99999999999999999999999999999999bbbb",
		TokenAddress: "0x3333333333333333333333333333333333333333",
		Value:        0,
		ObservedAt:   now,
	})
	if len(alerts) == 0 {
		t.Fatalf("expected risk score to trigger alert even though theta_p/theta_s exceed matched nibbles")
	}
	if alerts[0].MatchedPrefix >= cfg.ThetaP || alerts[0].MatchedSuffix >= cfg.ThetaS {
		t.Fatalf("test fixture invalid: expected prefix/suffix below theta, got %d/%d", alerts[0].MatchedPrefix, alerts[0].MatchedSuffix)
	}
}

func TestDetectFiltersCounterpartiesByObservedTimeAndWindow(t *testing.T) {
	cfg := Config{
		WindowDays: 30,
		KP:         4,
		KS:         4,
		Tau:        0.25,
		Lambda:     86400,
		Weights:    [5]float64{0.4, 0.2, 0.1, 0.2, 0.1},
		TinyValue:  10,
	}
	now := time.Date(2026, 5, 13, 10, 0, 0, 0, time.UTC)
	baseCP := Counterparty{
		Victim:       "0x1111111111111111111111111111111111111111",
		Recipient:    "0xaaaa00000000000000000000000000000000bbbb",
		Token:        "0x2222222222222222222222222222222222222222",
		ObservedFreq: 1,
	}
	pending := PendingTx{
		Hash:         "0xtime",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xaaaa99999999999999999999999999999999bbbb",
		TokenAddress: "0x3333333333333333333333333333333333333333",
		Value:        0,
		ObservedAt:   now,
	}

	cases := []struct {
		name      string
		lastSeen  time.Time
		wantAlert bool
	}{
		{name: "future_last_seen_rejected", lastSeen: now.Add(time.Hour), wantAlert: false},
		{name: "outside_window_rejected", lastSeen: now.Add(-31 * 24 * time.Hour), wantAlert: false},
		{name: "inside_window_allowed", lastSeen: now.Add(-24 * time.Hour), wantAlert: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			eng := NewEngine(cfg)
			cp := baseCP
			cp.LastSeen = tc.lastSeen
			if err := eng.LoadCounterparties([]Counterparty{cp}); err != nil {
				t.Fatalf("load cps failed: %v", err)
			}
			alerts, _ := eng.Detect(pending)
			got := len(alerts) > 0
			if got != tc.wantAlert {
				t.Fatalf("alert=%v, want %v", got, tc.wantAlert)
			}
		})
	}
}

func TestCounterfeitTokenMetadataRaisesTokenScore(t *testing.T) {
	cfg := Config{
		KP:        4,
		KS:        4,
		Tau:       0.70,
		Lambda:    3600,
		Weights:   [5]float64{0.2, 0.1, 0.45, 0.15, 0.1},
		TinyValue: 10,
	}
	now := time.Now().UTC()
	eng := NewEngine(cfg)
	eng.SetTokenMetadata([]TokenMetadata{
		{Address: "0x2222222222222222222222222222222222222222", Decimals: 6, Symbol: "USDT", Name: "Tether USD"},
		{Address: "0x3333333333333333333333333333333333333333", Decimals: 6, Symbol: "USDT", Name: "Tether USD"},
	})
	if err := eng.LoadCounterparties([]Counterparty{{
		Victim:       "0x1111111111111111111111111111111111111111",
		Recipient:    "0xaaaa00000000000000000000000000000000bbbb",
		Token:        "0x2222222222222222222222222222222222222222",
		TokenSymbol:  "USDT",
		TokenName:    "Tether USD",
		LastSeen:     now.Add(-5 * time.Minute),
		ObservedFreq: 1,
	}}); err != nil {
		t.Fatalf("load cps failed: %v", err)
	}
	alerts, _ := eng.Detect(PendingTx{
		Hash:         "0xcounterfeit",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xaaaa99999999999999999999999999999999bbbb",
		TokenAddress: "0x3333333333333333333333333333333333333333",
		Value:        1,
		ObservedAt:   now,
	})
	if len(alerts) == 0 {
		t.Fatalf("expected counterfeit token metadata to push score above tau")
	}
	if alerts[0].Score.Token != 1 {
		t.Fatalf("expected token score 1 for counterfeit metadata, got %.3f", alerts[0].Score.Token)
	}
}

func TestContextGateUsesConditionedTime(t *testing.T) {
	now := time.Date(2026, 6, 15, 12, 0, 0, 0, time.UTC)
	cfg := Config{
		KP:              6,
		KS:              6,
		Lambda:          3600,
		TinyValue:       10,
		ScoreMode:       "context_gate",
		ContextGateBase: 0.80,
		ContextWeights:  [4]float64{0, 0, 0, 1},
	}
	pending := PendingTx{
		Hash:         "0xconditionedtime",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xaaaa99999999999999999999999999999999bbbb",
		TokenAddress: "0x3333333333333333333333333333333333333333",
		Value:        0,
		ObservedAt:   now,
	}
	recent := Counterparty{
		Victim:    "0x1111111111111111111111111111111111111111",
		Recipient: "0xaaaa00000000000000000000000000000000bbbb",
		Token:     "0x2222222222222222222222222222222222222222",
		LastSeen:  now.Add(-time.Minute),
	}
	old := recent
	old.LastSeen = now.Add(-24 * time.Hour)

	recentScore := computeScore(cfg, nil, pending, recent, "aaaa99999999999999999999999999999999bbbb", 4, 4)
	oldScore := computeScore(cfg, nil, pending, old, "aaaa99999999999999999999999999999999bbbb", 4, 4)
	if recentScore.Total <= oldScore.Total {
		t.Fatalf("expected conditioned recency to raise score: recent=%.6f old=%.6f", recentScore.Total, oldScore.Total)
	}
	if recentScore.Total > recentScore.Address {
		t.Fatalf("context gate must not exceed address score: total=%.6f addr=%.6f", recentScore.Total, recentScore.Address)
	}
}

func TestAddressScoreModesUseOnlyTheirDeclaredEvidence(t *testing.T) {
	cfg := Config{
		KP:                  6,
		KS:                  6,
		AddressBalanceAlpha: 0.50,
		AddressBalanceGamma: 1.0,
	}
	raw := float64(4+1) / float64(cfg.KP+cfg.KS)

	cfg.AddressScoreMode = "prefix_only"
	prefix := addressEvidenceScore(cfg, raw, 4, 1)
	if math.Abs(prefix-(4.0/6.0)) > 1e-9 {
		t.Fatalf("prefix_only score=%.12f, want %.12f", prefix, 4.0/6.0)
	}

	cfg.AddressScoreMode = "suffix_only"
	suffix := addressEvidenceScore(cfg, raw, 4, 1)
	if math.Abs(suffix-(1.0/6.0)) > 1e-9 {
		t.Fatalf("suffix_only score=%.12f, want %.12f", suffix, 1.0/6.0)
	}

	cfg.AddressScoreMode = "balanced"
	balanced := addressEvidenceScore(cfg, raw, 4, 1)
	if balanced >= raw {
		t.Fatalf("balanced score should penalize one-sided evidence: balanced=%.12f raw=%.12f", balanced, raw)
	}
	if balanced <= suffix || balanced >= prefix {
		t.Fatalf("balanced score=%.12f should remain between suffix=%.12f and prefix=%.12f", balanced, suffix, prefix)
	}
}

func TestTrieAndLinearAgreeOnAlerts(t *testing.T) {
	cfg := Config{
		KP:        4,
		KS:        4,
		Tau:       0.45,
		Lambda:    3600,
		Weights:   [5]float64{0.3, 0.25, 0.2, 0.15, 0.1},
		TinyValue: 10,
	}
	now := time.Now().UTC()
	eng := NewEngine(cfg)
	if err := eng.LoadCounterparties([]Counterparty{{
		Victim:       "0x1111111111111111111111111111111111111111",
		Recipient:    "0xaaaa00000000000000000000000000000000bbbb",
		Token:        "0x2222222222222222222222222222222222222222",
		LastSeen:     now.Add(-5 * time.Minute),
		ObservedFreq: 1,
	}}); err != nil {
		t.Fatalf("load cps failed: %v", err)
	}
	pending := PendingTx{
		Hash:         "0xagree",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xaaaa99999999999999999999999999999999bbbb",
		TokenAddress: "0x3333333333333333333333333333333333333333",
		Value:        0,
		ObservedAt:   now,
	}
	trieAlerts, _ := eng.Detect(pending)
	linearAlerts, _ := eng.DetectLinear(pending)
	if len(trieAlerts) != len(linearAlerts) {
		t.Fatalf("trie alerts=%d, linear alerts=%d", len(trieAlerts), len(linearAlerts))
	}
	if len(trieAlerts) > 0 && trieAlerts[0].MatchedRecipient != linearAlerts[0].MatchedRecipient {
		t.Fatalf("matched recipient mismatch: trie=%s linear=%s", trieAlerts[0].MatchedRecipient, linearAlerts[0].MatchedRecipient)
	}
}

func TestDetectUsesPrefixOrSuffixUnionRetrieval(t *testing.T) {
	cfg := Config{
		KP:        6,
		KS:        6,
		Tau:       0.50,
		Lambda:    3600,
		Weights:   [5]float64{0.25, 0.25, 0.2, 0.2, 0.1},
		TinyValue: 10,
	}
	now := time.Now().UTC()
	eng := NewEngine(cfg)
	if err := eng.LoadCounterparties([]Counterparty{{
		Victim:       "0x1111111111111111111111111111111111111111",
		Recipient:    "0xaaaaaa0000000000000000000000000000bbbbbb",
		Token:        "0x2222222222222222222222222222222222222222",
		LastSeen:     now.Add(-5 * time.Minute),
		ObservedFreq: 1,
	}}); err != nil {
		t.Fatalf("load cps failed: %v", err)
	}
	pending := PendingTx{
		Hash:         "0xsuffixunion",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xffffff0000000000000000000000000000bbbbbb",
		TokenAddress: "0x3333333333333333333333333333333333333333",
		Value:        0,
		ObservedAt:   now,
	}
	intersectionAlerts, _ := eng.DetectIntersection(pending)
	if len(intersectionAlerts) != 0 {
		t.Fatalf("fixture invalid: intersection retrieval should miss suffix-only candidate")
	}
	unionAlerts, _ := eng.Detect(pending)
	if len(unionAlerts) == 0 {
		t.Fatalf("expected union retrieval to score suffix-only candidate")
	}
}

func TestDetectUsesCumulativeMultiDepthRetrieval(t *testing.T) {
	cfg := Config{
		KP:                   6,
		KS:                   6,
		MinPrefixDepth:       3,
		MinSuffixDepth:       3,
		MaxCandidatesPerSide: 2048,
		Tau:                  0.65,
		Lambda:               3600,
		Weights:              [5]float64{0.2, 0.3, 0.2, 0.2, 0.1},
		TinyValue:            10,
	}
	now := time.Now().UTC()
	eng := NewEngine(cfg)
	if err := eng.LoadCounterparties([]Counterparty{
		{
			Victim:       "0x1111111111111111111111111111111111111111",
			Recipient:    "0xabc1110000000000000000000000000000aaaaaa",
			Token:        "0x2222222222222222222222222222222222222222",
			LastSeen:     now.Add(-5 * time.Minute),
			ObservedFreq: 1,
		},
		{
			Victim:       "0x1111111111111111111111111111111111111111",
			Recipient:    "0xabc9990000000000000000000000000000bbbbbb",
			Token:        "0x2222222222222222222222222222222222222222",
			LastSeen:     now.Add(time.Hour),
			ObservedFreq: 1,
		},
	}); err != nil {
		t.Fatalf("load cps failed: %v", err)
	}
	pending := PendingTx{
		Hash:         "0xcumulative",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xabc9999999999999999999999999999999cccccc",
		TokenAddress: "0x3333333333333333333333333333333333333333",
		Value:        0,
		ObservedAt:   now,
	}
	legacyAlerts, _ := eng.DetectLegacy(pending)
	if len(legacyAlerts) != 0 {
		t.Fatalf("fixture invalid: legacy deepest-node retrieval should miss the lower-depth candidate")
	}
	alerts, _ := eng.Detect(pending)
	if len(alerts) == 0 {
		t.Fatalf("expected cumulative retrieval to score lower-depth candidate")
	}
	if alerts[0].MatchedRecipient != "abc1110000000000000000000000000000aaaaaa" {
		t.Fatalf("unexpected matched recipient: %s", alerts[0].MatchedRecipient)
	}
}

func TestDetectDoesNotAlertExactTrustedRecipient(t *testing.T) {
	cfg := Config{
		KP:                   6,
		KS:                   6,
		MinPrefixDepth:       3,
		MinSuffixDepth:       3,
		MaxCandidatesPerSide: 2048,
		Tau:                  0.25,
		Lambda:               3600,
		Weights:              [5]float64{0.3, 0.2, 0.2, 0.15, 0.15},
		TinyValue:            10,
	}
	now := time.Now().UTC()
	eng := NewEngine(cfg)
	if err := eng.LoadCounterparties([]Counterparty{{
		Victim:       "0x1111111111111111111111111111111111111111",
		Recipient:    "0xabc1110000000000000000000000000000aaaaaa",
		Token:        "0x2222222222222222222222222222222222222222",
		LastSeen:     now.Add(-5 * time.Minute),
		ObservedFreq: 1,
	}}); err != nil {
		t.Fatalf("load cps failed: %v", err)
	}
	pending := PendingTx{
		Hash:         "0xexact",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xabc1110000000000000000000000000000aaaaaa",
		TokenAddress: "0x2222222222222222222222222222222222222222",
		Value:        1,
		ObservedAt:   now,
	}
	alerts, _ := eng.Detect(pending)
	if len(alerts) != 0 {
		t.Fatalf("exact trusted recipient should not alert, got %d alerts", len(alerts))
	}
	linearAlerts, _ := eng.DetectLinear(pending)
	if len(linearAlerts) != 0 {
		t.Fatalf("linear exact trusted recipient should not alert, got %d alerts", len(linearAlerts))
	}
}

func TestDisplayAddressVectorUsesPrefixSuffixOneHot(t *testing.T) {
	vec, ok := displayAddressVector("0xabcdef0000000000000000000000000000123456")
	if !ok {
		t.Fatalf("display vectorization failed")
	}
	ones := 0
	for _, value := range vec {
		if value == 1 {
			ones++
		} else if value != 0 {
			t.Fatalf("display vector should be one-hot, got value %.3f", value)
		}
	}
	if ones != displayVectorPositions {
		t.Fatalf("expected %d one-hot entries, got %d", displayVectorPositions, ones)
	}
	code, ok := displayCodeFromAddress("0xabcdef0000000000000000000000000000123456")
	if !ok {
		t.Fatalf("display code failed")
	}
	if code[0] != 10 || code[1] != 11 || code[2] != 12 || code[displayVectorPositions-1] != 6 {
		t.Fatalf("unexpected display code: %v", code)
	}
	if projectDisplayCode(code, 0, 0) != projectDisplayCode(code, 0, 0) {
		t.Fatalf("projection must be deterministic")
	}
}

func TestPaperBaselinesFindDisplayLookalike(t *testing.T) {
	cfg := Config{
		WindowDays:           30,
		KP:                   6,
		KS:                   6,
		MinPrefixDepth:       3,
		MinSuffixDepth:       3,
		MaxCandidatesPerSide: 8,
		Tau:                  0.50,
		Lambda:               3600,
		Weights:              [5]float64{0.25, 0.25, 0.2, 0.2, 0.1},
		TinyValue:            10,
	}
	now := time.Now().UTC()
	eng := NewEngine(cfg)
	if err := eng.LoadCounterparties([]Counterparty{
		{
			Victim:       "0x1111111111111111111111111111111111111111",
			Recipient:    "0xaaaaaa0000000000000000000000000000bbbbbb",
			Token:        "0x2222222222222222222222222222222222222222",
			LastSeen:     now.Add(-5 * time.Minute),
			ObservedFreq: 3,
		},
		{
			Victim:       "0x1111111111111111111111111111111111111111",
			Recipient:    "0x1234560000000000000000000000000000fedcba",
			Token:        "0x2222222222222222222222222222222222222222",
			LastSeen:     now.Add(-10 * time.Minute),
			ObservedFreq: 1,
		},
	}); err != nil {
		t.Fatalf("load cps failed: %v", err)
	}
	eng.PrebuildDisplayIndexes(true)
	if err := eng.PrebuildDBIndex(); err != nil {
		t.Fatalf("prebuild db index failed: %v", err)
	}

	pending := PendingTx{
		Hash:         "0xpaperbaseline",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xaaaaaa9999999999999999999999999999bbbbbb",
		TokenAddress: "0x3333333333333333333333333333333333333333",
		Value:        0,
		ObservedAt:   now,
	}
	for name, detect := range map[string]func(PendingTx) ([]Alert, []PerfRecord){
		"db_index":        eng.DetectDBIndex,
		"dblsh2_display":  eng.DetectDBLSHDisplay,
		"lsh_apg_display": eng.DetectLSHAPGDisplay,
	} {
		t.Run(name, func(t *testing.T) {
			alerts, perf := detect(pending)
			if len(alerts) == 0 {
				t.Fatalf("expected paper baseline alert")
			}
			if alerts[0].MatchedRecipient != "aaaaaa0000000000000000000000000000bbbbbb" {
				t.Fatalf("unexpected matched recipient: %s", alerts[0].MatchedRecipient)
			}
			if len(perf) == 0 || perf[0].CandidatesScored == 0 {
				t.Fatalf("expected candidates to be scored")
			}
		})
	}
}

func TestPaperBaselinesRespectExactMatchAndTimeWindow(t *testing.T) {
	cfg := Config{
		WindowDays:           30,
		KP:                   6,
		KS:                   6,
		MaxCandidatesPerSide: 8,
		Tau:                  0.25,
		Lambda:               3600,
		Weights:              [5]float64{0.3, 0.2, 0.2, 0.15, 0.15},
		TinyValue:            10,
	}
	now := time.Date(2026, 6, 14, 9, 0, 0, 0, time.UTC)
	baseCP := Counterparty{
		Victim:       "0x1111111111111111111111111111111111111111",
		Recipient:    "0xaaaaaa0000000000000000000000000000bbbbbb",
		Token:        "0x2222222222222222222222222222222222222222",
		ObservedFreq: 1,
	}

	cases := []struct {
		name     string
		lastSeen time.Time
		to       string
		wantHit  bool
	}{
		{
			name:     "exact_recipient_suppressed",
			lastSeen: now.Add(-time.Hour),
			to:       "0xaaaaaa0000000000000000000000000000bbbbbb",
			wantHit:  false,
		},
		{
			name:     "future_history_rejected",
			lastSeen: now.Add(time.Hour),
			to:       "0xaaaaaa9999999999999999999999999999bbbbbb",
			wantHit:  false,
		},
		{
			name:     "expired_history_rejected",
			lastSeen: now.Add(-31 * 24 * time.Hour),
			to:       "0xaaaaaa9999999999999999999999999999bbbbbb",
			wantHit:  false,
		},
		{
			name:     "active_history_alerts",
			lastSeen: now.Add(-time.Hour),
			to:       "0xaaaaaa9999999999999999999999999999bbbbbb",
			wantHit:  true,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			eng := NewEngine(cfg)
			cp := baseCP
			cp.LastSeen = tc.lastSeen
			if err := eng.LoadCounterparties([]Counterparty{cp}); err != nil {
				t.Fatalf("load cps failed: %v", err)
			}
			eng.PrebuildDisplayIndexes(true)
			if err := eng.PrebuildDBIndex(); err != nil {
				t.Fatalf("prebuild db index failed: %v", err)
			}
			pending := PendingTx{
				Hash:         "0xpaperwindow",
				From:         "0x1111111111111111111111111111111111111111",
				To:           tc.to,
				TokenAddress: "0x3333333333333333333333333333333333333333",
				Value:        0,
				ObservedAt:   now,
			}
			for name, detect := range map[string]func(PendingTx) ([]Alert, []PerfRecord){
				"db_index":        eng.DetectDBIndex,
				"dblsh2_display":  eng.DetectDBLSHDisplay,
				"lsh_apg_display": eng.DetectLSHAPGDisplay,
			} {
				alerts, _ := detect(pending)
				gotHit := len(alerts) > 0
				if gotHit != tc.wantHit {
					t.Fatalf("%s alert=%v, want %v", name, gotHit, tc.wantHit)
				}
			}
		})
	}
}

func TestDetectPriorRuleRequiresThetaAndPoisoningPattern(t *testing.T) {
	cfg := Config{
		KP:        6,
		KS:        6,
		ThetaP:    6,
		ThetaS:    6,
		Tau:       0.30,
		Lambda:    3600,
		Weights:   [5]float64{0.3, 0.2, 0.2, 0.15, 0.15},
		TinyValue: 10,
	}
	now := time.Now().UTC()
	eng := NewEngine(cfg)
	if err := eng.LoadCounterparties([]Counterparty{{
		Victim:       "0x1111111111111111111111111111111111111111",
		Recipient:    "0xaaaaaa0000000000000000000000000000bbbbbb",
		Token:        "0x2222222222222222222222222222222222222222",
		LastSeen:     now.Add(-5 * time.Minute),
		ObservedFreq: 1,
	}}); err != nil {
		t.Fatalf("load cps failed: %v", err)
	}

	alerts, _ := eng.DetectPriorRule(PendingTx{
		Hash:         "0xpriorhit",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xaaaaaa9999999999999999999999999999bbbbbb",
		TokenAddress: "0x3333333333333333333333333333333333333333",
		Value:        0,
		ObservedAt:   now,
	})
	if len(alerts) == 0 {
		t.Fatalf("expected prior rule alert for theta match and zero-value poisoning pattern")
	}

	misses, _ := eng.DetectPriorRule(PendingTx{
		Hash:         "0xpriorbenign",
		From:         "0x1111111111111111111111111111111111111111",
		To:           "0xaaaaaa9999999999999999999999999999bbbbbb",
		TokenAddress: "0x2222222222222222222222222222222222222222",
		Value:        100,
		ObservedAt:   now,
	})
	if len(misses) != 0 {
		t.Fatalf("expected prior rule to ignore non-tiny same-token transfer")
	}
}
