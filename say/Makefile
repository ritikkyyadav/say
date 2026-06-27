# Makefile — Say build, package, sign, notarize.
#
# Quick start:
#   make build       — produce dist/Say.app (unsigned)
#   make dmg         — bundle Say.app into dist/Say-vX.Y.Z-unsigned.dmg
#   make sign        — codesign the .app (requires DEVELOPER_ID env var)
#   make notarize    — submit to Apple notary service (requires DEVELOPER_ID
#                      and APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD)
#   make all         — install deps, predownload model, build, dmg
#   make clean       — remove build/ and dist/
#
# Without an Apple Developer ID the unsigned `.app` and DMG still work,
# but users must right-click → Open the first time to bypass Gatekeeper.

VERSION       := 0.1.0
APP_NAME      := Oceanus
PYTHON        := $(if $(SAY_PYTHON),$(SAY_PYTHON),$(HOME)/.venvs/say/bin/python)
PIP           := $(PYTHON) -m pip
PYINSTALLER   := $(PYTHON) -m PyInstaller
DIST          := dist
APP_BUNDLE    := $(DIST)/$(APP_NAME).app
DMG_NAME      := $(APP_NAME)-v$(VERSION)-unsigned.dmg
DMG_PATH      := $(DIST)/$(DMG_NAME)
MODEL_NAME    := $(if $(SAY_MODEL),$(SAY_MODEL),mlx-community/whisper-large-v3-turbo)
MODEL_LEAF    := $(notdir $(MODEL_NAME))
HF_CACHE      := $(HOME)/.cache/huggingface/hub/models--$(subst /,--,$(MODEL_NAME))

.PHONY: all build dmg sign notarize clean install predownload bundle-model

all: install predownload build bundle-model dmg

install:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install pyinstaller create-dmg-cli || true

predownload:
	$(PYTHON) -c "from transcriber import Transcriber; Transcriber().predownload()"

build:
	$(PYINSTALLER) --noconfirm --clean Say.spec
	@# Patch Info.plist with our authoritative copy (overrides what BUNDLE() set)
	cp Info.plist "$(APP_BUNDLE)/Contents/Info.plist"
	@echo "[OK] Built unsigned $(APP_BUNDLE)"

bundle-model:
	@if [ ! -d "$(HF_CACHE)" ]; then \
		echo "[!] HF cache not found at $(HF_CACHE) — run 'make predownload' first"; \
		exit 1; \
	fi
	@SNAP=$$(ls -1 "$(HF_CACHE)/snapshots" | head -n1); \
		if [ -z "$$SNAP" ]; then echo "[!] No snapshot in $(HF_CACHE)"; exit 1; fi; \
		mkdir -p "$(APP_BUNDLE)/Contents/Resources/models/$(MODEL_LEAF)"; \
		echo "[+] Copying $(HF_CACHE)/snapshots/$$SNAP -> bundle"; \
		rsync -aL "$(HF_CACHE)/snapshots/$$SNAP/" "$(APP_BUNDLE)/Contents/Resources/models/$(MODEL_LEAF)/"

dmg:
	@if ! command -v create-dmg >/dev/null 2>&1; then \
		echo "[!] create-dmg not installed. Install via: brew install create-dmg"; \
		exit 1; \
	fi
	@rm -f "$(DMG_PATH)"
	create-dmg \
		--volname "$(APP_NAME) v$(VERSION)" \
		--window-size 500 320 \
		--icon-size 100 \
		--icon "$(APP_NAME).app" 130 130 \
		--app-drop-link 370 130 \
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
