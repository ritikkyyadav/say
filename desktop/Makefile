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
ENGINE_APP    := $(ENGINE_DIST)/Say.app
TAURI_TARGET  := $(abspath ./src-tauri/target/release/bundle)
SHELL_APP     := $(TAURI_TARGET)/macos/Say.app
EMBED_TARGET  := $(SHELL_APP)/Contents/Resources/SayEngine.app
DMG_DIR       := $(TAURI_TARGET)/dmg

.PHONY: all engine ui-build app embed-engine dmg clean

all: engine ui-build app embed-engine

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
	rsync -aL "$(ENGINE_APP)/" "$(EMBED_TARGET)/"
	@echo "[OK] Embedded engine at $(EMBED_TARGET)"
	@du -sh "$(SHELL_APP)" || true

dmg:
	@if [ ! -d "$(SHELL_APP)" ]; then \
		echo "[!] Need $(SHELL_APP) — run 'make all' first"; exit 1; \
	fi
	@if ! command -v create-dmg >/dev/null 2>&1; then \
		echo "[!] create-dmg not installed. Install with: brew install create-dmg"; exit 1; \
	fi
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

clean:
	rm -rf node_modules dist src-tauri/target src-tauri/gen
