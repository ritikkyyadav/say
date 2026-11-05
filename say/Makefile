# Makefile — Say build, package, sign, notarize.
#
# Quick start:
#   make build       — produce dist/Say.app (unsigned)
#   make dmg         — bundle the ENGINE into dist/SayEngine-vX.Y.Z-internal.dmg
#                      (a component, NOT the shippable product — see below)
#   make sign        — codesign the .app (requires DEVELOPER_ID env var)
#   make notarize    — submit to Apple notary service (requires DEVELOPER_ID
#                      and APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD)
#   make all         — install deps, predownload model, build, bundle model
#   make clean       — remove build/ and dist/
#
# Without an Apple Developer ID the unsigned `.app` and DMG still work,
# but users must right-click → Open the first time to bypass Gatekeeper.

VERSION       := 0.1.0
APP_NAME      := Say
PYTHON        := $(if $(SAY_PYTHON),$(SAY_PYTHON),$(HOME)/.venvs/say/bin/python)
PIP           := $(PYTHON) -m pip
PYINSTALLER   := $(PYTHON) -m PyInstaller
DIST          := dist
APP_BUNDLE    := $(DIST)/$(APP_NAME).app
# The engine is an internal component that the desktop shell embeds as
# SayEngine.app — it is NOT the app a user installs. It is LSUIElement=true, so
# launching it directly shows no window at all. Naming its disk image "Say" and
# giving it an /Applications drop-link made it indistinguishable from the real
# product DMG (desktop/…/Say_$(VERSION)_aarch64.dmg) and put a headless agent in
# /Applications, where it looked like "the app won't open". Hence the explicit
# name, and no drop-link below.
DMG_NAME      := SayEngine-v$(VERSION)-internal.dmg
DMG_PATH      := $(DIST)/$(DMG_NAME)
# The bundled default is the small Moonshine ONNX model (English). The big
# multilingual Whisper models are optional downloads from the Models tab, so
# only Moonshine ships inside the app.
MODEL_NAME    := $(if $(SAY_MODEL),$(SAY_MODEL),moonshine/medium-streaming-en)

# Stable local code-signing identity. An ad-hoc signature ('-') gives the
# bundle a Designated Requirement of `cdhash H"..."` and nothing else, so a
# rebuild invalidates every Accessibility / Input-Monitoring grant while
# System Settings still shows the switch on. `make signing-identity` mints a
# self-signed cert once; SIGN_ID falls back to ad-hoc when it is absent so a
# fresh clone / CI still builds.
SIGN_ID       := $(if $(SAY_SIGN_ID),$(SAY_SIGN_ID),Say Local Signing)
# Export the RESOLVED name. A bare `export SAY_SIGN_ID` exports an empty
# string when the user has not set one, and Say.spec / sub-makes then see
# a present-but-empty identity and fall back to ad-hoc signing.
export SAY_SIGN_ID := $(SIGN_ID)
resolve_sign_id = ID="$(SIGN_ID)"; security find-certificate -c "$$ID" >/dev/null 2>&1 || { \
	echo "[!] signing identity '$$ID' not installed — falling back to ad-hoc."; \
	echo "    TCC grants will not survive rebuilds. Run 'make signing-identity'."; \
	ID='-'; }
MODEL_LEAF    := $(notdir $(MODEL_NAME))
MOON_CACHE    := $(HOME)/.cache/moonshine/download.moonshine.ai/model/$(MODEL_LEAF)/quantized

.PHONY: all build dmg sign notarize clean install predownload bundle-model signing-identity

# Deliberately stops at bundle-model: the engine is consumed by
# `desktop/make all`, which embeds it. Run `make dmg` explicitly if you really
# want a standalone engine image; `cd desktop && make dmg` is the product one.
all: install predownload build bundle-model

install:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install pyinstaller create-dmg-cli || true

predownload:
	$(PYTHON) -c "from transcriber import Transcriber; Transcriber().predownload(print)"

build:
	@# moonshine_voice ships a Linux libmoonshine.so beside the macOS .dylib.
	@# PyInstaller's ctypes scan finds the "libmoonshine.so" CDLL literal and
	@# tries to Mach-O-parse the ELF (build crash). Hide it for the macOS build,
	@# then restore it (failure-safe) so only the .dylib is collected.
	@# NOTE: do NOT cp a different Info.plist over the bundle here — PyInstaller
	@# ad-hoc signs the bundle (codesign_identity='-' in Say.spec) and any
	@# post-sign edit to Contents/Info.plist breaks the code-signature seal,
	@# which makes macOS refuse all TCC (Accessibility/Input-Monitoring) grants.
	@MV_SO=$$($(PYTHON) -c "import moonshine_voice,os;print(os.path.join(os.path.dirname(moonshine_voice.__file__),'libmoonshine.so'))"); \
		[ -f "$$MV_SO" ] && mv "$$MV_SO" "$$MV_SO.bak" || true; \
		$(PYINSTALLER) --noconfirm --clean Say.spec; RC=$$?; \
		[ -f "$$MV_SO.bak" ] && mv "$$MV_SO.bak" "$$MV_SO" || true; \
		[ $$RC -eq 0 ] || { echo "[!] PyInstaller failed (rc=$$RC)"; exit $$RC; }
	@codesign --verify --strict "$(APP_BUNDLE)" \
		|| { echo "[!] PyInstaller seal invalid — aborting"; exit 1; }
	@# Report what TCC will actually pin the grant to. A bare cdhash means every
	@# rebuild silently orphans Accessibility / Input Monitoring while System
	@# Settings still shows the switch on — the failure this used to hide.
	@echo "[OK] Built + validly signed $(APP_BUNDLE)"
	@echo "    $$(codesign -d -r- '$(APP_BUNDLE)' 2>&1 | grep designated)"
	@if codesign -d -r- "$(APP_BUNDLE)" 2>&1 | grep -q "designated => cdhash"; then \
		echo "[warn] bare cdhash requirement — TCC grants will NOT survive the next"; \
		echo "       rebuild. Fix with: make signing-identity"; \
	else echo "[OK] Identity-anchored — TCC grants survive rebuilds."; fi

bundle-model:
	@if [ ! -d "$(MOON_CACHE)" ]; then \
		echo "[!] Moonshine model not found at $(MOON_CACHE) — run 'make predownload' first"; \
		exit 1; \
	fi
	mkdir -p "$(APP_BUNDLE)/Contents/Resources/models/$(MODEL_LEAF)"
	@echo "[+] Copying Moonshine model ($(MODEL_LEAF)) -> bundle"
	rsync -aL "$(MOON_CACHE)/" "$(APP_BUNDLE)/Contents/Resources/models/$(MODEL_LEAF)/"
	@# Bundling the model into Contents/Resources/ invalidates the PyInstaller
	@# seal, so re-seal. A plain (non-deep) re-sign works: the nested Mach-O is
	@# already validly signed by PyInstaller and the model is plain .ort data
	@# (not Mach-O) under Resources/.
	@xattr -cr "$(APP_BUNDLE)" 2>/dev/null || true
	@$(resolve_sign_id); \
		codesign --force --sign "$$ID" --identifier com.ritikyadav.say.dictation "$(APP_BUNDLE)"
	@codesign --verify --strict "$(APP_BUNDLE)" \
		&& echo "[OK] Re-sealed engine bundle with Moonshine model ($(APP_BUNDLE))" \
		|| { echo "[!] Engine re-seal invalid after bundle-model — aborting"; exit 1; }
	@echo "    Requirement: $$(codesign -d -r- '$(APP_BUNDLE)' 2>&1 | grep designated)"
	@codesign -d -r- "$(APP_BUNDLE)" 2>&1 | grep -q "cdhash" \
		&& echo "[warn] ad-hoc requirement — Accessibility grants will die on the next rebuild" \
		|| true

dmg:
	@if ! command -v create-dmg >/dev/null 2>&1; then \
		echo "[!] create-dmg not installed. Install via: brew install create-dmg"; \
		exit 1; \
	fi
	@rm -f "$(DMG_PATH)"
	@echo "[i] Building the ENGINE image. This is a component, not the product."
	@echo "    Users install desktop/src-tauri/target/release/bundle/dmg/Say_$(VERSION)_aarch64.dmg."
	@# No --app-drop-link on purpose: this bundle is LSUIElement=true and shows
	@# no window, so an /Applications drop target here only invites a broken install.
	create-dmg \
		--volname "Say Engine v$(VERSION) (component)" \
		--window-size 500 320 \
		--icon-size 100 \
		--icon "$(APP_NAME).app" 250 130 \
		--no-internet-enable \
		"$(DMG_PATH)" \
		"$(APP_BUNDLE)" || true
	@if [ -f "$(DMG_PATH)" ]; then echo "[OK] $(DMG_PATH)"; else echo "[!] DMG not created"; exit 1; fi

sign:
	@if [ -z "$(DEVELOPER_ID)" ]; then \
		echo "[skip] DEVELOPER_ID not set — leaving unsigned. Set it like:"; \
		echo "       DEVELOPER_ID='Developer ID Application: Your Name (TEAMID)' make sign"; \
		exit 0; \
	fi
	codesign --deep --force --options runtime --timestamp \
		--sign "$(DEVELOPER_ID)" "$(APP_BUNDLE)"
	codesign -dv --verbose=4 "$(APP_BUNDLE)"
	@echo "[OK] Signed $(APP_BUNDLE)"

notarize:
	@if [ -z "$(DEVELOPER_ID)" ] || [ -z "$(APPLE_ID)" ] || [ -z "$(APPLE_TEAM_ID)" ] || [ -z "$(APPLE_APP_PASSWORD)" ]; then \
		echo "[skip] Notarization requires DEVELOPER_ID, APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD."; \
		exit 0; \
	fi
	@if [ ! -f "$(DMG_PATH)" ]; then echo "[!] $(DMG_PATH) missing — run 'make dmg' first"; exit 1; fi
	xcrun notarytool submit "$(DMG_PATH)" \
		--apple-id "$(APPLE_ID)" --team-id "$(APPLE_TEAM_ID)" --password "$(APPLE_APP_PASSWORD)" \
		--wait
	xcrun stapler staple "$(DMG_PATH)"
	@echo "[OK] Notarized $(DMG_PATH)"

clean:
	rm -rf build dist *.spec.bak
	@echo "[OK] cleaned"

# Mint the stable local code-signing certificate (idempotent).
#
# Why this exists: macOS TCC stores a bundle's Designated Requirement when a
# permission is granted, and re-checks it on every launch. Ad-hoc signing
# produces `designated => cdhash H"<exact build bytes>"`, so the grant is
# pinned to one build and every `make all` orphans it — the Accessibility row
# stays visibly on while AXIsProcessTrusted() returns False and Fn dies. A
# self-signed cert produces `identifier "..." and certificate root = H"..."`
# instead, which is identical across rebuilds.
signing-identity:
	@if security find-certificate -c "$(SIGN_ID)" >/dev/null 2>&1; then \
		echo "[OK] '$(SIGN_ID)' already installed."; \
	else \
		echo "[+] Creating self-signed code-signing certificate '$(SIGN_ID)'…"; \
		TMP=$$(mktemp -d); \
		openssl req -x509 -newkey rsa:2048 -keyout "$$TMP/key.pem" -out "$$TMP/cert.pem" \
			-days 7300 -nodes -subj "/CN=$(SIGN_ID)/O=Say/C=US" \
			-addext "basicConstraints=critical,CA:false" \
			-addext "keyUsage=critical,digitalSignature" \
			-addext "extendedKeyUsage=critical,codeSigning" 2>/dev/null \
			|| { echo "[!] openssl failed"; rm -rf "$$TMP"; exit 1; }; \
		openssl pkcs12 -export -legacy -out "$$TMP/id.p12" -inkey "$$TMP/key.pem" \
			-in "$$TMP/cert.pem" -passout pass:saylocal -name "$(SIGN_ID)" 2>/dev/null \
			|| { echo "[!] pkcs12 export failed (needs -legacy: Security.framework cannot read AES-encrypted PKCS12)"; rm -rf "$$TMP"; exit 1; }; \
		security import "$$TMP/id.p12" -k "$(HOME)/Library/Keychains/login.keychain-db" \
			-P saylocal -A -T /usr/bin/codesign -T /usr/bin/security \
			|| { echo "[!] keychain import failed"; rm -rf "$$TMP"; exit 1; }; \
		rm -rf "$$TMP"; \
		echo "[OK] '$(SIGN_ID)' installed in the login keychain."; \
	fi
	@echo "    Rebuild + reinstall, then grant Accessibility once — it now survives rebuilds."
