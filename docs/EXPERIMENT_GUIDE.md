# Huong dan thuc nghiem cho Mempool-TrieGuard

## Muc luc

- [Tai lieu lien quan](#tai-lieu-lien-quan)
- [1. Muc tieu](#1-muc-tieu)
- [2. Cau hoi nghien cuu va thuc nghiem tuong ung](#2-cau-hoi-nghien-cuu-va-thuc-nghiem-tuong-ung)
- [3. Moi truong](#3-moi-truong)
- [4. Du lieu](#4-du-lieu)
- [5. Hien thuc detector](#5-hien-thuc-detector)
- [6. Baseline](#6-baseline)
- [7. Chi so danh gia](#7-chi-so-danh-gia)
- [8. Thong ke](#8-thong-ke)
- [9. Bang ket qua can bao cao](#9-bang-ket-qua-can-bao-cao)
- [10. Loi thuong gap](#10-loi-thuong-gap)
- [11. Tien do de hoan thien bai bao](#11-tien-do-de-hoan-thien-bai-bao)
- [12. Ket qua hien tai](#12-ket-qua-hien-tai)

## Tai lieu lien quan

- [README](../README.md) - tong quan repo, cach build, cach chay benchmark va Docker.
- [Dataset notes](DATASET.md) - mo ta Parquet/caches can co tren may local.
- [Progress handoff](PROGRESS.md) - trang thai hien tai va viec can lam tiep cho sinh vien khac.
- [Root AGENTS](../AGENTS.md) - quy tac cho AI/code agent khi sua repo.

## 1. Muc tieu

Huong dan nay mo ta cach hien thuc va danh gia Mempool-TrieGuard, mot he thong canh bao som address poisoning dua tren mempool va prefix/suffix trie. Tat ca ket qua dinh luong phai duoc tao tu thuc nghiem that, khong duoc dien so lieu uoc doan vao bai bao.

## 2. Cau hoi nghien cuu va thuc nghiem tuong ung

| RQ | Cau hoi | Thuc nghiem |
|---|---|---|
| RQ1 | He thong co phat hien giao dich poisoning truoc khi duoc xac nhan khong? | Replay cac giao dich co nhan tu dataset cua Tsuchiya et al. va do thoi diem canh bao so voi block time. |
| RQ2 | Trie co giam latency so voi quet tuyen tinh, DB index, va LSH-style retrieval khong? | Full-label lookup replay va benchmark lookup voi 10, 100, 1.000, 10.000 counterparty tren moi vi. |
| RQ3 | Thanh phan nao cua risk score dong gop nhieu nhat? | Ablation LR tren cac tap feature co dinh: full address+type+token, address-only, no-type, no-token, prefix-only, suffix-only. |
| RQ4 | He thong ben vung the nao khi mempool khong day du? | Mo phong ty le mat giao dich pending 10%, 25%, 50% va do recall. |
| Live supplement | Public mempool feed co dap ung duoc latency va visibility cua detector khong? | Chay micro-benchmark live tren RPC/WSS provider that trong mot khoang thoi gian ngan; bao cao nhu bo sung, khong thay the replay RQ1-RQ4. |

Quy tac so sanh hien tai: cac baseline replay cu giu threshold da chon truoc (`tau=0.40` cho additive score), con dong Mempool-TrieGuard moi dung cong thuc LR nhe voi threshold validation `tau=0.901`. Tau sweep chi la phan phan tich calibration rieng, khong tune tren test set.

## 3. Moi truong

- He dieu hanh: Linux hoac macOS.
- Node.js 20 tro len cho ingestion WebSocket bang `ethers.js`.
- Python 3.11 tro len cho xu ly dataset, benchmark, thong ke.
- Go hoac Rust tuy chon neu muon hien thuc detector hieu nang cao.
- Ethereum provider: Erigon, Geth, Nethermind, Infura, Alchemy, hoac endpoint mempool co WebSocket.
- Goi Python can thiet: `pandas`, `numpy`, `scipy`, `scikit-learn`, `matplotlib`, `pyarrow`.

## 4. Du lieu

1. Tai Blockchain Address Poisoning Companion Dataset cua Tsuchiya et al. tren Figshare.
2. Lay Ethereum ERC-20 transfer logs trong khoang thoi gian tuong ung voi dataset.
3. Thu thap pending transactions bang WebSocket `eth_subscribe` voi kenh `newPendingTransactions`.
4. Neu khong co pending timestamp lich su, chay replay bang `observed_at = block_time - delay`. Ghi ro trong bai bao rang day la historical replay model, khong phai do propagation cua provider that.

## 5. Hien thuc detector

### 5.1. Xay dung trusted counterparties

Voi moi vi duoc bao ve:

1. Lay cac transfer ERC-20 hop le trong cua so `W` ngay.
2. Loai transfer da duoc dataset gan nhan poisoning.
3. Giu cac recipient va sender xuat hien trong giao dich co gia tri khac 0 va token hop le.
4. Tao `R_v`, tap counterparty dang tin cay cua vi `v`.

### 5.2. Xay dung trie

1. Chuan hoa dia chi ve 40 ky tu hex, bo `0x`, dung lowercase.
2. Prefix trie luu `k_p` ky tu dau.
3. Suffix trie luu dao nguoc `k_s` ky tu cuoi.
4. Moi node luu danh sach counterparty id va so lan xuat hien.
5. Cap nhat trie theo batch moi 1 phut hoac theo streaming neu can latency thap.

### 5.3. Xu ly pending transfer

1. Parse calldata cua ERC-20 `transfer` va `transferFrom`.
2. Parse event neu provider cung cap simulation, neu khong thi giai ma input.
3. Xac dinh victim candidate.
4. Truy van prefix trie va suffix trie.
5. Giao hai tap ket qua de lay dia chi hop le `r`.
6. Tinh risk score LR nhanh:
   - `A`: address similarity tren prefix/suffix hien thi.
   - `T`: type context, gated boi address similarity.
   - `K`: token context, gated boi address similarity.
   - Khong dung time va value trong cong thuc chinh vi chung gay nhieu context tren full-label replay.
7. Phat canh bao neu `sigmoid(beta_0 + beta_A A + beta_T A T + beta_K A K)` vuot nguong `tau`.

## 6. Baseline

| Baseline | Mo ta |
|---|---|
| Confirmed-chain detector | Phat hien sau khi giao dich vao block, dua tren rule cua cac nghien cuu truoc. |
| Linear mempool scan | Voi moi pending transfer, so sanh voi toan bo `R_v`. |
| DB index | Dung SQLite in-memory B-tree index tren cac cot prefix/suffix da materialize theo tung victim. |
| DB-LSH-style display | Native adaptation cua co che query-based dynamic bucketing trong DB-LSH ICDE 2022 tren vector prefix/suffix hien thi; khong phai artifact DB-LSH goc hay cac journal extension. |
| Address-only trie | Dung trie nhung chi tinh similarity, khong dung token, time, value. |
| Prefix-only va suffix-only | Kiem tra tung trie rieng le. |

## 7. Chi so danh gia

- Precision, recall, F1.
- False alerts per account per day.
- Latency tu luc thay pending transaction den luc phat canh bao.
- Lookup time trung binh, p95, p99.
- Throughput: so pending transfers xu ly moi giay.
- Memory tren moi vi va tren moi 1.000 counterparties.
- Recall khi mat mempool 10%, 25%, 50%.
- Live micro-benchmark: pending messages/sec, pending inter-arrival p50/p95/p99, hash-only enrichment success/fail, fetch latency, detector latency p50/p95/p99, lookup latency p50/p95/p99, alert count, same-sender/same-nonce replacement candidates, included-block pending-feed seen rate, ERC-20 seen rate, pending-to-block timestamp lead time, va provider-specific visibility-loss proxy.

Live micro-benchmark chi la phan bo sung van hanh. Khong dung no de bao cao live precision/recall vi khong co nhan poisoning ground truth. Cung khong duoc dong nhat giao dich khong thay trong pending feed voi private order flow; ly do co the la provider miss, private/builder route, thoi diem startup, hoac loi RPC.

## 8. Thong ke

- Chay moi benchmark it nhat 30 lan.
- Bao cao mean plus/minus standard deviation.
- Dung paired Wilcoxon signed-rank test cho so sanh latency theo tung ngay.
- Dung bootstrap 10.000 mau de tinh 95% confidence interval cho precision va recall.
- Neu so sanh nhieu baseline, dung Holm-Bonferroni correction.

## 9. Bang ket qua can bao cao

Bang RQ1 hien tai tren full-label replay, tong hop qua delay profile `5/15/30` giay. Baseline rows giu threshold cu; dong Mempool-TrieGuard LR dung `tau=0.901`:

| Method | Precision | Recall | F1 | Alert latency |
|---|---|---|---|---|
| Confirmed-chain detector | 0.9999996 | 0.2837245 | 0.4420332 | post-confirmation |
| Linear mempool scan | 0.9786710 | 0.9654436 | 0.9720123 | pre-confirmation replay |
| Address-only trie | 0.9998110 | 0.9572912 | 0.9780892 | pre-confirmation replay |
| Mempool-TrieGuard LR full | 0.9999233 | 0.9574553 | 0.9782286 | pre-confirmation replay |

Bang RQ2 full-replay hien tai:

| Method | Scope | Lookup mean ms | P95 ms | P99 ms | Throughput TPS | Avg candidates | F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| Linear scan | 5/15/30s | 0.143894 | 1.070443 | 2.258818 | 35,475.12 | 97.32 | 0.972012 |
| Mempool-TrieGuard LR full | 5/15/30s | 0.003891 | 0.000000 | 0.000000 | 158,954.74 | 2.80 | 0.978229 |
| DB index | 15s | 0.289321 | 1.053628 | 2.380855 | 3,456.71 | 2.80 | 0.978891 |
| DB-LSH-style display | 15s | 0.636977 | 3.711281 | 5.872672 | 3,924.65 | 25.37 | 0.971070 |

Bang RQ2 scaling per-wallet bo sung, chay 30 lan tren shard `0036`, victim `0x79672062c5a45e3808d6b784129cf3ecf59d4224`, replay mau `10,000` event:

| Method | Counterparties | Lookup mean ms | Std | Throughput TPS |
|---|---:|---:|---:|---:|
| Mempool-TrieGuard | 10 | 0.000306 | 0.000124 | 1,470,579.88 |
| Linear scan | 10 | 0.000553 | 0.000145 | 1,015,607.87 |
| DB index | 10 | 0.203443 | 0.004186 | 4,854.85 |
| DB-LSH-style display | 10 | 0.017380 | 0.001146 | 52,790.53 |
| Mempool-TrieGuard | 100 | 0.000382 | 0.000153 | 1,330,953.84 |
| Linear scan | 100 | 0.002698 | 0.000211 | 316,276.11 |
| DB index | 100 | 0.203620 | 0.010293 | 4,859.05 |
| DB-LSH-style display | 100 | 0.019871 | 0.001382 | 46,833.94 |
| Mempool-TrieGuard | 1,000 | 0.000543 | 0.000159 | 1,115,330.47 |
| Linear scan | 1,000 | 0.021775 | 0.000377 | 44,908.07 |
| DB index | 1,000 | 0.219756 | 0.012835 | 4,501.59 |
| DB-LSH-style display | 1,000 | 0.048769 | 0.001637 | 19,665.70 |
| Mempool-TrieGuard | 10,000 | 0.000668 | 0.000175 | 903,593.11 |
| Linear scan | 10,000 | 0.211963 | 0.001097 | 4,696.11 |
| DB index | 10,000 | 0.215688 | 0.009550 | 4,581.39 |
| DB-LSH-style display | 10,000 | 0.158263 | 0.011017 | 6,258.47 |

Bang overhead bo sung:

| Counterparties | Load/update mean ms | Heap per wallet KB | Heap per 1k counterparties KB |
|---:|---:|---:|---:|
| 10 | 0.021030 | 14.53 | 1,453.44 |
| 100 | 0.055673 | 32.26 | 322.64 |
| 1,000 | 0.622817 | 163.73 | 163.73 |
| 10,000 | 6.491340 | 1,608.84 | 160.88 |

Bang RQ3 hien tai: calibrated LR feature ablation, split-victim, 30 runs. Moi ablation train LR rieng, chon threshold tren validation, roi test mot lan tren held-out test set.

| Variant | Runs | F1 mean | F1 std | Precision mean | Recall mean | Delta F1 mean |
|---|---:|---:|---:|---:|---:|---:|
| Full address+type+token | 30 | 0.979535288 | 0.000000529 | 0.999825460 | 0.960052261 | 0.000000000 |
| No token | 30 | 0.979512829 | 0.000000000 | 0.999990830 | 0.959856702 | -0.000022459 |
| Address only | 30 | 0.979512024 | 0.000000000 | 0.999991229 | 0.959854788 | -0.000023264 |
| No type | 30 | 0.979510876 | 0.000000000 | 0.999988836 | 0.959854788 | -0.000024412 |
| Suffix only | 30 | 0.979510504 | 0.000000000 | 0.999858497 | 0.959974191 | -0.000024784 |
| Prefix only | 30 | 0.978908512 | 0.000000066 | 0.998613195 | 0.959966409 | -0.000626776 |

Bang RQ4 hien tai:

| Loss rate | Precision | Recall | F1 |
|---:|---:|---:|---:|
| 0.00 | 0.999923 | 0.957455 | 0.978229 |
| 0.10 | 0.999923 | 0.861707 | 0.925684 |
| 0.25 | 0.999921 | 0.718047 | 0.835860 |
| 0.50 | 0.999924 | 0.478557 | 0.647314 |

Bang live mempool micro-benchmark can bao cao sau khi chay VPS:

| Metric | Source artifact |
|---|---|
| Run manifest/config hashes | `run_manifest.json` |
| Pending messages and pending messages/sec | `live_mempool_metrics.json` |
| Pending inter-arrival p50/p95/p99 | `live_mempool_metrics.json`, `live_mempool_events.csv` |
| Fetch success/failure and fetch latency | `live_mempool_metrics.json`, `live_mempool_events.csv` |
| Detector p50/p95/p99 latency | `live_mempool_metrics.json` |
| Lookup p50/p95/p99 latency | `live_mempool_metrics.json` |
| Same-sender/same-nonce replacement candidates | `live_mempool_metrics.json`, `live_mempool_events.csv` |
| Pending-to-block timestamp lead time | `live_mempool_metrics.json`, `live_mempool_blocks.csv` |
| Included tx pending-feed seen rate | `live_mempool_metrics.json`, `live_mempool_blocks.csv` |
| ERC-20 transfer-call seen rate | `live_mempool_metrics.json`, `live_mempool_blocks.csv` |
| Visibility-loss proxy | `1 - included_seen_pending_rate`, `1 - included_erc20_seen_pending_rate` |

Chap nhan mot run live visibility chi khi `visibility_valid=true`: warmup xong, co it nhat `100` block sau warmup, va `subscription_dropped_messages=0`. Warmup loai bo toi thieu `60` giay dau hoac `5` block dau, tuy dieu kien nao dai hon. Neu `visibility_valid=false`, chi dung run do lam smoke/debug, khong dua vao bang paper.

## 10. Loi thuong gap

- Provider khong tra ve full pending transaction: can goi them `eth_getTransactionByHash`.
- Mot so token khong tuan thu ERC-20 chuan: can parser linh hoat va log truong hop loi.
- Private transaction khong xuat hien trong public mempool: danh dau la ngoai pham vi quan sat.
- Counterparty set bi nhiem doc: can bootstrap tu danh sach trusted hoac loai cac giao dich bi nhan poisoning.
- False positive voi vi rat hoat dong: tang nguong va them birthday-collision filter.

## 11. Tien do de hoan thien bai bao

| Cong viec | Trang thai |
|---|---|
| Tai va chuan hoa dataset | Xong tren may local |
| Hien thuc parser va trie | Xong |
| Hien thuc risk score va alert | Xong voi LR address+type+token; time/value da loai khoi cong thuc chinh |
| Chay full-label benchmark | Xong cho baseline cu va dong Mempool-TrieGuard LR `tau=0.901` |
| Chay tau sweep | Xong voi loss rate `0`; dung lam diagnostic cho additive score cu |
| Chay RQ2 scaling per-wallet va overhead | Xong trong `results/missing_experiments_20260523`; 2 baseline moi o `results/rq2_two_baselines_30run_20260615` |
| Viet ban thao LaTeX | Da co file local `paper/paper.tex` |
| Review va polish ban thao | Da polish nhieu vong; chi con minor revision neu can |

## 12. Ket qua hien tai

Full-label dataset:

- Total rows: `34,905,969`.
- Positives: `17,365,954`.
- Negatives: `17,516,047`.
- Shards: `256`.
- Positives la `zero_value_transfer OR tiny_transfer OR counterfeit_token_transfer`.
- Negatives la `intended_transfer` hop le, loai poisoning va payoff rows.
- Pending observations duoc replay bang `observed_at = block_time - delay`.

Exploratory tau sweep:

| Method | Best tau | Best F1 | F1 at tau=0.40 | Delta F1 |
|---|---:|---:|---:|---:|
| Address-only trie | 0.505 | 0.978172 | 0.978089 | +0.000083 |
| Mempool-TrieGuard | 0.395 | 0.977826 | 0.977797 | +0.000029 |
| No time | 0.430 | 0.978073 | 0.978062 | +0.000011 |
| No token | 0.335 | 0.976106 | 0.973297 | +0.002809 |
| No value | 0.430 | 0.977976 | 0.977869 | +0.000107 |
| Prefix only | 0.390 | 0.977967 | 0.977916 | +0.000050 |
| Suffix only | 0.390 | 0.978000 | 0.977945 | +0.000054 |

Ket luan can viet dung trong paper: trie retrieval van la loi the chinh ve toc do; cong thuc LR moi giu latency nhe va dat F1 `0.978229` tren replay MTG-only. Trong RQ3, full address+type+token la bien the tot nhat theo 30-run LR ablation, nhung margin so voi address-only/no-type/no-token rat nho, nen can viet thanh bang chung calibration chu khong phong dai thanh cach biet lon.



