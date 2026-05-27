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
| RQ2 | Trie co giam latency so voi quet tuyen tinh khong? | Benchmark lookup voi 10, 100, 1.000, 10.000 counterparty tren moi vi. |
| RQ3 | Thanh phan nao cua risk score dong gop nhieu nhat? | Ablation: bo token context, bo time decay, bo value score, chi dung address similarity. |
| RQ4 | He thong ben vung the nao khi mempool khong day du? | Mo phong ty le mat giao dich pending 10%, 25%, 50% va do recall. |

Quy tac so sanh hien tai: cac bang RQ dung cung production threshold `tau=0.40`. Tau sweep chi la phan phan tich calibration rieng, khong dung de tune threshold theo tung method trong bang RQ.

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
4. Neu khong co pending timestamp lich su, chay replay: dung block timestamp lam moc xac nhan va gia lap pending time theo log cua provider hien tai. Ghi ro han che nay trong bai bao.

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
6. Tinh risk score:
   - Address similarity.
   - Loai transfer: zero-value, tiny-value, counterfeit token.
   - Token context: contract hop le hay token la gia.
   - Time decay tu lan tuong tac hop le gan nhat.
   - Value score.
7. Phat canh bao neu score vuot nguong `tau`.

## 6. Baseline

| Baseline | Mo ta |
|---|---|
| Confirmed-chain detector | Phat hien sau khi giao dich vao block, dua tren rule cua cac nghien cuu truoc. |
| Linear mempool scan | Voi moi pending transfer, so sanh voi toan bo `R_v`. |
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

## 8. Thong ke

- Chay moi benchmark it nhat 30 lan.
- Bao cao mean plus/minus standard deviation.
- Dung paired Wilcoxon signed-rank test cho so sanh latency theo tung ngay.
- Dung bootstrap 10.000 mau de tinh 95% confidence interval cho precision va recall.
- Neu so sanh nhieu baseline, dung Holm-Bonferroni correction.

## 9. Bang ket qua can bao cao

Bang RQ1 hien tai tren full-label replay voi `tau=0.40`, tong hop qua delay profile `5/15/30` giay:

| Method | Precision | Recall | F1 | Alert latency |
|---|---|---|---|---|
| Confirmed-chain detector | 0.9999996 | 0.2837245 | 0.4420332 | post-confirmation |
| Linear mempool scan | 0.9786710 | 0.9654436 | 0.9720123 | pre-confirmation replay |
| Address-only trie | 0.9998110 | 0.9572912 | 0.9780892 | pre-confirmation replay |
| Mempool-TrieGuard | 0.9992142 | 0.9572795 | 0.9777975 | pre-confirmation replay |

Bang RQ2 hien tai:

| Method | Lookup mean ms | P95 ms | P99 ms | Throughput TPS | Avg candidates |
|---|---:|---:|---:|---:|---:|
| Linear scan | 0.095244 | 0.543809 | 1.431798 | 25,738.66 | 97.32 |
| Mempool-TrieGuard | 0.004659 | 0.000000 | 0.014142 | 146,635.27 | 2.80 |

Bang RQ2 scaling per-wallet bo sung, chay 30 lan tren shard `0036`, victim `0x79672062c5a45e3808d6b784129cf3ecf59d4224`, replay mau `10,000` event:

| Method | Counterparties | Lookup mean ms | Std | Throughput TPS |
|---|---:|---:|---:|---:|
| Mempool-TrieGuard | 10 | 0.000306 | 0.000124 | 1,470,579.88 |
| Linear scan | 10 | 0.000553 | 0.000145 | 1,015,607.87 |
| Mempool-TrieGuard | 100 | 0.000382 | 0.000153 | 1,330,953.84 |
| Linear scan | 100 | 0.002698 | 0.000211 | 316,276.11 |
| Mempool-TrieGuard | 1,000 | 0.000543 | 0.000159 | 1,115,330.47 |
| Linear scan | 1,000 | 0.021775 | 0.000377 | 44,908.07 |
| Mempool-TrieGuard | 10,000 | 0.000668 | 0.000175 | 903,593.11 |
| Linear scan | 10,000 | 0.211963 | 0.001097 | 4,696.11 |

Bang overhead bo sung:

| Counterparties | Load/update mean ms | Heap per wallet KB | Heap per 1k counterparties KB |
|---:|---:|---:|---:|
| 10 | 0.021030 | 14.53 | 1,453.44 |
| 100 | 0.055673 | 32.26 | 322.64 |
| 1,000 | 0.622817 | 163.73 | 163.73 |
| 10,000 | 6.491340 | 1,608.84 | 160.88 |

Bang RQ3 hien tai:

| Method | Precision | Recall | F1 |
|---|---:|---:|---:|
| Address-only trie | 0.999811 | 0.957291 | 0.978089 |
| Mempool-TrieGuard | 0.999214 | 0.957280 | 0.977797 |
| No time | 0.999547 | 0.957480 | 0.978062 |
| No token | 0.998967 | 0.948913 | 0.973297 |
| No value | 0.999129 | 0.957496 | 0.977869 |
| Prefix only | 0.999564 | 0.957186 | 0.977916 |
| Suffix only | 0.999621 | 0.957189 | 0.977945 |

Bang RQ4 hien tai:

| Loss rate | Precision | Recall | F1 |
|---:|---:|---:|---:|
| 0.00 | 0.999214 | 0.957280 | 0.977797 |
| 0.10 | 0.999216 | 0.861548 | 0.925289 |
| 0.25 | 0.999218 | 0.717912 | 0.835523 |
| 0.50 | 0.999208 | 0.478467 | 0.647081 |

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
| Hien thuc risk score va alert | Xong, can calibration tiep |
| Chay full-label benchmark | Xong voi `tau=0.40` |
| Chay tau sweep | Xong voi loss rate `0` |
| Chay RQ2 scaling per-wallet va overhead | Xong trong `results/missing_experiments_20260523` |
| Viet ban thao LaTeX | Da co file local `paper/mempool_trieguard_full_dataset_paper_20260523.tex` |
| Review va polish ban thao | Con lai |

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

Ket luan can viet dung trong paper: trie retrieval tot va nhanh, `tau=0.40` gan toi uu cho production method, nhung risk score hien tai chua thang moi ablation. Vi vay can trinh bay day la finding ve calibration, khong nen tune threshold rieng cho tung method de lam dep bang RQ.



