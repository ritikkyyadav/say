# Build pipeline for the Say desktop app.
#
#   make engine       — produce dist/Say.app via PyInstaller (engine + model)
#   make ui-build     — vite build the React frontend
#   make app          — `tauri build` the desktop shell (Say.app + DMG)
#   make embed-engine — copy the engine .app into the desktop Say.app's
#                       Contents/Resources/SayEngine.app
#   make all          — engine + ui + tauri build + embed + (optionally) DMG
#   make install      — replace /Applications/Say.app with the shell built here
#
# The Tauri shell on its own does not contain the dictation engine; the
# engine is bundled in via `embed-engine` after `tauri build`. This keeps
# the cargo check step fast and avoids embedding a multi-hundred-MB
# resource into the Rust bin.

VERSION       := 0.1.0
ROOT          := $(abspath ..)
ENGINE_DIST   := $(ROOT)/say/dist
ENGINE_APP    := $(ENGINE_DIST)/Say.app
TAURI_TARGET  := $(abspath ./src-tauri/target/release/bundle)
SHELL_APP     := $(TAURI_TARGET)/macos/Say.app
EMBED_TARGET  := $(SHELL_APP)/Contents/Resources/SayEngine.app
DMG_DIR       := $(TAURI_TARGET)/dmg

SAY_PYTHON    ?= $(HOME)/.venvs/say/bin/python

# Stable local code-signing identity — see `make -C ../say signing-identity`.
# Ad-hoc signing ('-') gives the shell a Designated Requirement of
# `cdhash H"..."`, which macOS TCC pins the Accessibility grant to; every
# rebuild then orphans it while the switch still reads as on.
SIGN_ID       := $(if $(SAY_SIGN_ID),$(SAY_SIGN_ID),Say Local Signing)
# Export the RESOLVED name. A bare `export SAY_SIGN_ID` exports an empty
# string when the user has not set one, and Say.spec / sub-makes then see
# a present-but-empty identity and fall back to ad-hoc signing.
export SAY_SIGN_ID := $(SIGN_ID)
RUNTIME_JSON  := $(HOME)/Library/Application Support/Say/runtime.json

.PHONY: all engine ui-build check-bundle app embed-engine patch-plist dmg install clean dev dev-engine dev-shell engine-status engine-stop

all: engine ui-build app check-bundle embed-engine patch-plist

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
	@$(MAKE) --no-print-directory check-bundle

# The shipped JavaScript must never carry the engine's bearer token. vite.config.ts
# only reads runtime.json for the dev server, but a build made while an engine is
# running is exactly when a regression would leak a live one, so look for it.
check-bundle:
	@if [ -f "$(RUNTIME_JSON)" ]; then \
		TOKEN=$$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token") or "")' "$(RUNTIME_JSON)" 2>/dev/null); \
		if [ -n "$$TOKEN" ] && grep -rqF -- "$$TOKEN" dist; then \
			echo "[FAIL] dist/ contains the running engine's bearer token. Not shipping it."; exit 1; \
		fi; \
	fi
	@echo "[OK] No engine token in dist/"

app:
	@echo "[+] Building desktop shell with Tauri..."
	npm run tauri:build

patch-plist:
	@if [ ! -f "$(SHELL_APP)/Contents/Info.plist" ]; then \
		echo "[!] Shell Info.plist not found — run 'make app' first"; exit 1; \
	fi
	@# STRIP XATTRS FIRST. On macOS 15+ a freshly-built bundle carries
	@# `com.apple.provenance`, and while it is present every write into
	@# Contents/ fails with "Operation not permitted" — PlistBuddy silently
	@# does nothing and the `mv` below refuses. This used to run *after* the
	@# edits, which is why the shipped app ended up with no usage strings and
	@# CFBundleExecutable still set to "say-desktop". Order matters.
	@find "$(SHELL_APP)" -print0 | xargs -0 -I{} xattr -d com.apple.FinderInfo {} 2>/dev/null || true
	@find "$(SHELL_APP)" -print0 | xargs -0 -I{} xattr -d com.apple.ResourceFork {} 2>/dev/null || true
	@find "$(SHELL_APP)" -print0 | xargs -0 -I{} xattr -d com.apple.fileprovider.fpfs#P {} 2>/dev/null || true
	@find "$(SHELL_APP)" -print0 | xargs -0 -I{} xattr -d com.apple.provenance {} 2>/dev/null || true
	@xattr -cr "$(SHELL_APP)" 2>/dev/null || true
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
	@# Verify rather than trust: PlistBuddy exits 0 even when the write was
	@# refused, so without this the build reports success on a broken app.
	@PLIST="$(SHELL_APP)/Contents/Info.plist"; \
	for KEY in NSMicrophoneUsageDescription NSAccessibilityUsageDescription NSAppleEventsUsageDescription; do \
		/usr/libexec/PlistBuddy -c "Print :$$KEY" "$$PLIST" >/dev/null 2>&1 \
			|| { echo "[!] $$KEY missing from Info.plist — the patch did not apply"; exit 1; }; \
	done; \
	echo "[OK] Verified usage strings present"
	@# Rename the executable from "say-desktop" to "Say" so macOS shows
	@# the right label in Privacy & Security panes (TCC uses
	@# CFBundleExecutable when an ad-hoc-signed app is listed).
	@MACOS_DIR="$(SHELL_APP)/Contents/MacOS"; \
	if [ -f "$$MACOS_DIR/say-desktop" ] && [ ! -f "$$MACOS_DIR/Say" ]; then \
		mv "$$MACOS_DIR/say-desktop" "$$MACOS_DIR/Say" \
			|| { echo "[!] could not rename the shell binary"; exit 1; }; \
		/usr/libexec/PlistBuddy -c "Set :CFBundleExecutable Say" "$(SHELL_APP)/Contents/Info.plist"; \
	fi; \
	EXEC=$$(/usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" "$(SHELL_APP)/Contents/Info.plist" 2>/dev/null); \
	[ "$$EXEC" = "Say" ] || { echo "[!] CFBundleExecutable is '$$EXEC', expected 'Say'"; exit 1; }; \
	[ -f "$$MACOS_DIR/Say" ] || { echo "[!] $$MACOS_DIR/Say missing"; exit 1; }; \
	echo "[OK] Binary is Say + CFBundleExecutable=Say"
	@# The embedded engine (SayEngine.app) is ALREADY validly ad-hoc-signed
	@# by `make -C ../say` (PyInstaller seal + post-model re-seal). Do NOT re-sign
	@# it here — a post-hoc codesign of that PyInstaller bundle fails on its
	@# data-in-Frameworks / Python.framework layout and would leave a broken seal,
	@# which is exactly what blocks macOS from honouring Accessibility/Input-
	@# Monitoring grants. We only (re-)sign the SHELL, whose Info.plist + binary
	@# name we just edited. Signing the shell bundle (non-deep) re-seals it and
	@# validates the nested engine by reference.
	@echo "[+] Verifying embedded engine seal is intact before signing shell…"
	@codesign --verify --strict "$(SHELL_APP)/Contents/Resources/SayEngine.app" \
		|| { echo "[!] embedded engine seal is broken — rebuild the engine (make -C ../say all)"; exit 1; }
	@ID="$(SIGN_ID)"; security find-certificate -c "$$ID" >/dev/null 2>&1 || { \
		echo "[!] signing identity '$$ID' not installed — falling back to ad-hoc."; \
		echo "    TCC grants will not survive rebuilds. Run 'make -C ../say signing-identity'."; \
		ID='-'; }; \
		codesign --force --sign "$$ID" --identifier com.ritikyadav.say.desktop "$(SHELL_APP)"
	@echo "[+] Verifying final signatures…"
	@codesign --verify --strict "$(SHELL_APP)" \
		|| { echo "[!] shell seal invalid after signing — aborting"; exit 1; }
	@codesign --verify --strict "$(SHELL_APP)/Contents/Resources/SayEngine.app" \
		|| { echo "[!] engine seal broke while signing shell — aborting"; exit 1; }
	@echo "[OK] Both bundles validly signed:"
	@echo "    Shell:  $$(codesign -dv '$(SHELL_APP)' 2>&1 | grep Identifier)"
	@echo "    Engine: $$(codesign -dv '$(SHELL_APP)/Contents/Resources/SayEngine.app' 2>&1 | grep Identifier)"
	@echo "[+] Designated requirements (what TCC pins the grant to):"
	@echo "    Shell:  $$(codesign -d -r- '$(SHELL_APP)' 2>&1 | grep designated)"
	@echo "    Engine: $$(codesign -d -r- '$(SHELL_APP)/Contents/Resources/SayEngine.app' 2>&1 | grep designated)"
	@if codesign -d -r- "$(SHELL_APP)/Contents/Resources/SayEngine.app" 2>&1 | grep -q cdhash; then \
		echo "[warn] engine requirement is a bare cdhash — Accessibility/Input Monitoring"; \
		echo "       will break on the NEXT rebuild. Fix: make -C ../say signing-identity"; \
	else echo "[OK] Requirement is identity-anchored — TCC grants survive rebuilds."; fi

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
	@mkdir -p "$(DMG_DIR)"
	@rm -f "$(DMG_DIR)/Say_$(VERSION)_aarch64.dmg"
	create-dmg \
		--volname "Say v$(VERSION)" \
		--window-size 500 320 \
		--icon-size 100 \
		--icon "Say.app" 130 130 \
		--app-drop-link 370 130 \
		--no-internet-enable \
		"$(DMG_DIR)/Say_$(VERSION)_aarch64.dmg" \
		"$(SHELL_APP)"
	@ls -lh "$(DMG_DIR)/Say_$(VERSION)_aarch64.dmg"

INSTALL_DIR   := /Applications
INSTALLED_APP := $(INSTALL_DIR)/Say.app

# Install the SHELL — the bundle with the window — into /Applications.
#
# Doing this by hand is error-prone because two different bundles are called
# "Say.app": this one (com.ritikyadav.say.desktop, owns the UI) and the engine
# it embeds (com.ritikyadav.say.dictation, LSUIElement=true, no window at all).
# Dragging the engine into /Applications yields an app that launches, shows
# nothing, and reads as "the interface won't load". This target always picks
# the right one and refuses to install the wrong one.
#
# ditto, not cp -R/rsync -aL: it preserves the framework symlinks and the code
# signature seal. A broken seal makes macOS silently refuse every TCC grant.
install:
	@if [ ! -d "$(SHELL_APP)" ]; then \
		echo "[!] $(SHELL_APP) missing — run 'make all' first"; exit 1; \
	fi
	@ID=$$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$(SHELL_APP)/Contents/Info.plist" 2>/dev/null); \
	if [ "$$ID" != "com.ritikyadav.say.desktop" ]; then \
		echo "[!] $(SHELL_APP) is '$$ID', not the desktop shell. Refusing to install."; exit 1; \
	fi
	@if [ ! -d "$(EMBED_TARGET)" ]; then \
		echo "[!] No SayEngine.app inside the shell — run 'make embed-engine'."; exit 1; \
	fi
	@codesign --verify --strict "$(SHELL_APP)" || { echo "[!] Shell seal is broken — TCC would refuse every grant. Aborting."; exit 1; }
	@echo "[+] Stopping any running Say…"
	@pkill -f "$(INSTALLED_APP)/Contents/MacOS/Say" 2>/dev/null || true
	@$(MAKE) --no-print-directory engine-stop 2>/dev/null || true
	@# Stage beside the target and swap only once the copy verifies, so an
	@# interrupted or corrupt ditto can never leave /Applications holding a
	@# half-written bundle — the previous working app stays put until the
	@# replacement has proven itself.
	@echo "[+] Staging → $(INSTALLED_APP).staging"
	@rm -rf "$(INSTALLED_APP).staging"
	@ditto "$(SHELL_APP)" "$(INSTALLED_APP).staging"
	@codesign --verify --strict "$(INSTALLED_APP).staging" || { rm -rf "$(INSTALLED_APP).staging"; echo "[!] Staged copy failed verification — /Applications untouched"; exit 1; }
	@codesign --verify --strict "$(INSTALLED_APP).staging/Contents/Resources/SayEngine.app" || { rm -rf "$(INSTALLED_APP).staging"; echo "[!] Staged engine failed verification — /Applications untouched"; exit 1; }
	@# Deregister the outgoing bundle before replacing it. LaunchServices keeps
	@# every path it has ever seen, and a stale record can put a second "Say"
	@# row in the Privacy panes — so the switch the user flips is not the record
	@# tccd evaluates against the running process.
	@if [ -d "$(INSTALLED_APP)" ]; then \
		/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "$(INSTALLED_APP)" 2>/dev/null || true; \
		rm -rf "$(INSTALLED_APP).outgoing"; \
		mv "$(INSTALLED_APP)" "$(INSTALLED_APP).outgoing"; \
	fi
	@mv "$(INSTALLED_APP).staging" "$(INSTALLED_APP)"
	@rm -rf "$(INSTALLED_APP).outgoing"
	@# The staging bundle gets picked up by LaunchServices while it briefly
	@# exists, leaving a registration for a path that no longer resolves — the
	@# same duplicate-Privacy-row problem this target set out to avoid.
	@/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "$(INSTALLED_APP).staging" 2>/dev/null || true
	@echo "[+] Installed → $(INSTALLED_APP)"
	@/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$(INSTALLED_APP)" 2>/dev/null || true
	@echo "[OK] Installed. Shell + engine designated requirements:"
	@codesign -d -r- "$(INSTALLED_APP)" 2>&1 | grep -i designated | sed 's/^/    shell : /'
	@codesign -d -r- "$(INSTALLED_APP)/Contents/Resources/SayEngine.app" 2>&1 | grep -i designated | sed 's/^/    engine: /'
	@echo "    Launch it with: open -a Say"

clean:
	rm -rf node_modules dist src-tauri/target src-tauri/gen
