import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

// A stable local signing key, minted by `make signing-key`. Android keys an
// installed app's identity on its signing certificate exactly the way macOS
// TCC keys a grant on the code signature: a release APK signed with a
// different key cannot be installed over the previous one, and every grant
// (microphone, "enabled keyboard") belongs to the install. Without this file
// release builds fall back to the debug key, which at least is stable per
// machine.
val localKeystore = rootProject.file("keystore/say-local.jks")
if (!localKeystore.exists()) {
    // The Mac's lesson, repeated: a build that silently signs with a different
    // key than the last one produces an install failure with no reason given.
    logger.warn("say: keystore/say-local.jks is missing — release builds will be signed with the DEBUG key and will NOT install over an existing Say. Run `make signing-key`.")
}

// Sideloaded updates: a phone's package installer refuses to *reinstall* the
// same versionCode and reports it as a bare "App not installed." with no
// reason (a OnePlus did exactly this on 2026-09-07, over an install of the
// same 0.1.0/1 build that had worked an hour earlier). So every local build
// gets a strictly increasing code — minutes since 2026-01-01 — and a real
// release pins one with `-PsayVersionCode=N`. It stays well inside Play's
// 2,100,000,000 ceiling for the next few thousand years.
val buildVersionCode = (project.findProperty("sayVersionCode") as String?)?.toInt()
    ?: ((System.currentTimeMillis() - 1_767_225_600_000L) / 60_000L).toInt()

// `-PsayBundleModel=false` leaves the 51 MB model out of the APK (13 MB rather
// than 64 MB) and fetches it on first launch instead. The default keeps the
// promise that a fresh install works with no network; the flag exists because
// a phone that will not accept a large sideloaded APK is worth more than that
// promise on that phone.
val bundleModel = (project.findProperty("sayBundleModel") as String?)?.toBoolean() ?: true

android {
    namespace = "com.ritikyadav.say"
    compileSdk = 36

    defaultConfig {
        // Same namespace as the Mac bundles (com.ritikyadav.say.dictation /
        // .desktop) — see CLAUDE.md for why it is not com.say.*.
        applicationId = "com.ritikyadav.say.android"
        // 28 is where InputMethodService.switchToPreviousInputMethod() appears,
        // which is how the panel hands the field back to the user's keyboard.
        minSdk = 28
        targetSdk = 36
        buildConfigField("boolean", "BUNDLED_MODEL", bundleModel.toString())
        versionCode = buildVersionCode
        // The build code rides along in the version name so "which build is on
        // this phone?" is answerable from Settings → Apps → Say.
        versionName = "0.1.1 ($buildVersionCode)"

        // The Moonshine AAR carries arm64-v8a, armeabi-v7a and x86_64. Every
        // phone that can run this is arm64; the others only add ~17 MB each.
        // Override for an x86_64 emulator with -PsayAbis=arm64-v8a,x86_64.
        ndk {
            abiFilters += (project.findProperty("sayAbis") as String? ?: "arm64-v8a")
                .split(",").map { it.trim() }.filter { it.isNotEmpty() }
        }
    }

    signingConfigs {
        if (localKeystore.exists()) {
            create("local") {
                storeFile = localKeystore
                storePassword = "say-local"
                keyAlias = "say-local"
                keyPassword = "say-local"
                // minSdk is 28, so v1 (JAR) signing buys nothing; v3 is what
                // current Android prefers and what key rotation would need.
                enableV1Signing = false
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        release {
            // Off on purpose: the IME is bound by class name and the Moonshine
            // bridge by JNI symbol, and there is nothing here worth obfuscating.
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("local") ?: signingConfigs.getByName("debug")
        }
    }

    buildFeatures {
        buildConfig = true
    }

    if (!bundleModel) {
        sourceSets.getByName("main").assets.setSrcDirs(emptyList<String>())
    }

    // The model files are quantized weights and still compress by 35% (51 MB →
    // 33 MB), which is 18 MB off an APK that has to be moved onto a phone by
    // hand. They are read exactly once, on first launch, while the UI already
    // says "Loading the model…", so paying an inflate there and never again is
    // the right side of that trade. (Hence no `noCompress` for ort/bin.)

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.moonshine.voice) {
        // The AAR drags in WorkManager (and Room behind it) for its own
        // background model downloader, which this app does not use — it has
        // its own resumable downloader in ModelManager. Left in, it merges
        // WAKE_LOCK, RECEIVE_BOOT_COMPLETED and FOREGROUND_SERVICE into the
        // manifest: three permissions Say does not use, on a dictation app
        // whose whole claim is that it does nothing behind your back.
        exclude(group = "androidx.work")
    }
    implementation(libs.appcompat)
    implementation(libs.material)
    testImplementation(libs.junit)
}
