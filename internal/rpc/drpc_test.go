package rpc

import "testing"

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
