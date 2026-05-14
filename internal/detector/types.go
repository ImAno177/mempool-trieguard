package detector

import "time"

type Config struct {
	WindowDays           int
	KP                   int
	KS                   int
	ThetaP               int
	ThetaS               int
	MinPrefixDepth       int
	MinSuffixDepth       int
	MaxCandidatesPerSide int
	Tau                  float64
	Lambda               float64
	Weights              [5]float64
	TinyValue            float64
}

type Counterparty struct {
	Victim          string    `json:"victim"`
	Recipient       string    `json:"recipient"`
	Token           string    `json:"token"`
	TokenSymbol     string    `json:"token_symbol,omitempty"`
	TokenName       string    `json:"token_name,omitempty"`
	TokenDecimals   int       `json:"token_decimals,omitempty"`
	LastSeen        time.Time `json:"last_seen"`
	ObservedFreq    int       `json:"observed_freq"`
	MetadataMissing bool      `json:"metadata_missing,omitempty"`
}

type PendingTx struct {
	Hash            string    `json:"hash"`
	From            string    `json:"from"`
	To              string    `json:"to"`
	TokenAddress    string    `json:"token_address"`
	Value           float64   `json:"value"`
	ValueRaw        float64   `json:"value_raw,omitempty"`
	ValueNormalized float64   `json:"value_normalized,omitempty"`
	ObservedAt      time.Time `json:"observed_at"`
	Visible         *bool     `json:"visible,omitempty"`
}

type TokenMetadata struct {
	Address         string `json:"address"`
	Decimals        int    `json:"decimals"`
	Symbol          string `json:"symbol"`
	Name            string `json:"name"`
	MetadataMissing bool   `json:"metadata_missing,omitempty"`
}

type ScoreBreakdown struct {
	Address float64 `json:"address"`
	Type    float64 `json:"type"`
	Token   float64 `json:"token"`
	Time    float64 `json:"time"`
	Value   float64 `json:"value"`
	Total   float64 `json:"total"`
}

type Alert struct {
	TxHash            string         `json:"tx_hash"`
	Victim            string         `json:"victim"`
	Lookalike         string         `json:"lookalike"`
	MatchedRecipient  string         `json:"matched_recipient"`
	ObservedAt        time.Time      `json:"observed_at"`
	MatchedPrefix     int            `json:"matched_prefix"`
	MatchedSuffix     int            `json:"matched_suffix"`
	Score             ScoreBreakdown `json:"score"`
	Reason            string         `json:"reason"`
	SubscriptionTrace string         `json:"subscription_trace,omitempty"`
}

// PerfRecord is emitted for benchmark paths.
type PerfRecord struct {
	TxHash           string  `json:"tx_hash"`
	Victim           string  `json:"victim"`
	LookupLatencyMs  float64 `json:"lookup_latency_ms"`
	CandidatesScored int     `json:"candidates_scored"`
}
