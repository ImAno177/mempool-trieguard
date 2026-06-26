package rpc

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

const subscriptionRawMessageBuffer = 65536

type Client struct {
	HTTPURL string
	WSSURL  string
	Key     string
	HTTP    *http.Client
}

func NewClient(httpURL, wssURL, key string) *Client {
	return &Client{
		HTTPURL: strings.TrimSpace(httpURL),
		WSSURL:  strings.TrimSpace(wssURL),
		Key:     strings.TrimSpace(key),
		HTTP:    &http.Client{Timeout: 25 * time.Second},
	}
}

type rpcRequest struct {
	JSONRPC string      `json:"jsonrpc"`
	Method  string      `json:"method"`
	Params  interface{} `json:"params"`
	ID      int         `json:"id"`
}

type rpcResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      int             `json:"id"`
	Result  json.RawMessage `json:"result"`
	Error   *rpcError       `json:"error,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func (c *Client) Call(ctx context.Context, method string, params interface{}, out interface{}) error {
	if c.HTTPURL == "" {
		return errors.New("drpc http url is empty")
	}
	body, _ := json.Marshal(rpcRequest{JSONRPC: "2.0", Method: method, Params: params, ID: 1})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.HTTPURL, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.Key != "" {
		req.Header.Set("Drpc-Key", c.Key)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("rpc status %d: %s", resp.StatusCode, string(b))
	}
	var rr rpcResponse
	if err := json.NewDecoder(resp.Body).Decode(&rr); err != nil {
		return err
	}
	if rr.Error != nil {
		return fmt.Errorf("rpc error %d: %s", rr.Error.Code, rr.Error.Message)
	}
	if out == nil {
		return nil
	}
	if len(rr.Result) == 0 {
		return errors.New("empty rpc result")
	}
	if err := json.Unmarshal(rr.Result, out); err != nil {
		return err
	}
	return nil
}

func (c *Client) BlockNumber(ctx context.Context) (uint64, error) {
	var hexStr string
	if err := c.Call(ctx, "eth_blockNumber", []interface{}{}, &hexStr); err != nil {
		return 0, err
	}
	return parseHexUint64(hexStr)
}

func parseHexUint64(s string) (uint64, error) {
	s = strings.TrimSpace(strings.TrimPrefix(strings.ToLower(s), "0x"))
	if s == "" {
		return 0, nil
	}
	v, ok := new(big.Int).SetString(s, 16)
	if !ok {
		return 0, fmt.Errorf("invalid hex uint64 %q", s)
	}
	return v.Uint64(), nil
}

func HexToFloat(s string) float64 {
	s = strings.TrimSpace(strings.TrimPrefix(strings.ToLower(s), "0x"))
	if s == "" {
		return 0
	}
	v, ok := new(big.Int).SetString(s, 16)
	if !ok {
		return 0
	}
	f, _ := new(big.Float).SetInt(v).Float64()
	return f
}

type RPCTransaction struct {
	Hash                 string `json:"hash"`
	From                 string `json:"from"`
	To                   string `json:"to"`
	Nonce                string `json:"nonce,omitempty"`
	Value                string `json:"value"`
	Input                string `json:"input"`
	Gas                  string `json:"gas,omitempty"`
	GasPrice             string `json:"gasPrice,omitempty"`
	MaxFeePerGas         string `json:"maxFeePerGas,omitempty"`
	MaxPriorityFeePerGas string `json:"maxPriorityFeePerGas,omitempty"`
	BlockHash            string `json:"blockHash,omitempty"`
	BlockNumber          string `json:"blockNumber,omitempty"`
	TransactionIndex     string `json:"transactionIndex,omitempty"`
}

func (c *Client) GetTransactionByHash(ctx context.Context, hash string) (RPCTransaction, error) {
	var tx RPCTransaction
	err := c.Call(ctx, "eth_getTransactionByHash", []interface{}{hash}, &tx)
	return tx, err
}

type RPCTransactionReceipt struct {
	TransactionHash string `json:"transactionHash"`
	BlockHash       string `json:"blockHash"`
	BlockNumber     string `json:"blockNumber"`
	Status          string `json:"status"`
}

func (c *Client) GetTransactionReceipt(ctx context.Context, hash string) (RPCTransactionReceipt, error) {
	var receipt RPCTransactionReceipt
	err := c.Call(ctx, "eth_getTransactionReceipt", []interface{}{hash}, &receipt)
	return receipt, err
}

type RPCBlock struct {
	Number       string           `json:"number"`
	Hash         string           `json:"hash"`
	Timestamp    string           `json:"timestamp"`
	Transactions []RPCTransaction `json:"transactions"`
}

func (c *Client) GetBlockByNumber(ctx context.Context, tag string, fullTransactions bool) (RPCBlock, error) {
	var block RPCBlock
	err := c.Call(ctx, "eth_getBlockByNumber", []interface{}{tag, fullTransactions}, &block)
	return block, err
}

type Subscription struct {
	Conn           *websocket.Conn
	SubscriptionID string
	RawMessages    chan json.RawMessage
	Errors         chan error
	cancelOnce     sync.Once
	cancelFn       context.CancelFunc
	done           <-chan struct{}
	dropped        atomic.Int64
}

func (c *Client) Subscribe(ctx context.Context, methodName string) (*Subscription, error) {
	if c.WSSURL == "" {
		return nil, errors.New("drpc wss url is empty")
	}
	dialer := websocket.Dialer{HandshakeTimeout: 20 * time.Second}
	headers := http.Header{}
	if c.Key != "" {
		headers.Set("Drpc-Key", c.Key)
	}
	conn, _, err := dialer.DialContext(ctx, c.WSSURL, headers)
	if err != nil {
		return nil, err
	}
	if err := conn.WriteJSON(map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "eth_subscribe",
		"params":  []interface{}{methodName},
	}); err != nil {
		_ = conn.Close()
		return nil, err
	}
	var ack map[string]interface{}
	if err := conn.ReadJSON(&ack); err != nil {
		_ = conn.Close()
		return nil, err
	}
	subID, _ := ack["result"].(string)
	if subID == "" {
		_ = conn.Close()
		b, _ := json.Marshal(ack)
		return nil, fmt.Errorf("missing subscription id in ack: %s", string(b))
	}

	ctxRun, cancel := context.WithCancel(ctx)
	s := &Subscription{
		Conn:           conn,
		SubscriptionID: subID,
		RawMessages:    make(chan json.RawMessage, subscriptionRawMessageBuffer),
		Errors:         make(chan error, 8),
		cancelFn:       cancel,
		done:           ctxRun.Done(),
	}
	go s.readLoop(ctxRun)
	return s, nil
}

func (s *Subscription) Close() {
	s.cancelOnce.Do(func() {
		if s.cancelFn != nil {
			s.cancelFn()
		}
		_ = s.Conn.WriteJSON(map[string]interface{}{
			"jsonrpc": "2.0",
			"id":      2,
			"method":  "eth_unsubscribe",
			"params":  []interface{}{s.SubscriptionID},
		})
		_ = s.Conn.Close()
	})
}

func (s *Subscription) readLoop(ctx context.Context) {
	defer close(s.RawMessages)
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		_, msg, err := s.Conn.ReadMessage()
		if err != nil {
			select {
			case s.Errors <- err:
			default:
			}
			return
		}
		var envelope struct {
			Method string `json:"method"`
			Params struct {
				Result json.RawMessage `json:"result"`
			} `json:"params"`
		}
		if err := json.Unmarshal(msg, &envelope); err != nil {
			continue
		}
		if envelope.Method != "eth_subscription" {
			continue
		}
		s.enqueueRawMessage(envelope.Params.Result)
	}
}

func (s *Subscription) enqueueRawMessage(raw json.RawMessage) bool {
	select {
	case <-s.done:
		return false
	default:
	}
	select {
	case s.RawMessages <- raw:
		return true
	case <-s.done:
		return false
	}
}

func (s *Subscription) DroppedCount() int64 {
	if s == nil {
		return 0
	}
	return s.dropped.Load()
}

// DecodePendingResult supports either tx hash string or tx object payload.
func DecodePendingResult(raw json.RawMessage) (hash string, tx RPCTransaction, hasObject bool, err error) {
	if len(raw) == 0 {
		return "", RPCTransaction{}, false, errors.New("empty pending payload")
	}
	if raw[0] == '"' {
		if err := json.Unmarshal(raw, &hash); err != nil {
			return "", RPCTransaction{}, false, err
		}
		return hash, RPCTransaction{}, false, nil
	}
	if err := json.Unmarshal(raw, &tx); err != nil {
		return "", RPCTransaction{}, false, err
	}
	return tx.Hash, tx, true, nil
}

type ERC20TransferCall struct {
	Method string
	From   string
	To     string
	Value  float64
}

func ParseERC20TransferCall(input string) (ERC20TransferCall, bool) {
	s := strings.TrimPrefix(strings.ToLower(strings.TrimSpace(input)), "0x")
	if len(s) < 8 {
		return ERC20TransferCall{}, false
	}
	selector := s[:8]
	switch selector {
	case "a9059cbb": // transfer(address,uint256)
		if len(s) < 8+64+64 {
			return ERC20TransferCall{}, false
		}
		toHex := s[8+24 : 8+64]
		valHex := s[8+64 : 8+64+64]
		value, ok := parseABIUintFloat(valHex)
		if !ok {
			return ERC20TransferCall{}, false
		}
		return ERC20TransferCall{Method: "transfer", To: "0x" + toHex, Value: value}, true
	case "23b872dd": // transferFrom(address,address,uint256)
		if len(s) < 8+64+64+64 {
			return ERC20TransferCall{}, false
		}
		fromHex := s[8+24 : 8+64]
		toHex := s[8+64+24 : 8+64+64]
		valHex := s[8+64+64 : 8+64+64+64]
		value, ok := parseABIUintFloat(valHex)
		if !ok {
			return ERC20TransferCall{}, false
		}
		return ERC20TransferCall{Method: "transferFrom", From: "0x" + fromHex, To: "0x" + toHex, Value: value}, true
	default:
		return ERC20TransferCall{}, false
	}
}

func ParseERC20TransferInput(input string) (to string, value float64, ok bool) {
	call, ok := ParseERC20TransferCall(input)
	return call.To, call.Value, ok
}

func parseABIUintFloat(hexValue string) (float64, bool) {
	bi, ok := new(big.Int).SetString(hexValue, 16)
	if !ok {
		return 0, false
	}
	f, _ := new(big.Float).SetInt(bi).Float64()
	return f, true
}
