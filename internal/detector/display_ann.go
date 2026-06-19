package detector

import (
	"math"
	"math/rand"
	"sort"
	"sync"
)

const (
	displayPrefixNibbles   = 6
	displaySuffixNibbles   = 6
	displayVectorPositions = displayPrefixNibbles + displaySuffixNibbles
	displayNibbleValues    = 16
	displayVectorLength    = displayVectorPositions * displayNibbleValues

	dblshSeed = int64(1337)
	dblshL    = 8
	dblshK    = 10
	dblshC    = 1.5
	dblshBeta = 0.10

	lshAPGEdgeCap = 16
	lshAPGEF      = 64
)

type displayCode [displayVectorPositions]byte

type displayProjectionEntry struct {
	value float32
	point int
}

type displayANNIndex struct {
	ids         []string
	codes       []displayCode
	projections [][][]displayProjectionEntry
	graph       [][]int
	graphBuilt  bool
}

var (
	displayProjectionOnce    sync.Once
	displayProjectionWeights [][][][]float64
)

func displayCandidateCap(cfg Config) int {
	if cfg.MaxCandidatesPerSide <= 0 {
		return 4096
	}
	return max(1, cfg.MaxCandidatesPerSide*2)
}

func (idx *victimIndex) ensureDisplayANNIndex() *displayANNIndex {
	idx.displayMu.Lock()
	defer idx.displayMu.Unlock()
	if idx.display != nil {
		return idx.display
	}
	ids := make([]string, 0, len(idx.Recipients))
	for id := range idx.Recipients {
		ids = append(ids, id)
	}
	idx.display = newDisplayANNIndex(ids)
	return idx.display
}

func newDisplayANNIndex(ids []string) *displayANNIndex {
	sort.Strings(ids)
	ann := &displayANNIndex{
		ids:   make([]string, 0, len(ids)),
		codes: make([]displayCode, 0, len(ids)),
	}
	for _, id := range ids {
		code, ok := displayCodeFromAddress(id)
		if !ok {
			continue
		}
		ann.ids = append(ann.ids, id)
		ann.codes = append(ann.codes, code)
	}
	ann.buildProjectionIndexes()
	return ann
}

func displayProjectionSpec() [][][][]float64 {
	displayProjectionOnce.Do(func() {
		rng := rand.New(rand.NewSource(dblshSeed))
		weights := make([][][][]float64, dblshL)
		for table := 0; table < dblshL; table++ {
			weights[table] = make([][][]float64, dblshK)
			for dim := 0; dim < dblshK; dim++ {
				weights[table][dim] = make([][]float64, displayVectorPositions)
				for pos := 0; pos < displayVectorPositions; pos++ {
					weights[table][dim][pos] = make([]float64, displayNibbleValues)
					for nib := 0; nib < displayNibbleValues; nib++ {
						weights[table][dim][pos][nib] = rng.NormFloat64()
					}
				}
			}
		}
		displayProjectionWeights = weights
	})
	return displayProjectionWeights
}

func (ann *displayANNIndex) buildProjectionIndexes() {
	ann.projections = make([][][]displayProjectionEntry, dblshL)
	for table := 0; table < dblshL; table++ {
		ann.projections[table] = make([][]displayProjectionEntry, dblshK)
		for dim := 0; dim < dblshK; dim++ {
			entries := make([]displayProjectionEntry, len(ann.codes))
			for point, code := range ann.codes {
				entries[point] = displayProjectionEntry{
					value: projectDisplayCode(code, table, dim),
					point: point,
				}
			}
			sort.Slice(entries, func(i, j int) bool {
				if entries[i].value == entries[j].value {
					return entries[i].point < entries[j].point
				}
				return entries[i].value < entries[j].value
			})
			ann.projections[table][dim] = entries
		}
	}
}

func displayCodeFromAddress(addr string) (displayCode, bool) {
	normalized, err := NormalizeAddress(addr)
	if err != nil {
		return displayCode{}, false
	}
	var out displayCode
	for i := 0; i < displayPrefixNibbles; i++ {
		nib, ok := hexNibble(normalized[i])
		if !ok {
			return displayCode{}, false
		}
		out[i] = nib
	}
	for i := 0; i < displaySuffixNibbles; i++ {
		nib, ok := hexNibble(normalized[len(normalized)-displaySuffixNibbles+i])
		if !ok {
			return displayCode{}, false
		}
		out[displayPrefixNibbles+i] = nib
	}
	return out, true
}

func displayAddressVector(addr string) ([displayVectorLength]float64, bool) {
	code, ok := displayCodeFromAddress(addr)
	if !ok {
		return [displayVectorLength]float64{}, false
	}
	var out [displayVectorLength]float64
	for pos, nib := range code {
		out[pos*displayNibbleValues+int(nib)] = 1
	}
	return out, true
}

func hexNibble(ch byte) (byte, bool) {
	switch {
	case ch >= '0' && ch <= '9':
		return ch - '0', true
	case ch >= 'a' && ch <= 'f':
		return ch - 'a' + 10, true
	default:
		return 0, false
	}
}

func projectDisplayCode(code displayCode, table int, dim int) float32 {
	weights := displayProjectionSpec()[table][dim]
	sum := 0.0
	for pos, nib := range code {
		sum += weights[pos][int(nib)]
	}
	return float32(sum)
}

func displayMismatch(a displayCode, b displayCode) int {
	dist := 0
	for i := range a {
		if a[i] != b[i] {
			dist++
		}
	}
	return dist
}

func (ann *displayANNIndex) CandidateIDsDBLSH(address string, limit int) map[string]struct{} {
	code, ok := displayCodeFromAddress(address)
	if !ok {
		return map[string]struct{}{}
	}
	points := ann.queryDBLSHCode(code, limit)
	return ann.pointIDs(points)
}

func (ann *displayANNIndex) CandidateIDsAPG(address string, limit int) map[string]struct{} {
	code, ok := displayCodeFromAddress(address)
	if !ok {
		return map[string]struct{}{}
	}
	ann.ensureAPG()
	points := ann.queryAPGCode(code, limit)
	return ann.pointIDs(points)
}

func (ann *displayANNIndex) pointIDs(points []int) map[string]struct{} {
	out := make(map[string]struct{}, len(points))
	for _, point := range points {
		if point >= 0 && point < len(ann.ids) {
			out[ann.ids[point]] = struct{}{}
		}
	}
	return out
}

func (ann *displayANNIndex) queryDBLSHCode(code displayCode, limit int) []int {
	if len(ann.ids) == 0 {
		return nil
	}
	if limit <= 0 || limit > len(ann.ids) {
		limit = len(ann.ids)
	}
	target := int(math.Ceil(dblshBeta * float64(len(ann.ids))))
	if target < 1 {
		target = 1
	}
	if target > limit {
		target = limit
	}

	votes := map[int]int{}
	radius := float32(1.0)
	for iter := 0; iter < 12 && len(votes) < target; iter++ {
		for table := 0; table < dblshL; table++ {
			for point := range ann.queryTableHypercube(code, table, radius) {
				votes[point]++
			}
		}
		radius *= float32(dblshC)
	}
	if len(votes) == 0 {
		return nil
	}
	return ann.rankPointMap(code, votes, limit)
}

type projectionRange struct {
	dim   int
	start int
	end   int
}

func (ann *displayANNIndex) queryTableHypercube(code displayCode, table int, radius float32) map[int]struct{} {
	ranges := make([]projectionRange, 0, dblshK)
	for dim := 0; dim < dblshK; dim++ {
		entries := ann.projections[table][dim]
		q := projectDisplayCode(code, table, dim)
		start := sort.Search(len(entries), func(i int) bool {
			return entries[i].value >= q-radius
		})
		end := sort.Search(len(entries), func(i int) bool {
			return entries[i].value > q+radius
		})
		if start >= end {
			return map[int]struct{}{}
		}
		ranges = append(ranges, projectionRange{dim: dim, start: start, end: end})
	}
	sort.Slice(ranges, func(i, j int) bool {
		return ranges[i].end-ranges[i].start < ranges[j].end-ranges[j].start
	})

	counts := map[int]int{}
	first := ranges[0]
	for _, entry := range ann.projections[table][first.dim][first.start:first.end] {
		counts[entry.point] = 1
	}
	for _, r := range ranges[1:] {
		for _, entry := range ann.projections[table][r.dim][r.start:r.end] {
			if counts[entry.point] > 0 {
				counts[entry.point]++
			}
		}
	}

	out := map[int]struct{}{}
	for point, count := range counts {
		if count == dblshK {
			out[point] = struct{}{}
		}
	}
	return out
}

type rankedDisplayPoint struct {
	point int
	dist  int
	votes int
	id    string
}

func (ann *displayANNIndex) rankPointMap(code displayCode, votes map[int]int, limit int) []int {
	ranked := make([]rankedDisplayPoint, 0, len(votes))
	for point, voteCount := range votes {
		if point < 0 || point >= len(ann.codes) {
			continue
		}
		ranked = append(ranked, rankedDisplayPoint{
			point: point,
			dist:  displayMismatch(code, ann.codes[point]),
			votes: voteCount,
			id:    ann.ids[point],
		})
	}
	sort.Slice(ranked, func(i, j int) bool {
		if ranked[i].dist != ranked[j].dist {
			return ranked[i].dist < ranked[j].dist
		}
		if ranked[i].votes != ranked[j].votes {
			return ranked[i].votes > ranked[j].votes
		}
		return ranked[i].id < ranked[j].id
	})
	if limit > len(ranked) {
		limit = len(ranked)
	}
	out := make([]int, 0, limit)
	for i := 0; i < limit; i++ {
		out = append(out, ranked[i].point)
	}
	return out
}

func (ann *displayANNIndex) ensureAPG() {
	if ann.graphBuilt {
		return
	}
	graph := make([][]int, len(ann.ids))
	proposalCap := lshAPGEdgeCap * 4
	for point, code := range ann.codes {
		proposals := ann.queryDBLSHCode(code, proposalCap)
		votes := map[int]int{}
		for _, candidate := range proposals {
			if candidate != point {
				votes[candidate]++
			}
		}
		if len(votes) < lshAPGEdgeCap && len(ann.ids) <= 256 {
			for candidate := range ann.ids {
				if candidate != point {
					votes[candidate]++
				}
			}
		}
		graph[point] = ann.rankPointMap(code, votes, lshAPGEdgeCap)
	}
	ann.graph = graph
	ann.graphBuilt = true
}

func (ann *displayANNIndex) queryAPGCode(code displayCode, limit int) []int {
	if len(ann.ids) == 0 {
		return nil
	}
	if limit <= 0 || limit > len(ann.ids) {
		limit = len(ann.ids)
	}
	entryPoints := ann.queryDBLSHCode(code, lshAPGEF)
	if len(entryPoints) == 0 {
		entryPoints = []int{0}
	}

	visited := map[int]int{}
	frontier := make([]rankedDisplayPoint, 0, len(entryPoints))
	for _, point := range entryPoints {
		if point < 0 || point >= len(ann.ids) {
			continue
		}
		frontier = append(frontier, rankedDisplayPoint{
			point: point,
			dist:  displayMismatch(code, ann.codes[point]),
			id:    ann.ids[point],
		})
	}

	for len(frontier) > 0 && len(visited) < lshAPGEF {
		sort.Slice(frontier, func(i, j int) bool {
			if frontier[i].dist != frontier[j].dist {
				return frontier[i].dist < frontier[j].dist
			}
			return frontier[i].id < frontier[j].id
		})
		current := frontier[0]
		frontier = frontier[1:]
		if _, ok := visited[current.point]; ok {
			continue
		}
		visited[current.point] = 1
		if current.point < len(ann.graph) {
			for _, next := range ann.graph[current.point] {
				if _, ok := visited[next]; ok {
					continue
				}
				frontier = append(frontier, rankedDisplayPoint{
					point: next,
					dist:  displayMismatch(code, ann.codes[next]),
					id:    ann.ids[next],
				})
			}
		}
	}
	if len(visited) == 0 {
		return ann.queryDBLSHCode(code, limit)
	}
	return ann.rankPointMap(code, visited, limit)
}
