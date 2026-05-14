package detector

import (
	"errors"
	"strings"
)

func NormalizeAddress(addr string) (string, error) {
	a := strings.TrimSpace(strings.ToLower(addr))
	a = strings.TrimPrefix(a, "0x")
	if len(a) != 40 {
		return "", errors.New("address must have 40 hex chars")
	}
	for i := 0; i < len(a); i++ {
		c := a[i]
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			return "", errors.New("address contains non-hex character")
		}
	}
	return a, nil
}

func prefixMatchNibbles(a, b string) int {
	n := min(len(a), len(b))
	for i := 0; i < n; i++ {
		if a[i] != b[i] {
			return i
		}
	}
	return n
}

func suffixMatchNibbles(a, b string) int {
	n := min(len(a), len(b))
	for i := 1; i <= n; i++ {
		if a[len(a)-i] != b[len(b)-i] {
			return i - 1
		}
	}
	return n
}

func reverseString(s string) string {
	b := []byte(s)
	for i, j := 0, len(b)-1; i < j; i, j = i+1, j-1 {
		b[i], b[j] = b[j], b[i]
	}
	return string(b)
}
