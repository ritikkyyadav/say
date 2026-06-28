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
# The bundled default is the small Moonshine ONNX model (English). The big
# multilingual Whisper models are optional downloads from the Models tab, so
# only Moonshine ships inside the app.
MODEL_NAME    := $(if $(SAY_MODEL),$(SAY_MODEL),moonshine/medium-streaming-en)
MODEL_LEAF    := $(notdir $(MODEL_NAME))
MOON_CACHE    := $(HOME)/.cache/moonshine/download.moonshine.ai/model/$(MODEL_LEAF)/quantized

.PHONY: all build dmg sign notarize clean install predownload bundle-model

all: install predownload build bundle-model dmg

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
		&& echo "[OK] Built + validly ad-hoc-signed $(APP_BUNDLE)" \
		|| { echo "[!] PyInstaller seal invalid — aborting"; exit 1; }

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
	@codesign --force --sign - --identifier com.oceanus.dictation "$(APP_BUNDLE)"
	@codesign --verify --strict "$(APP_BUNDLE)" \
		&& echo "[OK] Re-sealed engine bundle with Moonshine model ($(APP_BUNDLE))" \
		|| { echo "[!] Engine re-seal invalid after bundle-model — aborting"; exit 1; }

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
