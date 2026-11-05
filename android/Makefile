# Say for Android — build, test, install, run on an emulator.
#
#   make models        download the bundled model into the APK's assets (once)
#   make test          JVM unit tests (the ported engine: postprocess, streaming, glyph)
#   make build         debug APK  → app/build/outputs/apk/debug/app-debug.apk
#   make release       release APK signed with the local key (make signing-key first)
#   make install       install the debug APK on the connected device/emulator and enable Say
#   make avd / emulator   create and boot an Android 16 arm64 emulator (headless)
#   make replay CLIP=path/to/16k-mono.wav   push a stored dictation through the engine on the device
#   make signing-key   mint keystore/say-local.jks so installs survive rebuilds
#
# Env: ANDROID_HOME (default ~/Library/Android/sdk), JAVA_HOME (default: Homebrew openjdk@17).

SHELL := /bin/zsh
ANDROID_HOME ?= $(HOME)/Library/Android/sdk
JAVA_HOME ?= $(shell [ -d /opt/homebrew/opt/openjdk@17 ] && echo /opt/homebrew/opt/openjdk@17 || /usr/libexec/java_home -v 17 2>/dev/null)
export ANDROID_HOME JAVA_HOME
ADB := $(ANDROID_HOME)/platform-tools/adb
BUILD_TOOLS := $(lastword $(sort $(wildcard $(ANDROID_HOME)/build-tools/*)))
APKSIGNER := $(BUILD_TOOLS)/apksigner
AAPT2 := $(BUILD_TOOLS)/aapt2
GRADLEW := ./gradlew --no-daemon -q

PKG := com.ritikyadav.say.android
IME := $(PKG)/com.ritikyadav.say.ime.SayInputMethodService

# The bundled model: Moonshine's smallest streaming English model, the same
# files the Mac's `moonshine_voice` fetches, minus the word-timestamp decoder.
MODEL ?= tiny-streaming-en
MODEL_URL := https://download.moonshine.ai/model/$(MODEL)/quantized
MODEL_DIR := app/src/main/assets/models/$(MODEL)
MODEL_FILES := adapter.ort cross_kv.ort decoder_kv.ort encoder.ort frontend.ort streaming_config.json tokenizer.bin

AVD ?= say36
IMAGE := system-images;android-36;google_apis;arm64-v8a

.PHONY: all models test build release release-small install uninstall avd emulator replay signing-key clean

all: models test build

# ── model ────────────────────────────────────────────────────────
models: $(addprefix $(MODEL_DIR)/,$(MODEL_FILES))

$(MODEL_DIR)/%:
	@mkdir -p $(MODEL_DIR)
	@echo "→ $(MODEL)/$*"
	@curl -fsSL -o "$@.partial" "$(MODEL_URL)/$*" && mv "$@.partial" "$@"

# ── build & test ─────────────────────────────────────────────────
test:
	$(GRADLEW) testDebugUnitTest

build: models
	$(GRADLEW) assembleDebug
	@ls -la app/build/outputs/apk/debug/app-debug.apk

# Every release build gets a fresh, strictly increasing versionCode (see
# app/build.gradle.kts): a phone's package installer refuses to reinstall the
# same code and says only "App not installed." Pin one with
# `make release SAY_VERSION_CODE=42` for a real release.
release: models
	@[ -f keystore/say-local.jks ] || { echo "no keystore/say-local.jks — run 'make signing-key' first, or this build gets the DEBUG key and will not install over an existing Say"; exit 1; }
	$(GRADLEW) assembleRelease $(if $(SAY_VERSION_CODE),-PsayVersionCode=$(SAY_VERSION_CODE),)
	@ls -la app/build/outputs/apk/release/app-release.apk
	@$(APKSIGNER) verify --print-certs app/build/outputs/apk/release/app-release.apk | grep "certificate DN" \
	  | grep -q "Say Local Signing" \
	  && echo "[OK] signed with the stable local identity — installs over the last build" \
	  || echo "[!!] NOT signed with 'Say Local Signing': this will NOT install over an existing Say"
	@$(AAPT2) dump badging app/build/outputs/apk/release/app-release.apk | grep -oE "versionCode='[0-9]*' versionName='[^']*'"
	@mkdir -p dist && cp app/build/outputs/apk/release/app-release.apk dist/Say.apk && ls -l dist/Say.apk

# The same app with the 51 MB model left out: 29 MiB instead of 61, fetched on
# first launch instead. For a phone whose package installer will not take the
# big one, and for any transfer with a size limit.
release-small:
	@[ -f keystore/say-local.jks ] || { echo "no keystore/say-local.jks — run 'make signing-key' first"; exit 1; }
	$(GRADLEW) assembleRelease -PsayBundleModel=false $(if $(SAY_VERSION_CODE),-PsayVersionCode=$(SAY_VERSION_CODE),)
	@mkdir -p dist && cp app/build/outputs/apk/release/app-release.apk dist/Say-nomodel.apk
	@$(AAPT2) dump badging dist/Say-nomodel.apk | grep -oE "versionCode='[0-9]*' versionName='[^']*'"
	@ls -l dist/Say-nomodel.apk

# ── device ───────────────────────────────────────────────────────
install: build
	$(ADB) install -r app/build/outputs/apk/debug/app-debug.apk
	$(ADB) shell pm grant $(PKG) android.permission.RECORD_AUDIO
	$(ADB) shell ime enable $(IME)
	@echo "Say is installed and enabled. Pick it from your keyboard's mic key or the switcher."

uninstall:
	$(ADB) uninstall $(PKG)

# Push a 16 kHz mono WAV into the app's sandbox and replay it through the
# whole pipeline (recorder → segmenter → Moonshine → postprocess → store),
# logging the transcript. Debug builds only. The Android replay_bench.
replay:
	@[ -n "$(CLIP)" ] || { echo "usage: make replay CLIP=/path/to/clip.wav"; exit 1; }
	$(ADB) exec-in "run-as $(PKG) sh -c 'cat > files/replay.wav'" < "$(CLIP)"
	$(ADB) logcat -c
	$(ADB) shell am broadcast -n $(PKG)/com.ritikyadav.say.debug.ReplayReceiver -a com.ritikyadav.say.DEBUG_REPLAY \
	    --es path /data/data/$(PKG)/files/replay.wav --es mode engine >/dev/null
	@for i in $$(seq 1 90); do sleep 1; $(ADB) logcat -d -s Say.Debug:I | grep -q TRANSCRIPT && break; done
	$(ADB) logcat -d -s Say.Debug:I Say.Transcriber:I | tail -12

# ── emulator ─────────────────────────────────────────────────────
avd:
	yes | sdkmanager --licenses >/dev/null 2>&1 || true
	sdkmanager "platform-tools" "emulator" "$(IMAGE)"
	echo no | avdmanager create avd -n $(AVD) -k "$(IMAGE)" -d pixel_7 --force
	@sed -i '' 's/^hw.ramSize=.*/hw.ramSize=4096/' $(HOME)/.android/avd/$(AVD).avd/config.ini

emulator:
	nohup $(ANDROID_HOME)/emulator/emulator -avd $(AVD) -no-window -no-boot-anim -no-snapshot \
	    -gpu swiftshader_indirect -memory 4096 > /tmp/say-emulator.log 2>&1 &
	@echo "booting $(AVD) headless; wait for: $(ADB) shell getprop sys.boot_completed"

# ── signing ──────────────────────────────────────────────────────
# A stable local key: Android identifies an installed app by its signing
# certificate the way macOS TCC identifies it by code signature, so every
# grant belongs to the key. Not a secret — its stability is the point.
signing-key:
	@mkdir -p keystore
	@[ -f keystore/say-local.jks ] && echo "keystore/say-local.jks exists" || \
	  $(JAVA_HOME)/bin/keytool -genkeypair -v -keystore keystore/say-local.jks -alias say-local \
	    -keyalg RSA -keysize 2048 -validity 10000 -storepass say-local -keypass say-local \
	    -dname "CN=Say Local Signing, O=Say, C=US"

clean:
	rm -rf app/build build .gradle .kotlin
