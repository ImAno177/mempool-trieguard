package live

import (
	"time"

	"mempool-trieguard/internal/detector"
	"mempool-trieguard/internal/rpc"
)

func pendingFromRPCTransaction(tx rpc.RPCTransaction, observedAt time.Time) (detector.PendingTx, bool, string) {
	pending := detector.PendingTx{
		Hash:       tx.Hash,
		From:       tx.From,
		To:         tx.To,
		ObservedAt: observedAt.UTC(),
		Value:      rpc.HexToFloat(tx.Value),
	}
	if call, ok := rpc.ParseERC20TransferCall(tx.Input); ok {
		pending.TokenAddress = tx.To
		if call.From != "" {
			pending.From = call.From
		}
		pending.To = call.To
		pending.Value = call.Value
		pending.ValueRaw = call.Value
		return pending, true, call.Method
	}
	return pending, false, ""
}
