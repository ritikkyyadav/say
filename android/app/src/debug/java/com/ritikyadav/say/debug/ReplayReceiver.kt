package com.ritikyadav.say.debug

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.ritikyadav.say.SayApp
import com.ritikyadav.say.engine.DictationController
import com.ritikyadav.say.engine.Recorder
import com.ritikyadav.say.engine.Target
import java.io.File

/**
 * `adb shell am broadcast -n <pkg>/com.ritikyadav.say.debug.ReplayReceiver \
 *      -a com.ritikyadav.say.DEBUG_REPLAY --es path <wav> [--es mode engine|ime] [--ez realtime true]`
 *
 * Installs a WAV as the next recording's audio source. `mode engine` also
 * drives a dictation through the controller right away and logs the
 * transcript under the tag Say.Debug; `mode ime` (the default) leaves the
 * hook armed for the keyboard, so focusing a field replays the clip through
 * the real commit path. No `path` clears the hook.
 */
class ReplayReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // `--es model <id>` switches the model in use first (a debug shortcut for
        // what the Models list does), so a pushed-in download can be exercised.
        intent.getStringExtra("model")?.let { id ->
            val app = SayApp.of(context)
            app.settings.model = id
            app.engine.reloadModel()
            Log.i(TAG, "model set to $id")
        }
        val path = intent.getStringExtra("path")
        if (path == null) {
            Recorder.debugSourceFactory = null
            Log.i(TAG, "replay hook cleared")
            return
        }
        val realtime = intent.getBooleanExtra("realtime", false)
        val file = File(path)
        Recorder.debugSourceFactory = { WavFileSource(file, realtime) }
        Log.i(TAG, "replay armed: $path realtime=$realtime")
        if (intent.getStringExtra("mode") != "engine") return

        val engine = SayApp.of(context).engine
        val sink: (com.ritikyadav.say.engine.Result) -> Unit = { r ->
            Log.i(TAG, "TRANSCRIPT[${r.rowId}] %.1fs/%.2fs: ${r.text}".format(r.audioSeconds, r.inferenceSeconds))
            engine.reportOutcome(r, true, "debug")
            Recorder.debugSourceFactory = null
        }
        engine.addListener { e -> Log.i(TAG, "EVENT ${e.state}${e.message?.let { " $it" } ?: ""}${e.error?.let { " $it" } ?: ""}${e.progress?.let { " ${it.first}/${it.second}" } ?: ""}") }
        var started = false
        val starter = object : (DictationController.Event) -> Unit {
            override fun invoke(e: DictationController.Event) {
                if (e.state == DictationController.State.READY && !started) {
                    started = true
                    engine.removeListener(this)
                    engine.startRecording(Target("debug", 0), sink)
                }
            }
        }
        engine.addListener(starter)
        engine.ensureModelLoaded()
        if (engine.canStartRecording()) starter(DictationController.Event(DictationController.State.READY))
    }

    private companion object { const val TAG = "Say.Debug" }
}
