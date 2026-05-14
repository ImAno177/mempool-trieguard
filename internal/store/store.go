package store

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"time"

	"mempool-trieguard/internal/detector"

	_ "modernc.org/sqlite"
)

type Store struct {
	db *sql.DB
}

type ConfigVersion struct {
	ID        int64     `json:"id"`
	CreatedAt time.Time `json:"created_at"`
	Operator  string    `json:"operator"`
	SHA256    string    `json:"sha256"`
	Source    string    `json:"source"`
	ConfigYML string    `json:"config_yaml"`
	IsActive  bool      `json:"is_active"`
}

type RunRecord struct {
	ID        int64     `json:"id"`
	CreatedAt time.Time `json:"created_at"`
	Mode      string    `json:"mode"`
	Method    string    `json:"method"`
	Status    string    `json:"status"`
	Metrics   string    `json:"metrics_json"`
	Artifacts string    `json:"artifacts_json"`
	Notes     string    `json:"notes"`
}

func Open(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	s := &Store{db: db}
	if err := s.migrate(); err != nil {
		return nil, err
	}
	return s, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) migrate() error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS config_versions (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			created_at TEXT NOT NULL,
			operator TEXT NOT NULL,
			source TEXT NOT NULL,
			sha256 TEXT NOT NULL,
			config_yaml TEXT NOT NULL,
			is_active INTEGER NOT NULL DEFAULT 0
		);`,
		`CREATE TABLE IF NOT EXISTS alerts (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			created_at TEXT NOT NULL,
			tx_hash TEXT NOT NULL,
			victim TEXT NOT NULL,
			lookalike TEXT NOT NULL,
			matched_recipient TEXT NOT NULL,
			score_total REAL NOT NULL,
			reason TEXT NOT NULL,
			observed_at TEXT NOT NULL,
			raw_json TEXT NOT NULL
		);`,
		`CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at DESC);`,
		`CREATE TABLE IF NOT EXISTS run_history (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			created_at TEXT NOT NULL,
			mode TEXT NOT NULL,
			method TEXT NOT NULL,
			status TEXT NOT NULL,
			metrics_json TEXT NOT NULL,
			artifacts_json TEXT NOT NULL,
			notes TEXT NOT NULL
		);`,
	}
	for _, stmt := range stmts {
		if _, err := s.db.Exec(stmt); err != nil {
			return fmt.Errorf("migrate sqlite: %w", err)
		}
	}
	return nil
}

func (s *Store) SaveConfigVersion(operator, source, yml string, makeActive bool) (ConfigVersion, error) {
	now := time.Now().UTC()
	h := sha256.Sum256([]byte(yml))
	hash := hex.EncodeToString(h[:])
	tx, err := s.db.Begin()
	if err != nil {
		return ConfigVersion{}, err
	}
	defer tx.Rollback()

	if makeActive {
		if _, err := tx.Exec(`UPDATE config_versions SET is_active = 0`); err != nil {
			return ConfigVersion{}, err
		}
	}
	res, err := tx.Exec(`INSERT INTO config_versions(created_at, operator, source, sha256, config_yaml, is_active) VALUES(?,?,?,?,?,?)`, now.Format(time.RFC3339), operator, source, hash, yml, boolToInt(makeActive))
	if err != nil {
		return ConfigVersion{}, err
	}
	id, _ := res.LastInsertId()
	if err := tx.Commit(); err != nil {
		return ConfigVersion{}, err
	}
	return ConfigVersion{ID: id, CreatedAt: now, Operator: operator, SHA256: hash, Source: source, ConfigYML: yml, IsActive: makeActive}, nil
}

func (s *Store) ListConfigVersions(limit int) ([]ConfigVersion, error) {
	if limit <= 0 {
		limit = 50
	}
	rows, err := s.db.Query(`SELECT id, created_at, operator, source, sha256, config_yaml, is_active FROM config_versions ORDER BY id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []ConfigVersion{}
	for rows.Next() {
		var rec ConfigVersion
		var ts string
		var active int
		if err := rows.Scan(&rec.ID, &ts, &rec.Operator, &rec.Source, &rec.SHA256, &rec.ConfigYML, &active); err != nil {
			return nil, err
		}
		rec.CreatedAt, _ = time.Parse(time.RFC3339, ts)
		rec.IsActive = active == 1
		out = append(out, rec)
	}
	return out, nil
}

func (s *Store) SaveAlerts(alerts []detector.Alert) error {
	if len(alerts) == 0 {
		return nil
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stmt, err := tx.Prepare(`INSERT INTO alerts(created_at, tx_hash, victim, lookalike, matched_recipient, score_total, reason, observed_at, raw_json) VALUES(?,?,?,?,?,?,?,?,?)`)
	if err != nil {
		return err
	}
	defer stmt.Close()
	now := time.Now().UTC().Format(time.RFC3339)
	for _, a := range alerts {
		raw, _ := json.Marshal(a)
		if _, err := stmt.Exec(now, a.TxHash, a.Victim, a.Lookalike, a.MatchedRecipient, a.Score.Total, a.Reason, a.ObservedAt.UTC().Format(time.RFC3339), string(raw)); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (s *Store) ListAlerts(limit int) ([]detector.Alert, error) {
	if limit <= 0 {
		limit = 100
	}
	rows, err := s.db.Query(`SELECT raw_json FROM alerts ORDER BY id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []detector.Alert{}
	for rows.Next() {
		var raw string
		if err := rows.Scan(&raw); err != nil {
			return nil, err
		}
		var a detector.Alert
		if err := json.Unmarshal([]byte(raw), &a); err != nil {
			continue
		}
		out = append(out, a)
	}
	return out, nil
}

func (s *Store) SaveRun(mode, method, status, metricsJSON, artifactsJSON, notes string) error {
	_, err := s.db.Exec(`INSERT INTO run_history(created_at, mode, method, status, metrics_json, artifacts_json, notes) VALUES(?,?,?,?,?,?,?)`, time.Now().UTC().Format(time.RFC3339), mode, method, status, metricsJSON, artifactsJSON, notes)
	return err
}

func (s *Store) ListRuns(limit int) ([]RunRecord, error) {
	if limit <= 0 {
		limit = 100
	}
	rows, err := s.db.Query(`SELECT id, created_at, mode, method, status, metrics_json, artifacts_json, notes FROM run_history ORDER BY id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []RunRecord{}
	for rows.Next() {
		var rec RunRecord
		var ts string
		if err := rows.Scan(&rec.ID, &ts, &rec.Mode, &rec.Method, &rec.Status, &rec.Metrics, &rec.Artifacts, &rec.Notes); err != nil {
			return nil, err
		}
		rec.CreatedAt, _ = time.Parse(time.RFC3339, ts)
		out = append(out, rec)
	}
	return out, nil
}

func boolToInt(v bool) int {
	if v {
		return 1
	}
	return 0
}
