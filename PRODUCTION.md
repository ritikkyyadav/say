# Production Readiness — Say

Honest status of how close Say is to shippable, and the gates that remain.
Last hardening pass: **2026-06-25** (model management).

## TL;DR

- **Usable on your own Mac:** yes (pending a one-time real-dictation smoke — see below).
- **Beta for friends:** blocked only on code signing + the Fn-hotkey decision.
- **Production for strangers:** gated on signing/notarization, a clean-machine E2E pass, and an installer.

## Hardened & verified (automated)

The model-management surface — Say's differentiator — is production-hardened and covered by tests (`pytest say/tests/`, 90 tests):

- **Live model switching** — no relaunch; `set_active()` swaps the running transcriber.
- **Downloads** — real-byte progress, **disk-space pre-check** (→ HTTP 507), **true cancel** (aborts mid-stream + cleans the partial cache), categorized errors (offline / disk full / other).
- **Deletes** — guarded (refuses active / bundled / in-flight) + two-step UI confirmation.
- **Privacy** — loopback-only API, per-launch bearer token, audio never written to disk, CORS locked to the shell origins. Only model *weights* are fetched (from Hugging Face to the local cache); audio + transcripts never leave the device.
- **Catalog/config drift** — `config.VALID_MODELS` is test-guarded to equal `model_catalog.CATALOG_IDS`.

Build/quality gates green: `ruff`, `mypy`, `tsc`, `vite build`.

## NOT yet verified by automation (needs a human)

These are the honest gaps — none are exercised headlessly:

1. **Core dictation loop** (press Fn → record → mlx transcribe → inject) — needs Mic + Accessibility + Input Monitoring grants and a physical keypress.
2. **A real model download** over the network (tests use a faked snapshot to stay hermetic/offline).
3. **The Tauri app actually launching** and rendering (only `vite build` is automated).
4. **First-run permissions wizard** on a clean machine.

### One-time manual smoke (do this before trusting it)

```bash
# 1. Engine
python say/app.py --server
# 2. UI (new terminal)
cd desktop && npm run tauri:dev
# 3. In the app: open Models (Cmd+3) → download a small model → "Use this model".
# 4. Focus any text field, hold Fn (or ctrl+space), speak, release → text appears.
```

If step 4 works, the foundation is sound.

## Release gates you must own

| Gate | Why it needs you | Where |
|------|------------------|-------|
| **Code signing** | Needs your Apple **Developer ID** cert | `make sign` (`DEVELOPER_ID`) |
| **Notarization** | Needs `APPLE_ID` / `APPLE_TEAM_ID` / `APPLE_APP_PASSWORD` | `make notarize` |
| **Gatekeeper** | An unsigned `.dmg` is blocked on other Macs | follows from the two above |
| **Permission grants** | TCC prompts can't be scripted | manual, first launch |

## Recommended before a public 0.1

- [ ] Pass the manual E2E smoke above on a clean macOS user account.
- [ ] Sign + notarize the `.dmg`; verify it opens on a second Mac.
- [ ] Decide the hotkey story (Fn default + auto-fallback is wired; confirm Fn suppression on your target macOS, or set `PREFER_LEGACY_HOTKEY=True`).
- [ ] Add a frontend test runner (Vitest) — UI correctness currently rests on `tsc` + review.
- [ ] Confirm graceful behavior when the active model's weights are missing at launch (lazy re-download path).
