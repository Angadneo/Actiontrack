# Test fixtures

`notes/` holds the full 36-file sample set (35 `.md` + 1 `.txt`): 34 files copied byte-exact from
`samples/notes/` plus `scratch.md` (binary) and `untitled.md` (valid note, 2 items) copied byte-exact from
`notes/notes/` (spec Appendix A.1a).

**Rule: fixtures are read-only; tests copy them to `tmp_path` before use** (see the `copy_fixture_notes`
fixture in `tests/conftest.py`). The conftest guard fails any test that changes this folder, `samples/notes/` or `notes/notes/`.

| File | Origin | SHA-256 |
|---|---|---|
| `2026-09-01-quote-portal-kickoff.md` | `samples/notes/` | `766c220da715fa97d9f9757899148bb006eb90cb74782e5ae63a2fa6f7207cfa` |
| `2026-09-02-standup.md` | `samples/notes/` | `59c81667c8d6203900e8ee9450bdb4c1438c969a7a8603f05a6c2c12a9c5e6b2` |
| `2026-09-03-billing-migration-sync.md` | `samples/notes/` | `f6e1a935f8127032ceadd57caf950a8390277f372d45c77cf9e17776f1bee771` |
| `2026-09-08-quote-portal-weekly.md` | `samples/notes/` | `3d0d5f82ebb41a880bd0ead57eaed60f482416cbcecefca5f73776c2ea4da93f` |
| `2026-09-09-notes-final-v2.md` | `samples/notes/` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `2026-09-09-qa-standup.md` | `samples/notes/` | `5e9876db0d7c057611fe915f061162f563b1b65a8ca62040fa6ba1d3c8251cbb` |
| `2026-09-10-acme-renewal-prep.md` | `samples/notes/` | `985a014fd1da6d22201c926d8d11e1dd5d7189b7e387094b2681ec410e70d25a` |
| `2026-09-14-onboarding-flow-design.md` | `samples/notes/` | `f90f026456ed3f9fa1860e6df6f18c2e09b18efc7f44d4d1a121cb84aa59fa5f` |
| `2026-09-15-quote-portal-weekly.md` | `samples/notes/` | `e35881cb435bf439ce25d3aa48e0318a9e19a27ae6d614d664152efcff177174` |
| `2026-09-16-standup.md` | `samples/notes/` | `54eb1509438160277a9d1d1902552ca60747578ec284658aa7aca8eb26d1de4b` |
| `2026-09-17-billing-migration-sync.md` | `samples/notes/` | `27858323cabd0a60c0ec662f84dacdc445666bb2c75fe42e38429f7e83252212` |
| `2026-09-18-design-review.md` | `samples/notes/` | `3326610997cb38e59c9052402712d73160e4230a41cef38efeef2360a8d6a354` |
| `2026-09-19-offsite-draft.txt` | `samples/notes/` | `04d648a78e8d5badf38b7964329c80a900db3e00938dea45ab9bd90138d1c96a` |
| `2026-09-21-incident-review.md` | `samples/notes/` | `db08a5b8542aa71978b3f80cdfae0bfdcf89243b08aaa2106d5070c73b92ad78` |
| `2026-09-22-quote-portal-weekly.md` | `samples/notes/` | `71132b175e88d7980ed3ef4936d3a2260d9f9bfda732691711d161739f8ffe49` |
| `2026-09-23-standup.md` | `samples/notes/` | `0e320a02362b82313c4744e366e550ad926cc4b96bb4b2d5ce8008d5a12abbfb` |
| `2026-09-24-onboarding-flow-design.md` | `samples/notes/` | `4cef6147f21e16b691470840ff0ec597ce174431da706a6d39574b56f7de06ea` |
| `2026-09-25-qa-sync.md` | `samples/notes/` | `f751a88394fc21b146b220ed637ce5e963e084a01cb17b2e29390db00e6da7ef` |
| `2026-09-28-acme-renewal-checkin.md` | `samples/notes/` | `6931eef8c5ec9e7b8d1340ad94761c0152f26c2c67ce0cd2eb87e95df074c0b2` |
| `2026-09-29-billing-cutover-planning.md` | `samples/notes/` | `72422cde6563f87a5aca136ffcf3d3a3471688bf0c80ca9695dfa10a0ccae6bc` |
| `2026-09-30-q4-offsite.md` | `samples/notes/` | `b792d327ca5acfbe2a0ba3c947571dfe07cddc5107cb949365d9161bc017681e` |
| `2026-09-30-standup.md` | `samples/notes/` | `0d638688f09e61c3dee82fbd015d06a360e851ac8506b699ef59d48a408b7573` |
| `2026-10-01-quote-portal-weekly.md` | `samples/notes/` | `be514aa565bc991ae5eb5d17d8387a9aed4c1d6b6dff4d84d6006551a0a88e49` |
| `2026-10-02-finance-sync.md` | `samples/notes/` | `cb51a32f5064fec4e51259142376b1de76b84adfdf0d50c96586b89b76e7afe7` |
| `2026-10-03-sales-pipeline.md` | `samples/notes/` | `ab30049a3ebd2b494bdedaec7650c8527342f0407cce7b316de54e3462f5051d` |
| `2026-10-05-onboarding-retro.md` | `samples/notes/` | `9751189432fbef4021ba8cc40a899c8e7062600e9ab7991116ce917b5ee1654e` |
| `2026-10-06-quote-portal-weekly.md` | `samples/notes/` | `6fa68daa2410a3bd8cd8c18da0f5cd2f21680c0f9aa3a485685acc44002bc22d` |
| `2026-10-07-standup.md` | `samples/notes/` | `46450ec5499cb17673ec4100a1bf8ff8f55663c11b5b0226213f82485b4d81fa` |
| `2026-10-08-billing-go-no-go.md` | `samples/notes/` | `a0f03d02bc7dadebdfcf5181391a3ef2a5b210fe34911ad51be1b246811f8dfc` |
| `2026-10-09-security-review.md` | `samples/notes/` | `b299e2242471a625d5326abfa052f2fef00dd21ba097c48fcf292c52e46fdf00` |
| `2026-10-10-sales-weekly.md` | `samples/notes/` | `503f10549421d440d84f59d6c826d37200ddafd237e7116ac8344bbd62b39cd6` |
| `2026-10-12-billing-post-cutover.md` | `samples/notes/` | `17fb55f802924e1e288c2bb3636c6b62ca37f4ccdce81e4e3a2779a2d03e5b48` |
| `2026-10-13-quote-portal-weekly.md` | `samples/notes/` | `ecbbf62b1fe8031caaee98317084b6cf97c729ffdbdffd0e2ac85d65a7d343d0` |
| `2026-10-14-acme-renewal-wrapup.md` | `samples/notes/` | `3e5a92a25c0ab578f41e7ef4e82bece7a9540b3e3c40933fef3f9329b2c913a4` |
| `scratch.md` | `notes/notes/` | `e066008ba847ef5b289136a1f880f56dfaf9dd377c7f2a69afb4b3190e843542` |
| `untitled.md` | `notes/notes/` | `0d3f52cd1b52d444bf45ba9ea56d42415e1e6f150bbdc9238f556b5c94d2e1a1` |
