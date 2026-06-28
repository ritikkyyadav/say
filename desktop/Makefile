# Build pipeline for the Say desktop app.
#
#   make engine       — produce dist/Say.app via PyInstaller (engine + model)
#   make ui-build     — vite build the React frontend
#   make app          — `tauri build` the desktop shell (Say.app + DMG)
#   make embed-engine — copy the engine .app into the desktop Say.app's
#                       Contents/Resources/SayEngine.app
#   make all          — engine + ui + tauri build + embed + (optionally) DMG
#
# The Tauri shell on its own does not contain the dictation engine; the
# engine is bundled in via `embed-engine` after `tauri build`. This keeps
# the cargo check step fast and avoids embedding a multi-hundred-MB
# resource into the Rust bin.

VERSION       := 0.1.0
ROOT          := $(abspath ..)
ENGINE_DIST   := $(ROOT)/say/dist
ENGINE_APP    := $(ENGINE_DIST)/Oceanus.app
TAURI_TARGET  := $(abspath ./src-tauri/target/release/bundle)
SHELL_APP     := $(TAURI_TARGET)/macos/Oceanus.app
EMBED_TARGET  := $(SHELL_APP)/Contents/Resources/OceanusEngine.app
DMG_DIR       := $(TAURI_TARGET)/dmg

SAY_PYTHON    ?= $(HOME)/.venvs/say/bin/python
RUNTIME_JSON  := $(HOME)/Library/Application Support/Say/runtime.json

.PHONY: all engine ui-build app embed-engine patch-plist dmg clean dev dev-engine dev-shell engine-status engine-stop

all: engine ui-build app embed-engine patch-plist

# Boot the dev workflow:
#   1. Make sure the Python engine is up in --no-menubar mode (no rumps icon
#      so it can run as a plain sidecar). If runtime.json points to a live
#      pid, reuse it; otherwise spawn a fresh engine in the background.
#   2. Run `tauri dev` in the foreground. Cmd+C stops the shell only — the
#      engine keeps serving until `make engine-stop`.
dev: dev-engine dev-shell

dev-engine:
	@if [ -f "$(RUNTIME_JSON)" ] && PID=$$(/usr/bin/python3 -c "import json,sys;print(json.load(open('$(RUNTIME_JSON)')).get('pid',''))" 2>/dev/null) && [ -n "$$PID" ] && kill -0 "$$PID" 2>/dev/null; then \
		echo "[+] Engine already running (pid $$PID) — reusing."; \
	else \
		if [ ! -x "$(SAY_PYTHON)" ]; then \
			echo "[!] $(SAY_PYTHON) not found. Create the venv first:"; \
			echo "    python3 -m venv ~/.venvs/say && source ~/.venvs/say/bin/activate && pip install -r ../say/requirements.txt"; \
			exit 1; \
		fi; \
		echo "[+] Starting engine in background (--no-menubar)…"; \
		mkdir -p "$(HOME)/Library/Logs/Say"; \
		(cd "$(ROOT)/say" && nohup "$(SAY_PYTHON)" app.py --no-menubar >>"$(HOME)/Library/Logs/Say/dev-engine.log" 2>&1 &); \
		sleep 1; \
	fi

dev-shell:
	@echo "[+] Launching tauri dev shell — Cmd+C to stop (engine keeps running)."
	npm run tauri:dev

engine-status:
	@if [ ! -f "$(RUNTIME_JSON)" ]; then echo "[-] runtime.json missing — engine is not running."; exit 1; fi
	@PID=$$(/usr/bin/python3 -c "import json;print(json.load(open('$(RUNTIME_JSON)')).get('pid',''))" 2>/dev/null); \
	PORT=$$(/usr/bin/python3 -c "import json;print(json.load(open('$(RUNTIME_JSON)')).get('port',''))" 2>/dev/null); \
	if [ -n "$$PID" ] && kill -0 "$$PID" 2>/dev/null; then \
		echo "[+] Engine alive: pid=$$PID port=$$PORT"; \
		curl -sS --max-time 2 "http://127.0.0.1:$$PORT/health" && echo; \
	else \
		echo "[-] runtime.json stale (pid $$PID not running). Run 'make dev-engine'."; \
		exit 1; \
	fi

engine-stop:
	@if [ ! -f "$(RUNTIME_JSON)" ]; then echo "[-] runtime.json missing — nothing to stop."; exit 0; fi
	@PID=$$(/usr/bin/python3 -c "import json;print(json.load(open('$(RUNTIME_JSON)')).get('pid',''))" 2>/dev/null); \
	if [ -n "$$PID" ] && kill -0 "$$PID" 2>/dev/null; then \
		echo "[+] Stopping engine pid $$PID…"; \
		kill -TERM "$$PID"; \
		for _ in 1 2 3 4 5 6; do kill -0 "$$PID" 2>/dev/null || exit 0; sleep 0.5; done; \
		echo "[!] Engine didn't exit, sending SIGKILL"; kill -KILL "$$PID" 2>/dev/null || true; \
	else \
		echo "[-] Engine not running (stale runtime.json)."; \
	fi

engine:
	@echo "[+] Building Say engine (PyInstaller)..."
	$(MAKE) -C $(ROOT)/say all
	@if [ ! -d "$(ENGINE_APP)" ]; then \
		echo "[!] Engine build did not produce $(ENGINE_APP)"; exit 1; \
	fi

ui-build:
	npm run build

app:
	@echo "[+] Building desktop shell with Tauri..."
	npm run tauri:build

patch-plist:
	@if [ ! -f "$(SHELL_APP)/Contents/Info.plist" ]; then \
		echo "[!] Shell Info.plist not found — run 'make app' first"; exit 1; \
	fi
	@echo "[+] Adding microphone / accessibility / input-monitoring usage strings to shell Info.plist..."
	@PLIST="$(SHELL_APP)/Contents/Info.plist"; \
	/usr/libexec/PlistBuddy -c "Delete :NSMicrophoneUsageDescription" "$$PLIST" 2>/dev/null; \
	/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string 'Say records your voice to transcribe it locally on this Mac. No audio is uploaded.'" "$$PLIST"; \
	/usr/libexec/PlistBuddy -c "Delete :NSAccessibilityUsageDescription" "$$PLIST" 2>/dev/null; \
	/usr/libexec/PlistBuddy -c "Add :NSAccessibilityUsageDescription string 'Say uses Accessibility APIs to inject transcribed text and watch the Fn hotkey.'" "$$PLIST"; \
	/usr/libexec/PlistBuddy -c "Delete :NSAppleEventsUsageDescription" "$$PLIST" 2>/dev/null; \
	/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string 'Say sends synthetic keyboard events to insert transcribed text into the active app.'" "$$PLIST"; \
	/usr/libexec/PlistBuddy -c "Delete :NSCameraUsageDescription" "$$PLIST" 2>/dev/null; \
	/usr/libexec/PlistBuddy -c "Add :NSCameraUsageDescription string 'Say does not use the camera.'" "$$PLIST"; \
	echo "[OK] Patched $$PLIST"
	@# Rename the executable from "say-desktop" to "Oceanus" so macOS shows
	@# the right label in Privacy & Security panes (TCC uses
	@# CFBundleExecutable when an ad-hoc-signed app is listed).
	@MACOS_DIR="$(SHELL_APP)/Contents/MacOS"; \
	if [ -f "$$MACOS_DIR/say-desktop" ] && [ ! -f "$$MACOS_DIR/Oceanus" ]; then \
		mv "$$MACOS_DIR/say-desktop" "$$MACOS_DIR/Oceanus"; \
		/usr/libexec/PlistBuddy -c "Set :CFBundleExecutable Oceanus" "$(SHELL_APP)/Contents/Info.plist"; \
		echo "[OK] Renamed binary to Oceanus + CFBundleExecutable=Oceanus"; \
	fi
	@# Strip every extended attribute that codesign refuses (FinderInfo,
	@# resource forks, fileprovider tags, provenance).  `xattr -cr`
	@# misses some so we hit each known offender explicitly.
	@find "$(SHELL_APP)" -print0 | xargs -0 -I{} xattr -d com.apple.FinderInfo {} 2>/dev/null || true
	@find "$(SHELL_APP)" -print0 | xargs -0 -I{} xattr -d com.apple.ResourceFork {} 2>/dev/null || true
	@find "$(SHELL_APP)" -print0 | xargs -0 -I{} xattr -d com.apple.fileprovider.fpfs#P {} 2>/dev/null || true
	@find "$(SHELL_APP)" -print0 | xargs -0 -I{} xattr -d com.apple.provenance {} 2>/dev/null || true
	@xattr -cr "$(SHELL_APP)" 2>/dev/null || true
	@# The embedded engine (OceanusEngine.app) is ALREADY validly ad-hoc-signed
	@# by `make -C ../say` (PyInstaller seal + post-model re-seal). Do NOT re-sign
	@# it here — a post-hoc codesign of that PyInstaller bundle fails on its
	@# data-in-Frameworks / Python.framework layout and would leave a broken seal,
	@# which is exactly what blocks macOS from honouring Accessibility/Input-
	@# Monitoring grants. We only (re-)sign the SHELL, whose Info.plist + binary
	@# name we just edited. Signing the shell bundle (non-deep) re-seals it and
	@# validates the nested engine by reference.
	@echo "[+] Verifying embedded engine seal is intact before signing shell…"
	@codesign --verify --strict "$(SHELL_APP)/Contents/Resources/OceanusEngine.app" \
		|| { echo "[!] embedded engine seal is broken — rebuild the engine (make -C ../say all)"; exit 1; }
	@codesign --force --sign - --identifier com.oceanus.dictation.desktop "$(SHELL_APP)"
	@echo "[+] Verifying final signatures…"
	@codesign --verify --strict "$(SHELL_APP)" \
		|| { echo "[!] shell seal invalid after signing — aborting"; exit 1; }
	@codesign --verify --strict "$(SHELL_APP)/Contents/Resources/OceanusEngine.app" \
		|| { echo "[!] engine seal broke while signing shell — aborting"; exit 1; }
	@echo "[OK] Both bundles validly signed:"
	@echo "    Shell:  $$(codesign -dv '$(SHELL_APP)' 2>&1 | grep Identifier)"
	@echo "    Engine: $$(codesign -dv '$(SHELL_APP)/Contents/Resources/OceanusEngine.app' 2>&1 | grep Identifier)"

embed-engine:
	@if [ ! -d "$(SHELL_APP)" ]; then \
		echo "[!] Tauri shell .app not found at $(SHELL_APP) — run 'make app' first"; exit 1; \
	fi
	@if [ ! -d "$(ENGINE_APP)" ]; then \
		echo "[!] Engine .app not found at $(ENGINE_APP) — run 'make engine' first"; exit 1; \
	fi
	@echo "[+] Embedding engine into $(SHELL_APP)..."
	rm -rf "$(EMBED_TARGET)"
	mkdir -p "$(SHELL_APP)/Contents/Resources"
	@# Use ditto, NOT `rsync -aL`: the -L flag dereferences symlinks, which
	@# flattens Python.framework's Versions/Current symlink into a real dir and
	@# destroys the engine's code-signature seal (codesign then reports the
	@# bundle "ambiguous"). ditto preserves symlinks + the seal intact.
	ditto "$(ENGINE_APP)" "$(EMBED_TARGET)"
	@codesign --verify --strict "$(EMBED_TARGET)" \
		&& echo "[OK] Embedded engine (seal intact) at $(EMBED_TARGET)" \
		|| { echo "[!] engine seal broke during embed — aborting"; exit 1; }
	@du -sh "$(SHELL_APP)" || true

dmg:
	@if [ ! -d "$(SHELL_APP)" ]; then \
		echo "[!] Need $(SHELL_APP) — run 'make all' first"; exit 1; \
	fi
	@if ! command -v create-dmg >/dev/null 2>&1; then \
		echo "[!] create-dmg not installed. Install with: brew install create-dmg"; exit 1; \
	fi
	@rm -f "$(DMG_DIR)/Oceanus_$(VERSION)_aarch64.dmg"
	create-dmg \
		--volname "Oceanus v$(VERSION)" \
		--window-size 500 320 \
		--icon-size 100 \
		--icon "Oceanus.app" 130 130 \
		--app-drop-link 370 130 \
		--no-internet-enable \
		"$(DMG_DIR)/Oceanus_$(VERSION)_aarch64.dmg" \
		"$(SHELL_APP)"
	@ls -lh "$(DMG_DIR)/Oceanus_$(VERSION)_aarch64.dmg"

clean:
	rm -rf node_modules dist src-tauri/target src-tauri/gen
