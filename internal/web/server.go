package web

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"gopkg.in/yaml.v3"

	"mempool-trieguard/internal/config"
	"mempool-trieguard/internal/live"
	"mempool-trieguard/internal/rpc"
	"mempool-trieguard/internal/store"
)

type Server struct {
	cfg       config.AppConfig
	st        *store.Store
	liveSvc   *live.Service
	rpcClient *rpc.Client
}

type datasetEntry struct {
	Name string
	Path string
	Size int64
}

func NewServer(cfg config.AppConfig, st *store.Store, liveSvc *live.Service) (*Server, error) {
	return &Server{cfg: cfg, st: st, liveSvc: liveSvc, rpcClient: rpc.NewClient(cfg.DRPC.HTTPURL, cfg.DRPC.WSSURL, cfg.DRPC.Key)}, nil
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleDashboard)
	mux.HandleFunc("/datasets", s.handleDatasets)
	mux.HandleFunc("/runs", s.handleRuns)
	mux.HandleFunc("/live/start", s.handleLiveStart)
	mux.HandleFunc("/live/stop", s.handleLiveStop)
	mux.HandleFunc("/live/status", s.handleLiveStatus)
	mux.HandleFunc("/live/alerts", s.handleLiveAlerts)
	mux.HandleFunc("/config", s.handleConfig)
	mux.HandleFunc("/config/import", s.handleConfigImport)
	mux.HandleFunc("/api/smoke", s.handleSmoke)
	return s.basicAuth(mux)
}

func (s *Server) basicAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		user, pass, ok := r.BasicAuth()
		if !ok || user != s.cfg.BasicAuth.User || pass != s.cfg.BasicAuth.Pass {
			w.Header().Set("WWW-Authenticate", `Basic realm="mempool-trieguard"`)
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) handleDashboard(w http.ResponseWriter, r *http.Request) {
	status := s.liveSvc.Status()
	alerts := s.liveSvc.Alerts(20)
	writeJSON(w, map[string]interface{}{
		"now":    time.Now().UTC(),
		"config": s.cfg,
		"status": status,
		"alerts": alerts,
	})
}

func (s *Server) handleDatasets(w http.ResponseWriter, r *http.Request) {
	entries := []datasetEntry{}
	root := filepath.Join(".", "dataset")
	_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info == nil || info.IsDir() {
			return nil
		}
		entries = append(entries, datasetEntry{Name: info.Name(), Path: path, Size: info.Size()})
		return nil
	})
	writeJSON(w, map[string]interface{}{
		"now":      time.Now().UTC(),
		"datasets": entries,
	})
}

func (s *Server) handleRuns(w http.ResponseWriter, r *http.Request) {
	runs, err := s.st.ListRuns(100)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	if len(runs) == 0 {
		runs = append(runs, s.collectRunsFromResults()...)
	}
	writeJSON(w, map[string]interface{}{
		"now":  time.Now().UTC(),
		"runs": runs,
	})
}

func (s *Server) handleLiveStart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if err := s.liveSvc.Start(context.Background()); err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	writeJSON(w, map[string]interface{}{"ok": true, "status": s.liveSvc.Status()})
}

func (s *Server) handleLiveStop(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	s.liveSvc.Stop()
	writeJSON(w, map[string]interface{}{"ok": true, "status": s.liveSvc.Status()})
}

func (s *Server) handleLiveStatus(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, s.liveSvc.Status())
}

func (s *Server) handleLiveAlerts(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, s.liveSvc.Alerts(200))
}

func (s *Server) handleConfig(w http.ResponseWriter, r *http.Request) {
	cfgs, err := s.st.ListConfigVersions(100)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSON(w, map[string]interface{}{
		"now":             time.Now().UTC(),
		"current_config":  s.cfg,
		"config_versions": cfgs,
	})
}

func (s *Server) handleConfigImport(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if err := r.ParseMultipartForm(5 << 20); err != nil {
		http.Error(w, "parse form: "+err.Error(), http.StatusBadRequest)
		return
	}
	f, _, err := r.FormFile("config_file")
	if err != nil {
		http.Error(w, "missing config_file", http.StatusBadRequest)
		return
	}
	defer f.Close()
	body, _ := io.ReadAll(f)
	operator := strings.TrimSpace(r.FormValue("operator"))
	if operator == "" {
		operator = "ui"
	}

	dcfg, err := extractDetectorConfig(body, s.cfg)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.liveSvc.ApplyDetectorConfig(dcfg); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if _, err := s.st.SaveConfigVersion(operator, "import-best-config", string(body), true); err != nil {
		log.Printf("save config version failed: %v", err)
	}
	writeJSON(w, map[string]interface{}{"ok": true, "detector": dcfg})
}

func (s *Server) handleSmoke(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	block, err := s.rpcClient.BlockNumber(ctx)
	if err != nil {
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, map[string]interface{}{"ok": true, "block_number": block})
}

func extractDetectorConfig(yml []byte, base config.AppConfig) (config.DetectorConfig, error) {
	out := base.Detector

	var full struct {
		Detector config.DetectorConfig `yaml:"detector"`
	}
	if err := yaml.Unmarshal(yml, &full); err == nil {
		if full.Detector.KP > 0 {
			out = full.Detector
			if len(full.Detector.Weights) == 0 {
				out.Weights = base.Detector.Weights
			}
			if len(full.Detector.ContextWeights) == 0 {
				out.ContextWeights = base.Detector.ContextWeights
			}
			if len(full.Detector.LogisticWeights) == 0 {
				out.LogisticWeights = base.Detector.LogisticWeights
			}
			fillDetectorDefaults(&out, base.Detector)
			return out, nil
		}
	}

	var direct config.DetectorConfig
	if err := yaml.Unmarshal(yml, &direct); err == nil {
		if direct.KP > 0 {
			out = direct
			if len(direct.Weights) == 0 {
				out.Weights = base.Detector.Weights
			}
			if len(direct.ContextWeights) == 0 {
				out.ContextWeights = base.Detector.ContextWeights
			}
			if len(direct.LogisticWeights) == 0 {
				out.LogisticWeights = base.Detector.LogisticWeights
			}
			fillDetectorDefaults(&out, base.Detector)
			return out, nil
		}
	}
	return out, fmt.Errorf("unable to parse detector config from yaml")
}

func fillDetectorDefaults(dst *config.DetectorConfig, base config.DetectorConfig) {
	if dst.MinPrefixDepth <= 0 {
		dst.MinPrefixDepth = base.MinPrefixDepth
	}
	if dst.MinSuffixDepth <= 0 {
		dst.MinSuffixDepth = base.MinSuffixDepth
	}
	if dst.MaxCandidatesPerSide <= 0 {
		dst.MaxCandidatesPerSide = base.MaxCandidatesPerSide
	}
	if dst.ScoreMode == "" {
		dst.ScoreMode = base.ScoreMode
	}
	if dst.LogisticIntercept == 0 {
		dst.LogisticIntercept = base.LogisticIntercept
	}
	if dst.AddressScoreMode == "" {
		dst.AddressScoreMode = base.AddressScoreMode
	}
	if dst.AddressBalanceAlpha == 0 {
		dst.AddressBalanceAlpha = base.AddressBalanceAlpha
	}
	if dst.AddressBalanceGamma == 0 {
		dst.AddressBalanceGamma = base.AddressBalanceGamma
	}
	if dst.ContextGateBase <= 0 {
		dst.ContextGateBase = base.ContextGateBase
	}
}

func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

func (s *Server) collectRunsFromResults() []store.RunRecord {
	glob := filepath.Join("results", "method_runs", "delay_*", "summary_*.json")
	paths, _ := filepath.Glob(glob)
	out := make([]store.RunRecord, 0, len(paths))
	for _, p := range paths {
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		var payload struct {
			Metrics map[string]interface{} `json:"metrics"`
		}
		if err := json.Unmarshal(b, &payload); err != nil {
			continue
		}
		method, _ := payload.Metrics["method"].(string)
		ts := time.Now().UTC()
		if fi, err := os.Stat(p); err == nil {
			ts = fi.ModTime().UTC()
		}
		out = append(out, store.RunRecord{
			ID:        int64(len(out) + 1),
			CreatedAt: ts,
			Mode:      "local-benchmark",
			Method:    method,
			Status:    "artifact-only",
			Metrics:   string(b),
			Artifacts: p,
			Notes:     "loaded from results directory",
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt.After(out[j].CreatedAt) })
	return out
}
