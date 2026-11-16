package com.ritikyadav.say.engine

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.util.Log
import com.ritikyadav.say.Settings
import com.ritikyadav.say.glyph.BandAnalyzer
import com.ritikyadav.say.store.TranscriptStore
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executor
import java.util.concurrent.Executors

/** Where a recording began. Text is committed only if focus is still there when it lands. */
data class Target(val packageName: String?, val fieldId: Int, val hint: String? = null) {
    /** What the focus policy compares. A field id of 0 is "unknown", so only the app counts then. */
    val key: String? get() = packageName?.let { if (fieldId != 0) "$it#$fieldId" else it }
}

/**
 * Should the transcript be typed, given where the recording began and where
 * focus is now? The keystrokes go wherever focus is at the instant
 * transcription ends — and typing a dictation meant for one field into another
 * is the worst thing this app can do with a correct transcript. Unknown on
 * either side means inject: no field id is not evidence that focus moved.
 */
fun focusPolicy(startedIn: String?, nowIn: String?): String {
    if (startedIn.isNullOrEmpty() || nowIn.isNullOrEmpty()) return "inject"
    return if (startedIn == nowIn) "inject" else "hold"
}

/** One finished dictation, persisted, waiting to be committed. */
class Result(
    val rowId: Long,
    val text: String,
    val target: Target,
    val audioSeconds: Double,
    val inferenceSeconds: Double,
)

/**
 * The state machine coordinating record → segment → decode → postprocess →
 * persist → hand over. Mirrors the Mac's `controller.py`:
 *
 *   IDLE → LOADING → READY → RECORDING → PROCESSING → READY, any stage → ERROR.
 *
 * The transcript is persisted BEFORE it is committed, so a failed commit can
 * never lose the user's words; the sink reports the outcome afterwards. A
 * recording nobody spoke into is refused rather than decoded (the loop gotcha),
 * and an incomplete streamed result is re-decoded whole rather than returned
 * with words missing.
 */
class DictationController(
    context: Context,
    private val settings: Settings,
    private val store: TranscriptStore,
    private val models: ModelManager,
    private val transcriber: MoonshineTranscriber,
    private val mainExecutor: Executor,
    private val recorderFactory: () -> Recorder = { Recorder { Recorder.debugSourceFactory?.invoke() ?: MicSource() } },
) {
    enum class State { IDLE, LOADING, READY, RECORDING, PROCESSING, ERROR }

    data class Event(
        val state: State,
        val message: String? = null,
        val progress: Pair<Int, Int>? = null,
        val error: String? = null,
    )

    /** Outcome the panel shows for a beat, carried in Event.message. */
    object Messages {
        const val NOTHING_HEARD = "nothing_heard"
        const val HELD = "held"
        const val TOO_SHORT = "too_short"
    }

    private val app = context.applicationContext
    private val worker = Executors.newSingleThreadExecutor { Thread(it, "say-engine") }
    private val listeners = CopyOnWriteArrayList<(Event) -> Unit>()
    private val lock = Any()

    /** Live spectrum + voice gate, fed from the capture thread. The glyph reads it every frame. */
    val analyzer = BandAnalyzer()

    @Volatile var state: State = State.IDLE
        private set

    @Volatile var lastEvent: Event = Event(State.IDLE)
        private set

    private var recorder: Recorder? = null
    private var session: SegmentedSession? = null
    private var target: Target? = null
    private var sink: ((Result) -> Unit)? = null
    private var loading = false

    /** The model the transcriber has loaded, for the UI. */
    val loadedModel: String? get() = transcriber.modelId

    // End-of-speech bookkeeping, capture thread only.
    private var trailingSilence = 0.0
    private var spokeYet = false
    private var totalSeconds = 0.0

    fun addListener(l: (Event) -> Unit) { listeners.add(l) }
    fun removeListener(l: (Event) -> Unit) { listeners.remove(l) }

    private fun emit(event: Event) {
        state = event.state
        lastEvent = event
        mainExecutor.execute { for (l in listeners) runCatching { l(event) } }
    }

    // ── model ────────────────────────────────────────────────────

    /** Load the configured model if it is not already loaded. Idempotent, asynchronous. */
    fun ensureModelLoaded() {
        val wanted = settings.model
        synchronized(lock) {
            if (loading) return
            if (transcriber.isLoaded && transcriber.modelId == wanted) {
                if (state == State.IDLE) emit(Event(State.READY))
                return
            }
            if (state == State.RECORDING || state == State.PROCESSING) return
            loading = true
        }
        emit(Event(State.LOADING))
        worker.execute {
            try {
                var info = ModelCatalog.get(wanted)
                if (info == null || (!info.bundled && !models.isInstalled(info.id))) {
                    // A model that is gone falls back to the built-in one, and the
                    // setting follows so the Models list tells the truth.
                    info = ModelCatalog.bundled()
                    settings.model = info.id
                }
                // Installed, or copied out of the APK, or fetched now — the
                // last of which is first launch on a build with no bundled
                // model. The percent rides in the LOADING event so the wait is
                // a number rather than a spinner.
                val dir = models.ensureAvailable(info) { percent ->
                    emit(Event(State.LOADING, progress = percent to 100))
                }
                transcriber.load(info.id, dir, info.arch)
                transcriber.setKeyterms(Postprocess.keyterms(settings.postprocessOptions()))
                emit(Event(State.READY))
            } catch (e: Throwable) {
                Log.e(TAG, "model load failed", e)
                transcriber.close()
                emit(Event(State.ERROR, error = e.message ?: "model failed to load"))
            } finally {
                synchronized(lock) { loading = false }
            }
        }
    }

    /** Swap to another installed model, live. */
    fun reloadModel() {
        synchronized(lock) {
            if (state == State.RECORDING || state == State.PROCESSING) return
        }
        transcriber.close()
        ensureModelLoaded()
    }

    /** Vocabulary changed on the Home screen: re-bias the decoder without a reload. */
    fun refreshKeyterms() {
        worker.execute { transcriber.setKeyterms(Postprocess.keyterms(settings.postprocessOptions())) }
    }

    // ── recording ────────────────────────────────────────────────

    /** The acceptance rule, shared with [startRecording] so the two cannot drift. */
    fun canStartRecording(): Boolean =
        transcriber.isLoaded && (state == State.READY || state == State.ERROR)

    /**
     * Open the mic and start a dictation. `sink` receives the finished
     * transcript on the main thread — the keyboard commits it, the Home screen
     * types it into its own field. False if refused or the mic would not open.
     */
    fun startRecording(target: Target, sink: (Result) -> Unit): Boolean {
        synchronized(lock) {
            if (!canStartRecording()) {
                Log.i(TAG, "start refused in $state")
                return false
            }
            val rec = recorderFactory()
            val sess = SegmentedSession(transcriber) { done, total ->
                if (state == State.PROCESSING) emit(Event(State.PROCESSING, progress = done to total))
            }
            trailingSilence = 0.0
            spokeYet = false
            totalSeconds = 0.0
            analyzer.rearm()
            rec.monitor = { block, n -> onBlock(block, n) }
            rec.segmentCallback = { segment -> sess.push(segment) }
            rec.onMaxReached = { mainExecutor.execute { stopRecording() } }
            rec.onSourceEnded = { mainExecutor.execute { stopRecording() } }
            if (!rec.start()) {
                sess.cancel()
                emit(Event(State.ERROR, error = "microphone unavailable"))
                return false
            }
            recorder = rec
            session = sess
            this.target = target
            this.sink = sink
            emit(Event(State.RECORDING))
            return true
        }
    }

    /** Capture thread: feed the glyph, then ask the end-of-speech rule. */
    private fun onBlock(block: FloatArray, n: Int) {
        analyzer.feed(block, n)
        val dt = n.toDouble() / Config.SAMPLE_RATE
        totalSeconds += dt
        if (analyzer.snapshot().gate > 0.5) {
            spokeYet = true
            trailingSilence = 0.0
        } else {
            trailingSilence += dt
        }
        if (state == State.RECORDING && EndOfSpeech.shouldFinish(trailingSilence, spokeYet, totalSeconds)) {
            // stop() joins the capture thread, so it must not run on it.
            mainExecutor.execute { stopRecording() }
        }
    }

    /** Stop the mic and transcribe. The result reaches [sink] on the main thread. */
    fun stopRecording() {
        val rec: Recorder
        val sess: SegmentedSession
        val tgt: Target
        val s: ((Result) -> Unit)?
        synchronized(lock) {
            if (state != State.RECORDING) return
            rec = recorder ?: return
            sess = session ?: return
            tgt = target ?: Target(null, 0)
            s = sink
            recorder = null
            session = null
            sink = null
            emit(Event(State.PROCESSING))
        }
        worker.execute { process(rec, sess, tgt, s) }
    }

    private fun process(rec: Recorder, sess: SegmentedSession, tgt: Target, s: ((Result) -> Unit)?) {
        val t0 = System.nanoTime()
        try {
            val capture = rec.stop()
            val audioSeconds = capture.full.size.toDouble() / Config.SAMPLE_RATE
            if (capture.failure != null && capture.liveSeconds < Config.MIN_LIVE_SECONDS) {
                // The device failed before it delivered anything. Say so —
                // "didn't catch that" would send the user off to speak louder.
                sess.cancel()
                emit(Event(State.ERROR, error = capture.failure))
                return
            }
            if (capture.liveSeconds < Config.MIN_LIVE_SECONDS || audioSeconds < Config.MIN_AUDIO_SECONDS) {
                sess.cancel()
                emit(Event(State.READY, message = Messages.TOO_SHORT))
                return
            }
            if (MoonshineTranscriber.isSilent(capture.full)) {
                sess.cancel()
                emit(Event(State.READY, message = Messages.NOTHING_HEARD))
                return
            }
            var text = sess.finish(capture.tail)
            if (!sess.complete) {
                // A partial transcript is worse than a slow one: decode the whole clip.
                Log.w(TAG, "streamed result incomplete; re-decoding the whole clip")
                text = transcriber.transcribe(capture.full)
            } else if (text.isEmpty() && sess.attempted == 0) {
                text = transcriber.transcribe(capture.full)
            }
            text = Postprocess.apply(text, settings.postprocessOptions())
            val inference = (System.nanoTime() - t0) / 1e9
            if (text.isBlank()) {
                emit(Event(State.READY, message = Messages.NOTHING_HEARD))
                return
            }
            // Persist first. Whatever the commit does next, the words are safe.
            val rowId = store.insert(
                text = text,
                audioSeconds = audioSeconds,
                inferenceSeconds = inference,
                model = transcriber.modelId ?: settings.model,
                injectMethod = "pending",
                targetApp = tgt.packageName,
                success = false,
            )
            val result = Result(rowId, text, tgt, audioSeconds, inference)
            Log.i(TAG, "transcribed %.1fs in %.2fs: %d words".format(audioSeconds, inference, TranscriptStore.wordCount(text)))
            if (s == null) {
                hold(result)
            } else {
                mainExecutor.execute {
                    try {
                        s(result)
                    } catch (e: Throwable) {
                        Log.e(TAG, "sink failed; holding", e)
                        hold(result)
                    }
                }
                emit(Event(State.READY))
            }
        } catch (e: Throwable) {
            Log.e(TAG, "processing failed", e)
            sess.cancel()
            emit(Event(State.ERROR, error = e.message ?: "transcription failed"))
        }
    }

    /** Throw the clip away. */
    fun cancelRecording() {
        val rec: Recorder?
        val sess: SegmentedSession?
        synchronized(lock) {
            if (state != State.RECORDING) return
            rec = recorder
            sess = session
            recorder = null
            session = null
            sink = null
        }
        sess?.cancel()
        worker.execute {
            rec?.cancel()
            emit(Event(State.READY))
        }
    }

    // ── outcomes ─────────────────────────────────────────────────

    /** The sink committed (or failed to commit) the text. */
    fun reportOutcome(result: Result, success: Boolean, method: String) {
        worker.execute { store.setOutcome(result.rowId, success, method) }
    }

    /**
     * Focus moved (or the field vanished) before the text could land: keep the
     * words on the clipboard and in history rather than typing them somewhere
     * they were never meant to go.
     */
    fun hold(result: Result) {
        runCatching {
            val cm = app.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("Say", result.text))
        }
        reportOutcome(result, false, "clipboard")
        emit(Event(State.READY, message = Messages.HELD))
    }

    companion object {
        private const val TAG = "Say.Engine"
    }
}
