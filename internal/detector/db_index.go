package detector

import (
	"database/sql"
	"fmt"
	"sort"

	_ "modernc.org/sqlite"
)

type dbCandidateIndex struct {
	db             *sql.DB
	prefixStmts    [displayPrefixNibbles + 1]*sql.Stmt
	suffixStmts    [displaySuffixNibbles + 1]*sql.Stmt
	kp             int
	ks             int
	minPrefixDepth int
	minSuffixDepth int
}

func (idx *dbCandidateIndex) Close() error {
	if idx == nil || idx.db == nil {
		return nil
	}
	for _, stmt := range idx.prefixStmts {
		if stmt != nil {
			_ = stmt.Close()
		}
	}
	for _, stmt := range idx.suffixStmts {
		if stmt != nil {
			_ = stmt.Close()
		}
	}
	return idx.db.Close()
}

func (e *Engine) ensureDBCandidateIndex() (*dbCandidateIndex, error) {
	e.dbMu.Lock()
	defer e.dbMu.Unlock()
	if e.dbIndex != nil {
		return e.dbIndex, nil
	}

	e.mu.RLock()
	rows := make([]dbCandidateRow, 0)
	for victim, idx := range e.indices {
		for recipient := range idx.Recipients {
			rows = append(rows, dbCandidateRow{
				victim:    victim,
				recipient: recipient,
			})
		}
	}
	e.mu.RUnlock()
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].victim != rows[j].victim {
			return rows[i].victim < rows[j].victim
		}
		return rows[i].recipient < rows[j].recipient
	})

	idx, err := newDBCandidateIndex(rows, e.cfg)
	if err != nil {
		return nil, err
	}
	e.dbIndex = idx
	return e.dbIndex, nil
}

type dbCandidateRow struct {
	victim    string
	recipient string
}

func newDBCandidateIndex(rows []dbCandidateRow, cfg Config) (*dbCandidateIndex, error) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		return nil, fmt.Errorf("open sqlite db index: %w", err)
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)

	stmts := []string{
		`PRAGMA journal_mode=OFF`,
		`PRAGMA synchronous=OFF`,
		`PRAGMA temp_store=MEMORY`,
		`CREATE TABLE counterparties (
			victim TEXT NOT NULL,
			recipient TEXT NOT NULL,
			prefix1 TEXT NOT NULL,
			prefix2 TEXT NOT NULL,
			prefix3 TEXT NOT NULL,
			prefix4 TEXT NOT NULL,
			prefix5 TEXT NOT NULL,
			prefix6 TEXT NOT NULL,
			suffix1 TEXT NOT NULL,
			suffix2 TEXT NOT NULL,
			suffix3 TEXT NOT NULL,
			suffix4 TEXT NOT NULL,
			suffix5 TEXT NOT NULL,
			suffix6 TEXT NOT NULL,
			PRIMARY KEY (victim, recipient)
		)`,
	}
	for _, stmt := range stmts {
		if _, err := db.Exec(stmt); err != nil {
			_ = db.Close()
			return nil, fmt.Errorf("init sqlite db index: %w", err)
		}
	}

	tx, err := db.Begin()
	if err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("begin sqlite db index load: %w", err)
	}
	insert, err := tx.Prepare(`INSERT OR IGNORE INTO counterparties(
		victim, recipient,
		prefix1, prefix2, prefix3, prefix4, prefix5, prefix6,
		suffix1, suffix2, suffix3, suffix4, suffix5, suffix6
	) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
	if err != nil {
		_ = tx.Rollback()
		_ = db.Close()
		return nil, fmt.Errorf("prepare sqlite db index load: %w", err)
	}
	for _, row := range rows {
		if _, err := insert.Exec(
			row.victim,
			row.recipient,
			prefixAtDepth(row.recipient, 1),
			prefixAtDepth(row.recipient, 2),
			prefixAtDepth(row.recipient, 3),
			prefixAtDepth(row.recipient, 4),
			prefixAtDepth(row.recipient, 5),
			prefixAtDepth(row.recipient, 6),
			suffixAtDepth(row.recipient, 1),
			suffixAtDepth(row.recipient, 2),
			suffixAtDepth(row.recipient, 3),
			suffixAtDepth(row.recipient, 4),
			suffixAtDepth(row.recipient, 5),
			suffixAtDepth(row.recipient, 6),
		); err != nil {
			_ = insert.Close()
			_ = tx.Rollback()
			_ = db.Close()
			return nil, fmt.Errorf("insert sqlite db index row: %w", err)
		}
	}
	if err := insert.Close(); err != nil {
		_ = tx.Rollback()
		_ = db.Close()
		return nil, fmt.Errorf("close sqlite db index insert: %w", err)
	}
	if err := tx.Commit(); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("commit sqlite db index load: %w", err)
	}

	for depth := 1; depth <= displayPrefixNibbles; depth++ {
		stmt := fmt.Sprintf(`CREATE INDEX idx_counterparties_prefix%d ON counterparties(victim, prefix%d, recipient)`, depth, depth)
		if _, err := db.Exec(stmt); err != nil {
			_ = db.Close()
			return nil, fmt.Errorf("create sqlite prefix index: %w", err)
		}
	}
	for depth := 1; depth <= displaySuffixNibbles; depth++ {
		stmt := fmt.Sprintf(`CREATE INDEX idx_counterparties_suffix%d ON counterparties(victim, suffix%d, recipient)`, depth, depth)
		if _, err := db.Exec(stmt); err != nil {
			_ = db.Close()
			return nil, fmt.Errorf("create sqlite suffix index: %w", err)
		}
	}

	idx := &dbCandidateIndex{
		db:             db,
		kp:             dbClampDepth(cfg.KP, displayPrefixNibbles),
		ks:             dbClampDepth(cfg.KS, displaySuffixNibbles),
		minPrefixDepth: dbClampDepth(cfg.MinPrefixDepth, displayPrefixNibbles),
		minSuffixDepth: dbClampDepth(cfg.MinSuffixDepth, displaySuffixNibbles),
	}
	if idx.minPrefixDepth > idx.kp {
		idx.minPrefixDepth = idx.kp
	}
	if idx.minSuffixDepth > idx.ks {
		idx.minSuffixDepth = idx.ks
	}
	for depth := 1; depth <= displayPrefixNibbles; depth++ {
		stmt, err := db.Prepare(fmt.Sprintf(`SELECT recipient FROM counterparties WHERE victim = ? AND prefix%d = ? ORDER BY recipient LIMIT ?`, depth))
		if err != nil {
			_ = idx.Close()
			return nil, fmt.Errorf("prepare sqlite prefix query: %w", err)
		}
		idx.prefixStmts[depth] = stmt
	}
	for depth := 1; depth <= displaySuffixNibbles; depth++ {
		stmt, err := db.Prepare(fmt.Sprintf(`SELECT recipient FROM counterparties WHERE victim = ? AND suffix%d = ? ORDER BY recipient LIMIT ?`, depth))
		if err != nil {
			_ = idx.Close()
			return nil, fmt.Errorf("prepare sqlite suffix query: %w", err)
		}
		idx.suffixStmts[depth] = stmt
	}
	return idx, nil
}

func dbClampDepth(value int, maxDepth int) int {
	if value <= 0 {
		return maxDepth
	}
	if value > maxDepth {
		return maxDepth
	}
	return value
}

func prefixAtDepth(addr string, depth int) string {
	if depth <= 0 {
		return ""
	}
	if depth > len(addr) {
		depth = len(addr)
	}
	return addr[:depth]
}

func suffixAtDepth(addr string, depth int) string {
	if depth <= 0 {
		return ""
	}
	if depth > len(addr) {
		depth = len(addr)
	}
	return addr[len(addr)-depth:]
}

func (idx *dbCandidateIndex) CandidateIDs(victim string, address string, sideLimit int) (map[string]struct{}, error) {
	victim, err := NormalizeAddress(victim)
	if err != nil {
		return map[string]struct{}{}, nil
	}
	lookalike, err := NormalizeAddress(address)
	if err != nil {
		return map[string]struct{}{}, nil
	}
	if sideLimit <= 0 {
		sideLimit = 4096
	}

	out := map[string]struct{}{}
	prefixSide := map[string]struct{}{}
	for depth := idx.kp; depth >= idx.minPrefixDepth && len(prefixSide) < sideLimit; depth-- {
		key := prefixAtDepth(lookalike, depth)
		remaining := sideLimit - len(prefixSide)
		if err := idx.querySideInto(prefixSide, out, idx.prefixStmts[depth], victim, key, remaining); err != nil {
			return nil, err
		}
	}
	suffixSide := map[string]struct{}{}
	for depth := idx.ks; depth >= idx.minSuffixDepth && len(suffixSide) < sideLimit; depth-- {
		key := suffixAtDepth(lookalike, depth)
		remaining := sideLimit - len(suffixSide)
		if err := idx.querySideInto(suffixSide, out, idx.suffixStmts[depth], victim, key, remaining); err != nil {
			return nil, err
		}
	}
	return out, nil
}

func (idx *dbCandidateIndex) querySideInto(side map[string]struct{}, out map[string]struct{}, stmt *sql.Stmt, victim string, key string, limit int) error {
	if stmt == nil || limit <= 0 {
		return nil
	}
	rows, err := stmt.Query(victim, key, limit)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var recipient string
		if err := rows.Scan(&recipient); err != nil {
			return err
		}
		side[recipient] = struct{}{}
		out[recipient] = struct{}{}
	}
	return rows.Err()
}
