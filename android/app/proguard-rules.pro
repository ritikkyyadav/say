# The IME service and the Moonshine JNI bridge are found by name at runtime.
# Minification is off in build.gradle.kts, so these are belt-and-braces for
# anyone who turns it on.
-keep class ai.moonshine.voice.** { *; }
-keep class com.ritikyadav.say.ime.SayInputMethodService { *; }
