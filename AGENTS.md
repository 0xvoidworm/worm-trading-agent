# Graph-first token guard

- Untuk pertanyaan arsitektur, dependency, call-flow, atau “di mana X diatur”, mulai dengan `graphify query "<pertanyaan>" --budget 1200`.
- Gunakan `graphify path "<A>" "<B>"` untuk hubungan spesifik. Baca source hanya setelah graph menunjuk file/simbol relevan atau hasil graph terpotong.
- Untuk bug yang sudah terlokalisasi ke 1–3 file, langsung baca file; jangan paksa Graphify.
- Setelah perubahan code selesai, jalankan `graphify update .` sekali jika struktur/call graph berubah.
