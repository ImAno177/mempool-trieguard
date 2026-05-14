package detector

// TrieNode stores candidate recipient IDs at every traversed node.
type TrieNode struct {
	Children map[byte]*TrieNode
	IDs      map[string]int
}

type Trie struct {
	Root *TrieNode
}

func NewTrie() *Trie {
	return &Trie{Root: &TrieNode{Children: map[byte]*TrieNode{}, IDs: map[string]int{}}}
}

func (t *Trie) Insert(key string, id string) {
	node := t.Root
	for i := 0; i < len(key); i++ {
		ch := key[i]
		next, ok := node.Children[ch]
		if !ok {
			next = &TrieNode{Children: map[byte]*TrieNode{}, IDs: map[string]int{}}
			node.Children[ch] = next
		}
		node = next
		node.IDs[id]++
	}
}

func (t *Trie) CandidateIDsByPrefix(key string) map[string]int {
	node := t.Root
	for i := 0; i < len(key); i++ {
		next, ok := node.Children[key[i]]
		if !ok {
			break
		}
		node = next
	}
	out := make(map[string]int, len(node.IDs))
	for k, v := range node.IDs {
		out[k] = v
	}
	return out
}

func (t *Trie) CandidateIDsByDepthRange(key string, minDepth int) map[string]int {
	node := t.Root
	out := map[string]int{}
	for i := 0; i < len(key); i++ {
		next, ok := node.Children[key[i]]
		if !ok {
			break
		}
		node = next
		depth := i + 1
		if depth < minDepth {
			continue
		}
		for id, freq := range node.IDs {
			out[id] += freq
		}
	}
	return out
}
