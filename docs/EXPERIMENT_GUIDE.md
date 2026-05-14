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

Sau khi chay xong, thay cac gia tri `TBD` trong bao cao hoac file thuyet minh bang ket qua that:

| Method | Precision | Recall | F1 | Alert latency |
|---|---|---|---|---|
| Confirmed-chain detector | ket qua | ket qua | ket qua | post-confirmation |
| Linear mempool scan | ket qua | ket qua | ket qua | ket qua |
| Address-only trie | ket qua | ket qua | ket qua | ket qua |
| Mempool-TrieGuard | ket qua | ket qua | ket qua | ket qua |

## 10. Loi thuong gap

- Provider khong tra ve full pending transaction: can goi them `eth_getTransactionByHash`.
- Mot so token khong tuan thu ERC-20 chuan: can parser linh hoat va log truong hop loi.
- Private transaction khong xuat hien trong public mempool: danh dau la ngoai pham vi quan sat.
- Counterparty set bi nhiem doc: can bootstrap tu danh sach trusted hoac loai cac giao dich bi nhan poisoning.
- False positive voi vi rat hoat dong: tang nguong va them birthday-collision filter.

## 11. Tien do de hoan thien bai bao

| Cong viec | Thoi luong du kien |
|---|---|
| Tai va chuan hoa dataset | 2 ngay |
| Hien thuc parser va trie | 3 ngay |
| Hien thuc risk score va alert | 2 ngay |
| Chay benchmark latency | 1 ngay |
| Replay dataset va tinh metric | 3 ngay |
| Viet bang, hinh, va phan ket qua | 2 ngay |
| Review va polish ban thao | 2 ngay |

Tong thoi gian du kien: 15 ngay lam viec.



