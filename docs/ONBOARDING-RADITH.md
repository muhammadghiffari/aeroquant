# Onboarding — Radith (laptop-primary, VPS Windows sebagai backup akses)

## 0. Baca ini dulu — Radith sudah dikonfirmasi sebagai akun ke-4

`RUNBOOK-2_6.md` baris pertama nulis: *"Team: Ghiffari, Raka, Amil — one Linux user, one Alpaca account, one systemd unit, one git worktree each."* PRD §11b juga eksplisit: **tiga** akun kompetisi paralel, satu per anggota, dan aturan wajib "flat sebelum Kamis 4:15 PM ET" ditulis berlaku ke *"all three accounts, not just whichever one ends up submitted."*

Radith nggak ada di roster itu sama sekali. Kalau rencananya Radith beneran jalanin strategi & akun trading sendiri (bukan cuma role support/observer), itu artinya:

- Ini jadi akun ke-**4**, bukan penyesuaian kecil — tiap tempat yang nulis "tiga"/"×3"/"all three accounts" (checklist Kamis §17, perbandingan equity Jumat pagi, roster `adduser` §2, one-page write-up soal infrastruktur di §10) perlu diupdate eksplisit jadi empat.
- Ini bukan sesuatu yang aku putuskan sendiri di sini — ini keputusan tim, idealnya dicatat semacam bump versi PRD (2.7), sama seperti tiap keputusan lain di dokumen ini yang selalu ditulis "kenapa" dan "kapan."

**Keputusan tim:** Radith beneran trading sebagai akun ke-4, bukan sekadar backup/monitoring pasif. Checklist akun, credential, branch, alert, dan runtime di bawah berlaku penuh.

Kalau memang jadi akun ke-4: dia tetap butuh Alpaca scratch account + akun resmi $100k sendiri (RUNBOOK §0/§16), collaborator akses di GitHub (§4), dan `.env` sendiri dengan `ANTHROPIC_API_KEY`/`FEATHERLESS_API_KEY` miliknya sendiri (RUNBOOK §0 eksplisit: *"each person gets their own key — don't share one"*).

## 1. Kenapa dia nggak masuk VPS Nevacloud yang sama — dan itu keputusan yang tepat

VPS `alpaca-server` itu 2 vCPU / ~1.9GB RAM. RUNBOOK §3 sendiri sudah bilang 3 worker konkuren aja "*can plausibly approach or exceed 2GB — a real OOM-kill risk, not theoretical*." Nambahin worker ke-4 di box yang sama bakal makin mepet — apalagi kalau lagi jam trading beneran, karena swap itu nyelametin dari crash tapi bikin proses jadi lambat/laggy pas lagi butuh eksekusi cepat, bukan solusi gratis. Jadi rencana "Radith jalan dari laptopnya sendiri" itu keputusan infra yang masuk akal, bukan cuma keterpaksaan.

Yang perlu diwaspadai justru di sisi lain: **laptop yang "dinyalain terus" bukan pengganti systemd yang sepadan.** RUNBOOK §13 kasih tiap worker `Restart=always`, `MemoryMax`, `CPUQuota` — kalau laptop Radith sleep, update Windows restart otomatis, wifi putus, atau dia lupa nutup laptop pas keluar rumah, posisi yang harusnya di-force-close sebelum expiry (aturan non-negotiable #4 di `CLAUDE.md`, dan deadline Kamis 4:15 PM ET yang hard) bisa nggak ke-trigger. Ini persis jenis risiko yang bikin RDP backup itu penting — tapi backup akses doang nggak cukup kalau nggak ada yang jamin proses tradingnya sendiri auto-restart. Untuk Windows nggak ada systemd; setara paling deket:

- **Task Scheduler** dengan trigger "on startup" + "restart on failure" (bisa di-set di tab Settings task-nya), atau
- **NSSM** (Non-Sucking Service Manager) buat jalanin script Python-nya sebagai Windows service beneran, auto-restart kalau crash — ini paling mirip perilaku `Restart=always` systemd, atau
- **WSL2** di dalam laptop/VM Windows-nya, biar bisa pakai path yang sama persis dengan runbook (venv, systemd-in-WSL kalau versi WSL-nya support, atau minimal cron+watchdog script).

Pilih salah satu **sebelum** Senin, jangan andalkan "kalau crash nanti aku restart manual" — itu cukup untuk dev, nggak cukup untuk komitmen "zero open position by Thursday 4:15 PM ET."

## 2. RDP backup — cara pakai yang lebih aman

Kredensial yang kamu kirim di chat ini (IP, port, username) sekarang sudah "tersebar" — anggap begitu, dan itu bukan masalah besar selama nggak ada password ikut kekirim (dan memang nggak ada, bagus). Tapi tetap, langkah pertama:

1. **Ganti password RDP-nya sekarang**, sebelum dipakai beneran minggu ini. Password baru: panjang, unik, bukan yang dipakai ulang di tempat lain.
2. **Jangan simpan kredensial RDP di file yang ikut ke-commit ke repo** (termasuk guide ini) — taruh di password manager tim, bukan di markdown yang numpang lewat banyak orang.

Port yang dipakai sudah bukan default 3389 — itu langkah bagus, tapi port custom doang **bukan** proteksi, cuma ngurangin noise scanner paling malas. Yang benar-benar efektif, urut dari paling penting:

- **Paling direkomendasikan: jangan expose RDP ke internet terbuka sama sekali.** Pasang Tailscale (atau WireGuard) di VM Windows-nya dan di laptop Radith — `tailscale up` di kedua sisi, ~5 menit, gratis untuk skala kecil begini. Habis itu RDP-nya cuma nyambung lewat IP privat Tailscale, port publik ditutup total di firewall. Ini ngilangin hampir seluruh kelas risiko (brute force, credential stuffing, RDP-CVE scanning) tanpa harus ribet hardening manual di tengah hackathon.
- Kalau nggak sempat setup itu: **batasi Windows Firewall inbound RDP cuma dari IP-IP yang dikenal** (IP rumah/kantor Ghiffari & Radith) — bukan `Any`. IP dinamis di ISP Indonesia bikin ini kurang stabil, itu sebabnya opsi Tailscale di atas lebih disarankan.
- **Jangan pakai akun `Administrator` built-in buat login RDP.** Itu username nomor satu yang ditarget bot brute-force RDP di seluruh dunia — mereka nggak perlu nebak usernamenya lagi. Rename built-in Administrator (Local Security Policy → Local Policies → Security Options → "Accounts: Rename administrator account"), lalu buat akun admin baru dengan nama nggak umum khusus buat RDP.
- **Pastikan Network Level Authentication (NLA) aktif** — System Properties → Remote → centang "Allow connections only from computers running RDP with NLA."
- **Set account lockout policy** (misal: lock 15–30 menit setelah 5x gagal login) — Local Security Policy → Account Policies → Account Lockout Policy. Ini bikin brute force jadi nggak praktis meski password-nya kebobol lemah.
- **Cek Security Event Log sekarang, sebelum diandalkan minggu ini** — filter Event ID `4625` (failed logon). Kalau RDP-nya "udah aktif" dari sebelumnya (sesuai yang kamu bilang), ada kemungkinan sudah kena scan/brute-force attempt; kalau jumlahnya tinggi, itu sinyal buat rotate password + pasang salah satu mitigasi di atas segera, bukan nanti-nanti.
- **Windows Update dulu sebelum dipakai serius.** Ini eval image (dari screenshot System Information-nya) — pastikan patch RDP-related terbaru sudah masuk, terutama karena license eval kadang start dari image yang sempat nganggur nggak ke-update.
- Opsional tapi bagus kalau ada waktu: pasang **RDPGuard** atau **IPBan** (setara `fail2ban` di Linux) — auto-block IP setelah beberapa kali gagal login berturut-turut.

## 2. Repo access & alerting — dua hal yang belum eksplisit di guide manapun

**GitHub:** kamu butuh diinvite sebagai collaborator ke repo `aeroquant` (Ghiffari yang jalanin,
`RUNBOOK-2_6.md` §4) — bukan sesuatu yang otomatis. Setelah invite diterima:

```bash
# 👤 di laptop kamu sendiri, sekali
ssh-keygen -t ed25519 -C "email kamu"
# GitHub → Settings → SSH and GPG keys → New SSH key → paste isi id_ed25519.pub

git clone git@github.com:<org-atau-username>/aeroquant.git
cd aeroquant
git checkout -b strategy/radith
```

Beda dari Ghiffari/Raka/Amil: kamu **gak pakai `git worktree`** (itu buat berbagi satu `.git` object
store di VPS yang sama) — clone biasa di laptop sendiri sudah cukup, karena kamu bukan di mesin yang
sama dengan mereka bertiga.

**Telegram alert:** RUNBOOK §14 nyebut alerting bisa "shared across all three, or one bot per person."
Karena strategi kamu independen dari mereka bertiga, **bikin bot Telegram sendiri** (via `@BotFather`,
langkahnya sama seperti di `TEAM-ONBOARDING.md` §3) — jangan numpang ke bot punya Ghiffari, biar alert
crash/force-close kamu gak nyampur sama punya mereka.

## 3. Soal kondisi VM-nya sendiri (dari 2 screenshot yang kamu kirim)

- Server Manager nunjukkin ada Critical Event ID 41 (`Kernel-Power`) — itu biasanya nandain VM sempat mati nggak bersih (power loss / hard reset / host pause). Kemungkinan besar cuma insiden lama yang nggak berulang, tapi worth di-cross-check: kalau event ini muncul lagi selama minggu trading, itu bisa jadi penyebab worker mati mendadak tanpa force-close sempat jalan — bukan cuma soal RDP-nya lagi, tapi soal VM-nya sendiri stabil apa nggak.
- "Windows License valid for 179 days" — jauh di atas window kompetisi (berakhir Kamis 3 Sep), jadi nggak perlu dipikirin minggu ini, cuma dicatat aja biar nggak lupa reactivate someday.
- Spec VM-nya (2 vCPU, 4096MB RAM, Hyper-V) itu terpisah dari VPS Nevacloud Linux (`alpaca-server`) — pastikan semua orang di tim paham ini dua mesin beda, biar nggak ada asumsi keliru soal "VPS kita" pas ngomongin kapasitas.

## 4. Ringkasan aksi, urut prioritas

1. Konfirmasi ke tim: Radith akun trading beneran (ke-4) atau support role — lalu update PRD/RUNBOOK sesuai (lihat §0).
2. Terima invite GitHub collaborator dari Ghiffari, setup SSH key, clone repo, checkout `strategy/radith` (§2).
3. Bikin bot Telegram sendiri buat alert (§2).
4. Setup auto-restart buat proses trading-nya di laptop (NSSM/Task Scheduler/WSL2) — jangan andalkan restart manual.
5. Rotate password RDP sekarang.
6. Pasang Tailscale/WireGuard, tutup port RDP publik di firewall — atau minimal batasi ke IP dikenal.
7. Rename akun `Administrator`, aktifkan NLA + account lockout.
8. Cek Event ID 4625 buat lihat riwayat percobaan login gagal.
9. Windows Update, baru mulai dev serius di atasnya.
