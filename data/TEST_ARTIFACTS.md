# Test Artifacts in trade_journal

Trade rows yang sengaja di-seed selama Sprint untuk verifikasi live.
Di-EXCLUDE dari hitungan equity (`portfolio.get_equity`) supaya tidak
mencemari portfolio accounting dengan loss/profit yang bukan dari sinyal
asli.

Konvensi: thesis di-prefix `[TEST_ARTIFACT_<KIND>]`.
SQL filter contoh:
```sql
SELECT * FROM trade_journal
WHERE thesis IS NULL OR thesis NOT LIKE '[TEST_ARTIFACT%';
```

## Catatan

| ID | Tanggal      | Pair      | Direction | PnL       | Apa yang divalidasi |
|----|--------------|-----------|-----------|-----------|---------------------|
| 5  | 2026-04-25   | ETH/USDT  | LONG      | -231.99   | Step 2 auto-exit live test: SL trigger fire dari daemon cycle. Entry sengaja di SL territory (+10% di atas market) supaya pertama kali daemon cycle, advisor emit EXIT verdict, executor close. Confirmed: cycle #1 pasca-restart, advisor `Bearish EXIT trade #5: SL hit -9.09%`, executor `CLOSE @ 2319.93`. |
| 6  | 2026-04-25   | SOL/USDT  | LONG      | -10.82    | Step 3 sizing live test: REGIME_CHANGE dummy signal `t=15.50` di-inject (`[TEST_ARTIFACT_STEP3]` payload). Daemon `--once` cycle: advisor sized 22.075 SOL × $86.6 = $1911.72 notional (~20% equity 9558.6), risk $95.59 ≈ 1%. Cap A satisfied (20% < 25%), Cap B (20% < 100%), Cap C (1/3). Executor opened trade #6 cleanly. Membuktikan: size_hint bukan lagi 1.0, advisor → executor pipeline integral, exposure recheck gate #8 lolos. **Closed 2026-04-26 00:17 UTC pre-Step 5 cleanup** @ SOL last close 86.11 (notes: `[TEST_ARTIFACT cleanup pre-step5]`), PnL -10.82. |
| 7  | 2026-04-25   | ETH/USDT  | LONG      | -143.38   | Step 4 daily-loss killswitch live test (`[TEST_ARTIFACT_STEP4_WARN_SEED]`). Seed close pada 20:17 UTC untuk push daily_pnl_pct di luar -warn (1%) → trigger `RISK_KILLSWITCH` signal sekaligus tulis `data/risk.killswitch`. Catatan: real today PnL sebenarnya sudah -5.77% sejak trade #4 close pagi (SOL/USDT -585.80) jadi engage akan terjadi tanpa seed pun — trade ini purpose-built untuk reproduce flow advisor→executor block ENTRY. Verified scenarios: (A) WARN/TRIGGER engage idempotent, (B) ENTRY blocked di gate #0.5 dengan reason `risk_killswitch_active: daily_loss daily=-7.19%`, EXIT bypass gate #0.5 dan terhenti di gate #3 (health=GAP) — proves EXIT bisa close losing positions, (C) `python scripts/risk.py reset --yes` hapus file + emit `RISK_RESET` + append risk.log. |

## Aturan tambah artifact baru

1. Saat seed manual → langsung add `[TEST_ARTIFACT_<KIND>]` di thesis prefix.
   Kind contoh: `SL`, `TP`, `REGIME`, `TIME`, `SIZING`, `STEP4_WARN_SEED`.
2. Update tabel di file ini sebelum daemon restart (audit trail).
3. Setelah validasi selesai, biarkan trade record tetap (untuk reproducibility),
   jangan dihapus.
