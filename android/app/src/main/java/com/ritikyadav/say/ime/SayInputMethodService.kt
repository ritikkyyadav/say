package com.ritikyadav.say.ime

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.inputmethodservice.InputMethodService
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.ContextThemeWrapper
import android.view.LayoutInflater
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.ImageButton
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.ritikyadav.say.R
import com.ritikyadav.say.SayApp
import com.ritikyadav.say.engine.Config
import com.ritikyadav.say.engine.DictationController
import com.ritikyadav.say.engine.DictationController.State
import com.ritikyadav.say.engine.Result
import com.ritikyadav.say.engine.Target
import com.ritikyadav.say.engine.focusPolicy
import com.ritikyadav.say.glyph.GlyphView
import com.ritikyadav.say.store.TranscriptStore
import com.ritikyadav.say.ui.MicPermissionActivity

/**
 * Say as an Android keyboard.
 *
 * The Mac's gesture is hold Fn, speak, release. Here it is: your keyboard's mic
 * key (or the switcher) brings this panel up, listening starts at once, you
 * speak, you tap the mark or the tick. The text is committed into the field
 * you were in and the previous keyboard comes back. That is the whole surface;
 * there are no keys on it because the user already has a keyboard.
 *
 * The seam with the engine is the same shape as the Mac's `gestures.py`: the
 * panel never opens the mic or shows the listening glyph unless the controller
 * has agreed to record, and every engine event routes to the glyph and the
 * status line through one place.
 */
class SayInputMethodService : InputMethodService() {
    private lateinit var app: SayApp
    private val engine: DictationController get() = app.engine
    private val handler = Handler(Looper.getMainLooper())

    private var glyph: GlyphView? = null
    private var status: TextView? = null
    private var allow: Button? = null
    private var done: ImageButton? = null
    private var cancel: ImageButton? = null

    /** Where the current field is; captured on focus, compared again when text lands. */
    private var target: Target? = null

    /** A field was just focused and should start listening as soon as the engine is ready. */
    private var wantsStart = false

    /** The user asked for their keyboard back; honour it once the words have landed. */
    private var switchAfterCommit = false

    private val onEvent: (DictationController.Event) -> Unit = { render(it) }
    private val sink: (Result) -> Unit = { deliver(it) }
    private var clearNotice: Runnable? = null

    override fun onCreate() {
        super.onCreate()
        app = SayApp.of(this)
        engine.addListener(onEvent)
        // The service exists as soon as Say is enabled: load now, so the first
        // field the user focuses is not also the first model load.
        engine.ensureModelLoaded()
    }

    override fun onDestroy() {
        engine.removeListener(onEvent)
        super.onDestroy()
    }

    override fun onCreateInputView(): View {
        // An IME window carries the platform's InputMethod theme, not the app's:
        // inflated bare, every ?attr and Material style in the panel fails to
        // resolve and the service crashes on first show. Wrap the app theme in.
        val themed = LayoutInflater.from(ContextThemeWrapper(this, R.style.Theme_Say))
        val view = themed.inflate(R.layout.ime_panel, null)
        glyph = view.findViewById<GlyphView>(R.id.glyph).also {
            it.analyzer = engine.analyzer
            it.setOnClickListener { onDoneTapped() }
        }
        status = view.findViewById(R.id.status)
        allow = view.findViewById<Button>(R.id.btn_allow).also { b ->
            b.setOnClickListener {
                startActivity(Intent(this, MicPermissionActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
        }
        done = view.findViewById<ImageButton>(R.id.btn_done).also { it.setOnClickListener { onDoneTapped() } }
        cancel = view.findViewById<ImageButton>(R.id.btn_cancel).also { it.setOnClickListener { onCancelTapped() } }
        view.findViewById<ImageButton>(R.id.btn_keyboard).setOnClickListener { onKeyboardTapped() }
        render(engine.lastEvent)
        return view
    }

    /** Never take the whole screen in landscape: the panel is small on purpose. */
    override fun onEvaluateFullscreenMode(): Boolean = false

    override fun onStartInputView(info: EditorInfo, restarting: Boolean) {
        super.onStartInputView(info, restarting)
        target = Target(info.packageName, info.fieldId, info.hintText?.toString())
        Log.i(TAG, "field focused in ${info.packageName} (#${info.fieldId}); engine ${engine.state}")
        switchAfterCommit = false
        wantsStart = true
        begin()
    }

    override fun onWindowShown() {
        super.onWindowShown()
        if (wantsStart) begin()
    }

    override fun onFinishInputView(finishingInput: Boolean) {
        // The field went away under a live recording: keep the words. If focus
        // has moved by the time they are ready, the focus policy holds them.
        if (engine.state == State.RECORDING) engine.stopRecording()
        wantsStart = false
        glyph?.mode = GlyphView.Mode.HIDDEN
        super.onFinishInputView(finishingInput)
    }

    // ── flow ─────────────────────────────────────────────────────

    private fun hasMic(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun begin() {
        if (!hasMic()) {
            showMicState()
            return
        }
        allow?.visibility = View.GONE
        glyph?.visibility = View.VISIBLE
        engine.ensureModelLoaded()
        tryStart()
    }

    /** Start only if the controller agrees — the glyph goes live first, and retracts if refused. */
    private fun tryStart() {
        val t = target ?: return
        if (!engine.canStartRecording()) return
        glyph?.mode = GlyphView.Mode.LISTEN
        if (engine.startRecording(t, sink)) {
            wantsStart = false
        } else {
            glyph?.mode = GlyphView.Mode.REST
        }
    }

    private fun showMicState() {
        glyph?.mode = GlyphView.Mode.HIDDEN
        glyph?.visibility = View.INVISIBLE
        allow?.visibility = View.VISIBLE
        status?.text = getString(R.string.panel_no_mic)
    }

    private fun onDoneTapped() {
        when (engine.state) {
            State.RECORDING -> engine.stopRecording()
            State.READY, State.ERROR -> { wantsStart = true; begin() }
            else -> Unit
        }
    }

    private fun onCancelTapped() {
        wantsStart = false
        if (engine.state == State.RECORDING) engine.cancelRecording()
        switchBack()
    }

    private fun onKeyboardTapped() {
        wantsStart = false
        if (engine.state == State.RECORDING) {
            switchAfterCommit = true
            engine.stopRecording()
        } else {
            switchBack()
        }
    }

    private fun switchBack() {
        glyph?.mode = GlyphView.Mode.HIDDEN
        // "Previous" is only recorded for switches the user made; when there is
        // none, the next enabled keyboard is still what they meant by "back".
        if (!switchToPreviousInputMethod() && !switchToNextInputMethod(false)) {
            // Say is the only keyboard: just close.
            requestHideSelf(0)
        }
    }

    // ── engine → panel ───────────────────────────────────────────

    private fun render(event: DictationController.Event) {
        val g = glyph ?: return
        val s = status ?: return
        clearNotice?.let { handler.removeCallbacks(it) }
        when (event.state) {
            State.IDLE, State.LOADING -> {
                g.mode = GlyphView.Mode.REST
                val p = event.progress
                s.text = if (p != null) getString(R.string.status_downloading, p.first)
                else getString(R.string.panel_loading)
            }
            State.READY -> {
                g.mode = GlyphView.Mode.REST
                val notice = when (event.message) {
                    DictationController.Messages.NOTHING_HEARD, DictationController.Messages.TOO_SHORT ->
                        getString(R.string.panel_nothing_heard)
                    DictationController.Messages.HELD -> getString(R.string.panel_held)
                    else -> null
                }
                if (notice != null) {
                    s.text = notice
                    val r = Runnable { if (engine.state == State.READY) s.text = getString(R.string.panel_ready) }
                    clearNotice = r
                    handler.postDelayed(r, Config.PANEL_NOTICE_MS)
                } else {
                    s.text = getString(R.string.panel_ready)
                }
                if (event.message == DictationController.Messages.HELD && switchAfterCommit) switchBack()
                if (wantsStart && hasMic()) tryStart()
            }
            State.RECORDING -> {
                g.mode = GlyphView.Mode.LISTEN
                s.text = getString(R.string.panel_listening)
            }
            State.PROCESSING -> {
                g.mode = GlyphView.Mode.WORK
                val p = event.progress
                s.text = if (p != null && p.second > 1) getString(R.string.panel_progress, p.first, p.second)
                else getString(R.string.panel_processing)
            }
            State.ERROR -> {
                g.mode = GlyphView.Mode.REST
                s.text = event.error ?: getString(R.string.panel_error)
            }
        }
    }

    /** The finished dictation. Main thread. */
    private fun deliver(result: Result) {
        val now = currentInputEditorInfo?.let { Target(it.packageName, it.fieldId) }
        val policy = focusPolicy(result.target.key, now?.key)
        Log.i(TAG, "delivering ${TranscriptStore.wordCount(result.text)} words: policy=$policy (began ${result.target.key}, now ${now?.key})")
        if (policy == "hold") {
            engine.hold(result)
            return
        }
        val ic = currentInputConnection
        if (ic == null) {
            engine.hold(result)
            return
        }
        // Butt the words up against what is already there, with one space if
        // the cursor is flush against a word.
        val before = ic.getTextBeforeCursor(1, 0)
        val prefix = if (before.isNullOrEmpty() || before.last().isWhitespace()) "" else " "
        val committed = ic.commitText(prefix + result.text, 1)
        if (!committed) {
            engine.hold(result)
            return
        }
        engine.reportOutcome(result, true, "ime")
        if (app.settings.returnToKeyboard || switchAfterCommit) {
            switchAfterCommit = false
            // A beat so the words are seen landing before the keyboard swaps.
            handler.postDelayed({ switchBack() }, 220)
        }
    }

    private companion object { const val TAG = "Say.IME" }
}
