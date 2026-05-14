package config

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

// AppConfig controls both local benchmark and VPS live runtime.
type AppConfig struct {
	Mode              string         `yaml:"mode"`
	ListenAddr        string         `yaml:"listen_addr"`
	DataDir           string         `yaml:"data_dir"`
	DBPath            string         `yaml:"db_path"`
	ProtectedAccounts string         `yaml:"protected_accounts_path"`
	MaxAlertsInMemory int            `yaml:"max_alerts_in_memory"`
	BasicAuth         BasicAuth      `yaml:"basic_auth"`
	DRPC              DRPCConfig     `yaml:"drpc"`
	Detector          DetectorConfig `yaml:"detector"`
	Benchmark         BenchConfig    `yaml:"benchmark"`
	Live              LiveConfig     `yaml:"live"`
}

type BasicAuth struct {
	User string `yaml:"user"`
	Pass string `yaml:"pass"`
}

type DRPCConfig struct {
	HTTPURL string `yaml:"http_url"`
	WSSURL  string `yaml:"wss_url"`
	Key     string `yaml:"key"`
}

type DetectorConfig struct {
	WindowDays           int       `yaml:"window_days"`
	KP                   int       `yaml:"kp"`
	KS                   int       `yaml:"ks"`
	ThetaP               int       `yaml:"theta_p"`
	ThetaS               int       `yaml:"theta_s"`
	MinPrefixDepth       int       `yaml:"min_prefix_depth"`
	MinSuffixDepth       int       `yaml:"min_suffix_depth"`
	MaxCandidatesPerSide int       `yaml:"max_candidates_per_side"`
	Tau                  float64   `yaml:"tau"`
	Lambda               float64   `yaml:"lambda"`
	Weights              []float64 `yaml:"weights"`
	TinyValue            float64   `yaml:"tiny_value"`
}

type BenchConfig struct {
	DelayProfilesSeconds []int `yaml:"delay_profiles_seconds"`
	BootstrapSamples     int   `yaml:"bootstrap_samples"`
	BenchmarkRuns        int   `yaml:"benchmark_runs"`
	RandomSeed           int64 `yaml:"random_seed"`
}

type LiveConfig struct {
	SubscriptionName string `yaml:"subscription_name"`
}

func Default() AppConfig {
	return AppConfig{
		Mode:              "local-benchmark",
		ListenAddr:        ":8080",
		DataDir:           "./data",
		DBPath:            "./data/app.db",
		ProtectedAccounts: "./configs/protected_accounts.json",
		MaxAlertsInMemory: 2000,
		BasicAuth: BasicAuth{
			User: "admin",
			Pass: "change-me",
		},
		DRPC: DRPCConfig{
			HTTPURL: "",
			WSSURL:  "",
			Key:     "",
		},
		Detector: DetectorConfig{
			WindowDays:           30,
			KP:                   6,
			KS:                   6,
			ThetaP:               4,
			ThetaS:               4,
			MinPrefixDepth:       3,
			MinSuffixDepth:       3,
			MaxCandidatesPerSide: 2048,
			Tau:                  0.70,
			Lambda:               7 * 24 * 3600,
			Weights:              []float64{0.30, 0.20, 0.20, 0.15, 0.15},
			TinyValue:            10.0,
		},
		Benchmark: BenchConfig{
			DelayProfilesSeconds: []int{5, 15, 30},
			BootstrapSamples:     10000,
			BenchmarkRuns:        30,
			RandomSeed:           42,
		},
		Live: LiveConfig{SubscriptionName: "drpc_pendingTransactions"},
	}
}

func Load(path string) (AppConfig, error) {
	cfg := Default()

	if path != "" {
		b, err := os.ReadFile(path)
		if err != nil {
			return cfg, fmt.Errorf("read config: %w", err)
		}
		if err := yaml.Unmarshal(b, &cfg); err != nil {
			return cfg, fmt.Errorf("parse config yaml: %w", err)
		}
	}

	overrideFromEnv(&cfg)

	if err := validate(cfg); err != nil {
		return cfg, err
	}
	return cfg, nil
}

func overrideFromEnv(cfg *AppConfig) {
	setStr := func(env string, dst *string) {
		if v := strings.TrimSpace(os.Getenv(env)); v != "" {
			*dst = v
		}
	}
	setInt := func(env string, dst *int) {
		if v := strings.TrimSpace(os.Getenv(env)); v != "" {
			if iv, err := strconv.Atoi(v); err == nil {
				*dst = iv
			}
		}
	}
	setF64 := func(env string, dst *float64) {
		if v := strings.TrimSpace(os.Getenv(env)); v != "" {
			if fv, err := strconv.ParseFloat(v, 64); err == nil {
				*dst = fv
			}
		}
	}

	setStr("APP_MODE", &cfg.Mode)
	setStr("APP_LISTEN_ADDR", &cfg.ListenAddr)
	setStr("APP_DATA_DIR", &cfg.DataDir)
	setStr("APP_DB_PATH", &cfg.DBPath)
	setStr("APP_PROTECTED_ACCOUNTS_PATH", &cfg.ProtectedAccounts)
	setInt("APP_MAX_ALERTS_IN_MEMORY", &cfg.MaxAlertsInMemory)
	setStr("APP_BASIC_AUTH_USER", &cfg.BasicAuth.User)
	setStr("APP_BASIC_AUTH_PASS", &cfg.BasicAuth.Pass)

	setStr("DRPC_HTTP_URL", &cfg.DRPC.HTTPURL)
	setStr("DRPC_WSS_URL", &cfg.DRPC.WSSURL)
	setStr("DRPC_KEY", &cfg.DRPC.Key)

	setInt("DETECTOR_WINDOW_DAYS", &cfg.Detector.WindowDays)
	setInt("DETECTOR_KP", &cfg.Detector.KP)
	setInt("DETECTOR_KS", &cfg.Detector.KS)
	setInt("DETECTOR_THETA_P", &cfg.Detector.ThetaP)
	setInt("DETECTOR_THETA_S", &cfg.Detector.ThetaS)
	setInt("DETECTOR_MIN_PREFIX_DEPTH", &cfg.Detector.MinPrefixDepth)
	setInt("DETECTOR_MIN_SUFFIX_DEPTH", &cfg.Detector.MinSuffixDepth)
	setInt("DETECTOR_MAX_CANDIDATES_PER_SIDE", &cfg.Detector.MaxCandidatesPerSide)
	setF64("DETECTOR_TAU", &cfg.Detector.Tau)
	setF64("DETECTOR_LAMBDA", &cfg.Detector.Lambda)
	setF64("DETECTOR_TINY_VALUE", &cfg.Detector.TinyValue)
}

func validate(cfg AppConfig) error {
	if cfg.Detector.KP <= 0 || cfg.Detector.KS <= 0 {
		return errors.New("detector kp/ks must be > 0")
	}
	if cfg.Detector.ThetaP <= 0 || cfg.Detector.ThetaS <= 0 {
		return errors.New("detector theta_p/theta_s must be > 0")
	}
	if cfg.Detector.MinPrefixDepth <= 0 || cfg.Detector.MinPrefixDepth > cfg.Detector.KP {
		return errors.New("detector min_prefix_depth must be between 1 and kp")
	}
	if cfg.Detector.MinSuffixDepth <= 0 || cfg.Detector.MinSuffixDepth > cfg.Detector.KS {
		return errors.New("detector min_suffix_depth must be between 1 and ks")
	}
	if cfg.Detector.MaxCandidatesPerSide < 0 {
		return errors.New("detector max_candidates_per_side must be >= 0")
	}
	if cfg.Detector.Tau < 0 || cfg.Detector.Tau > 1.5 {
		return errors.New("detector tau must be between 0 and 1.5")
	}
	if len(cfg.Detector.Weights) != 5 {
		return errors.New("detector weights must have 5 values")
	}
	if cfg.Live.SubscriptionName == "" {
		return errors.New("live subscription_name is required")
	}
	if cfg.MaxAlertsInMemory < 100 {
		return errors.New("max_alerts_in_memory must be >= 100")
	}
	return nil
}
