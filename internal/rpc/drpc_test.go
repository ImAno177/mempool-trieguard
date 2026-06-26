package rpc

import (
	"encoding/json"
	"testing"
	"time"
)

func TestParseERC20TransferCallTransfer(t *testing.T) {
	input := "0xa9059cbb" +
		"0000000000000000000000001111111111111111111111111111111111111111" +
		"000000000000000000000000000000000000000000000000000000000000007b"
	call, ok := ParseERC20TransferCall(input)
	if !ok {
		t.Fatalf("expected transfer calldata to parse")
	}
	if call.Method != "transfer" {
		t.Fatalf("method=%s", call.Method)
	}
	if call.To != "0x1111111111111111111111111111111111111111" {
		t.Fatalf("to=%s", call.To)
	}
	if call.Value != 123 {
		t.Fatalf("value=%f", call.Value)
	}
}

func TestParseERC20TransferCallTransferFrom(t *testing.T) {
	input := "0x23b872dd" +
		"0000000000000000000000001111111111111111111111111111111111111111" +
		"0000000000000000000000002222222222222222222222222222222222222222" +
		"00000000000000000000000000000000000000000000000000000000000001f4"
	call, ok := ParseERC20TransferCall(input)
	if !ok {
		t.Fatalf("expected transferFrom calldata to parse")
	}
	if call.Method != "transferFrom" {
		t.Fatalf("method=%s", call.Method)
	}
	if call.From != "0x1111111111111111111111111111111111111111" {
		t.Fatalf("from=%s", call.From)
	}
	if call.To != "0x2222222222222222222222222222222222222222" {
		t.Fatalf("to=%s", call.To)
	}
	if call.Value != 500 {
		t.Fatalf("value=%f", call.Value)
	}
}

func TestSubscriptionBufferedEnqueueDoesNotDropWithoutReceiver(t *testing.T) {
	done := make(chan struct{})
	sub := &Subscription{RawMessages: make(chan json.RawMessage, subscriptionRawMessageBuffer), done: done}
	if !sub.enqueueRawMessage(json.RawMessage(`"0xabc"`)) {
		t.Fatalf("enqueue should succeed into subscription buffer")
	}
	if got := sub.DroppedCount(); got != 0 {
		t.Fatalf("dropped count = %d, want 0", got)
	}
	got := <-sub.RawMessages
	if string(got) != `"0xabc"` {
		t.Fatalf("raw message = %s", string(got))
	}
}

func TestSubscriptionEnqueueStopsWhenClosed(t *testing.T) {
	done := make(chan struct{})
	close(done)
	sub := &Subscription{RawMessages: make(chan json.RawMessage), done: done}
	result := make(chan bool, 1)
	go func() {
		result <- sub.enqueueRawMessage(json.RawMessage(`"0xabc"`))
	}()
	select {
	case ok := <-result:
		if ok {
			t.Fatalf("enqueue should stop when subscription is closed")
		}
	case <-time.After(time.Second):
		t.Fatalf("enqueue did not stop after subscription close")
	}
}
