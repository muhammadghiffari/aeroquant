# START-HERE — urutan literal dari nol (baca ini duluan, sebelum file lain manapun)

Ini bukan dokumen kelima yang nambahin kebingungan — ini **peta** ke 4 file lain (`RUNBOOK-2_6.md`,
`ONBOARDING-GHIFFARI.md`, `TEAM-ONBOARDING.md`, `ONBOARDING-RADITH.md`). Kamu gak perlu baca
semuanya top-to-bottom sekarang — ikutin fase di bawah, tiap fase nunjuk ke section spesifik yang
perlu dibaca **pas itu juga**, bukan sebelumnya.

**Kenapa bingung kemarin:** ada bug urutan beneran di `RUNBOOK-2_6.md` — §2 nyuruh `git clone --bare`
padahal repo-nya baru dibuat di §4 (dua section setelahnya). Udah dibenerin (isinya dipindah ke §4c),
tapi biar gak ketemu jebakan serupa lagi, ikutin urutan FASE di sini, bukan loncat baca nomor section.

---

## FASE 0 — cuma kamu (Ghiffari), di LAPTOP kamu sendiri, sebelum sentuh VPS sama sekali

1. Pastiin kode project (`CLAUDE.md`, `README.md`, `model_gateway.py`, dst) ada di folder lokal laptop kamu.
2. **`RUNBOOK-2_6.md` §4 (bagian atas aja, sebelum §4b)** — bikin repo GitHub (`gh repo create` atau manual di github.com), push kode lokal kamu ke sana.
3. Di bagian yang sama (§4), invite Raka, Amil, dan Radith sebagai collaborator.

**Selesai di sini kalau:** `gh repo view` nunjukin repo private, dan Raka+Amil ke-list sebagai collaborator.

---

## FASE 1 — cuma kamu (Ghiffari), SSH ke VPS pakai **root password dari panel Nevacloud**

Ini SSH pertama — pakai kredensial dari dashboard provider VPS kamu, bukan kredensial yang lain.

```bash
ssh root@202.134.242.101
```

4. **`RUNBOOK-2_6.md` §1** — install paket dasar, chrony, ufw, fail2ban. (Ini yang udah kamu jalanin & aku cek dari screenshot — beres.)
5. **`RUNBOOK-2_6.md` §2** — bikin user Linux `ghiffari`/`raka`/`amil`, kasih sudo cuma ke `ghiffari`, `enable-linger` buat raka & amil, set password masing-masing (JANGAN kirim di grup — DM personal). **Berhenti di "Check" §2, jangan lanjut ke bagian workspace/git dulu.**
6. **`RUNBOOK-2_6.md` §3** — tambah swap 2GB.
7. **`RUNBOOK-2_6.md` §4b** — sekarang kamu logout dari `root`, SSH lagi tapi **sebagai `ghiffari`** (`ssh ghiffari@202.134.242.101`, pakai password yang baru kamu set sendiri di langkah 5). Generate SSH key **di VPS ini** (beda dari SSH key buat login VPS!), daftarin ke akun GitHub kamu. Ini kunci yang dipakai buat `git clone`/`push`, bukan buat login SSH.
8. **`RUNBOOK-2_6.md` §4c** — masih sebagai `ghiffari`, `git clone --bare` sekali (bikin shared object store), lalu `git worktree add` buat bikin folder kerja kamu sendiri `~/aeroquant-ghiffari/`.
9. **`RUNBOOK-2_6.md` §5 dan seterusnya, DAN `ONBOARDING-GHIFFARI.md`** — venv, `.env`, smoke test, dst. `ONBOARDING-GHIFFARI.md` isinya highlight yang beda dari asumsi RUNBOOK (model_gateway.py yang udah dibenerin, spec VPS aktual, dst) — baca paralel sambil ngikutin RUNBOOK.
10. **`ONBOARDING-GHIFFARI.md` §4 (yang baru ditambahin)** — jalanin `loginctl enable-linger` + `deluser sudo` buat Raka & Amil (ini juga cuma bisa kamu, admin, yang jalanin).

**Selesai fase ini kalau:** worker kamu sendiri jalan smoke-test bersih, `.venv` aktif, `pytest` hijau.

---

## FASE 2 — Raka & Amil, masing-masing SSH ke VPS sebagai diri sendiri

Mereka **TIDAK** login sebagai root, **TIDAK** perlu §1/§2/§3 (itu udah kamu kerjain sebagai admin).
Mereka mulai dari kredensial yang kamu kasih personal (username + password dari §2 di atas):

```bash
ssh raka@202.134.242.101      # atau amil
```

Lalu masing-masing ikutin **`TEAM-ONBOARDING.md` dari atas ke bawah** — file itu udah isinya lengkap:
accept invite GitHub → §4b-equivalent (generate SSH key sendiri di VPS, daftarin ke GitHub sendiri,
ini ada implisit di alur clone-nya) → `git worktree add -b strategy/<name>` (nyambung ke
`~/aeroquant.git` yang kamu bikin di FASE 1 langkah 8) → setup bot sendiri → systemd user-unit →
aturan wajib bagian 6.

**Selesai fase ini kalau:** masing-masing `git status` di folder mereka nunjukin branch sendiri, dan
`systemctl --user status aeroquant-<name>` nunjukin enabled.

---

## FASE 3 — Radith sebagai akun ke-4, di LAPTOP dia sendiri, gak nyentuh VPS Linux sama sekali

Karena Radith sudah dikonfirmasi sebagai akun ke-4 dan sudah diinvite ke GitHub, dia ikutin
**`ONBOARDING-RADITH.md`** dari atas, terutama §2 (accept invite → SSH key di laptop dia sendiri,
bukan di VPS → `git clone` biasa, checkout `strategy/radith`).

---

## Cara kasih tau Raka/Amil di Discord (copy-paste, edit bagian [ ])

> **Setup AeroQuant — mulai dari sini**
> Aku (Ghiffari) udah beresin VPS, bikin akun kalian (`raka`/`amil`), dan invite kalian ke repo GitHub
> `aeroquant` — cek email/notifikasi GitHub, accept invite-nya dulu.
>
> Password VPS kalian: **aku DM personal, bukan di sini.**
>
> Yang perlu kalian lakuin, urutannya:
> 1. Accept invite GitHub (link di email/notif GitHub kalian)
> 2. Baca & ikutin **`TEAM-ONBOARDING.md`** dari section 0 (checklist) sampai selesai — semua command
>    di situ, gak perlu nebak-nebak
> 3. SSH ke VPS: `ssh [raka/amil]@202.134.242.101` pakai password yang aku DM
> 4. Ikutin section 2 (GitHub auth + clone) → section 3 (credential Featherless & Alpaca) → section 4
>    (MCP server) → section 5 (systemd) di `TEAM-ONBOARDING.md`
> 5. Kalau ada yang error atau bingung di step manapun, langsung tanya di sini — jangan nebak lanjutin
>
> Deadline penting yang wajib dibaca sebelum mulai coding: bagian 6 & 7 di file yang sama (aturan
> wajib + jadwal). Itu bukan opsional, itu syarat eligibility.

---

## Ringkasan super-singkat siapa baca file apa

| Siapa | Baca file mana | Kapan |
|---|---|---|
| Ghiffari | file ini (FASE 0-1) → `RUNBOOK-2_6.md` → `ONBOARDING-GHIFFARI.md` (paralel) | sekarang, sebelum Senin |
| Raka, Amil | `TEAM-ONBOARDING.md` (dari section 0) | setelah Ghiffari selesai FASE 1 |
| Radith (akun ke-4) | `ONBOARDING-RADITH.md` (dari section 0) | sekarang, setelah invite GitHub |
| Semua orang | `CLAUDE.md` (non-negotiable rules) — sekali baca, biar paham batasan arsitektur | sebelum nulis kode strategi |
