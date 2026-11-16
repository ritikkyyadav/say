// Top-level build file. Versions live in gradle/libs.versions.toml — the one
// place they are written, so the app module and CI cannot drift apart.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
}
