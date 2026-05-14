import pyarrow.parquet as pq
p='/app/data/normalized/address_poisoning_ethereum.normalized.full.parquet'
pf=pq.ParquetFile(p)
rows=0; poisoning=0; intended=0; payoff=0; unconfirmed=0
victims=set(); tokens=set(); intended_keys=set()
cols=['from_addr','to_addr','is_sender_victim','token_addr','intended_addr','intended_transfer','zero_value_transfer','tiny_transfer','counterfeit_token_transfer','payoff_transfer','payoff_transfer_unconfirmed']
for i,batch in enumerate(pf.iter_batches(batch_size=200000, columns=cols),1):
    d=batch.to_pydict(); n=len(d['token_addr']); rows+=n
    for j in range(n):
        is_poison=bool(d['zero_value_transfer'][j] or d['tiny_transfer'][j] or d['counterfeit_token_transfer'][j])
        if is_poison: poisoning+=1
        if d['intended_transfer'][j]: intended+=1
        if d['payoff_transfer'][j]: payoff+=1
        if d['payoff_transfer_unconfirmed'][j]: unconfirmed+=1
        v=d['from_addr'][j] if d['is_sender_victim'][j] else d['to_addr'][j]
        if v: victims.add(v)
        t=d['token_addr'][j]
        if t: tokens.add(t)
        if d['intended_transfer'][j] and not is_poison and not d['payoff_transfer'][j] and not d['payoff_transfer_unconfirmed'][j] and d['intended_addr'][j]:
            intended_keys.add((v,d['intended_addr'][j],t))
    if i % 25 == 0:
        print({'batches': i, 'rows': rows, 'poisoning': poisoning, 'trusted_keys_from_labels': len(intended_keys), 'victims': len(victims), 'tokens': len(tokens)}, flush=True)
print({'rows': rows, 'poisoning': poisoning, 'intended': intended, 'payoff': payoff, 'payoff_unconfirmed': unconfirmed, 'unique_victims': len(victims), 'unique_tokens': len(tokens), 'trusted_keys_from_labels': len(intended_keys)})
