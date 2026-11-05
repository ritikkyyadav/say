package com.ritikyadav.say

import android.app.Application
import android.content.Context
import androidx.core.content.ContextCompat
import com.ritikyadav.say.engine.DictationController
import com.ritikyadav.say.engine.ModelManager
import com.ritikyadav.say.engine.MoonshineTranscriber
import com.ritikyadav.say.store.TranscriptStore

/**
 * Process-wide singletons. The keyboard service and the Home screen run in this
 * one process, so they share the engine, the store and the settings directly —
 * no HTTP handshake needed, unlike the Mac's two-process layout.
 */
class SayApp : Application() {
    lateinit var settings: Settings
        private set
    lateinit var store: TranscriptStore
        private set
    lateinit var models: ModelManager
        private set
    lateinit var transcriber: MoonshineTranscriber
        private set
    lateinit var engine: DictationController
        private set

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        store = TranscriptStore(this)
        models = ModelManager(this)
        transcriber = MoonshineTranscriber()
        engine = DictationController(this, settings, store, models, transcriber, ContextCompat.getMainExecutor(this))
    }

    companion object {
        fun of(context: Context): SayApp = context.applicationContext as SayApp
    }
}
