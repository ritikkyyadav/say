package com.ritikyadav.say.overlay

import android.accessibilityservice.AccessibilityService
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import android.widget.Toast
import com.ritikyadav.say.R
import com.ritikyadav.say.SayApp
import com.ritikyadav.say.engine.DictationController
import com.ritikyadav.say.engine.DictationController.State
import com.ritikyadav.say.engine.Result
import com.ritikyadav.say.engine.Target
import com.ritikyadav.say.engine.focusPolicy

/**
 * Say anywhere: the floating glyph over other apps.
 *
 * This is the Mac's overlay + injector on Android. An accessibility service
 * is the only thing on Android that may place text into another app's field
 * without being its keyboard, and it may also hang a window over every app
 * without the "draw over other apps" permission. So this service does both:
 * it hosts the [Bubble], and when a dictation finishes it puts the words at
 * the cursor of the field that has input focus.
 *
 * The privacy contract is structural, like the Mac read-back's: the only node
 * this service ever reads is the one with input focus, only at the moment a
 * transcript lands in it, and only to splice the words in at the cursor.
 * Focus and window events are used to decide whether to show the bubble; no
 * text is read for that. Nothing is logged, stored or sent.
 */
class SayAccessibilityService : AccessibilityService() {
    private lateinit var app: SayApp
    private val engine: DictationController get() = app.engine
    private val handler = Handler(Looper.getMainLooper())
    private var bubble: Bubble? = null
    private var target: Target? = null
    private var lastKeyboardTop = -1
    private var lastFieldTop = -1

    private val onEvent: (DictationController.Event) -> Unit = { render(it) }
    private val sink: (Result) -> Unit = { deliver(it) }

    override fun onServiceConnected() {
        super.onServiceConnected()
        app = SayApp.of(this)
        instance = this
        bubble = Bubble(this, onTap = { onBubbleTapped() }, onLongPress = { onBubbleHeld() })
        engine.addListener(onEvent)
        engine.ensureModelLoaded()
        refreshVisibility()
        Log.i(TAG, "connected; bubble hosted")
    }

    override fun onDestroy() {
        engine.removeListener(onEvent)
        bubble?.destroy()
        bubble = null
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        refreshVisibility()
    }

    override fun onInterrupt() = Unit

    // ── when to show the bubble ──────────────────────────────────

    /**
     * Visible while a keyboard is up in some other app, or a text field has
     * input focus — the moments a person could be about to type — and always
     * while a dictation is in flight. Hidden when Say's own keyboard is the one
     * up, since its panel already carries the glyph.
     */
    private fun refreshVisibility() {
        val b = bubble ?: return
        val busy = engine.state == State.RECORDING || engine.state == State.PROCESSING
        val keyboard = windows.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
        val sayIsKeyboard = keyboard != null && currentImeIsSay()
        // Only the focused field's *bounds* are read here, never its text —
        // enough to keep the mark off it, and nothing more.
        var fieldTop = -1
        val fieldFocused = focusedEditable()?.recycleAfter { node ->
            val r = android.graphics.Rect()
            node.getBoundsInScreen(r)
            if (!r.isEmpty) fieldTop = r.top
            true
        } ?: false
        val show = busy || (!sayIsKeyboard && (keyboard != null || fieldFocused))
        if (com.ritikyadav.say.BuildConfig.DEBUG) {
            Log.d(TAG, "visibility: keyboard=${keyboard != null} sayIsKeyboard=$sayIsKeyboard field=$fieldFocused busy=$busy -> $show (windows=${windows.size})")
        }
        if (keyboard != null && !sayIsKeyboard) {
            val bounds = android.graphics.Rect()
            keyboard.getBoundsInScreen(bounds)
            if (bounds.top != lastKeyboardTop || fieldTop != lastFieldTop) {
                lastKeyboardTop = bounds.top
                lastFieldTop = fieldTop
                b.anchorAbove(bounds.top, fieldTop)
            }
        } else if (keyboard == null && (lastKeyboardTop != -1 || fieldTop != lastFieldTop)) {
            lastKeyboardTop = -1
            lastFieldTop = fieldTop
            b.anchorAbove(-1, fieldTop)
        }
        b.visible = show
    }

    private fun currentImeIsSay(): Boolean =
        Settings.Secure.getString(contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD)
            ?.startsWith(packageName) == true

    private fun focusedEditable(): AccessibilityNodeInfo? {
        val root = rootInActiveWindow ?: return null
        val node = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return null
        if (!node.isEditable) {
            node.recycle()
            return null
        }
        return node
    }

    private inline fun <T> AccessibilityNodeInfo.recycleAfter(block: (AccessibilityNodeInfo) -> T): T =
        try { block(this) } finally { runCatching { recycle() } }

    // ── the gesture ──────────────────────────────────────────────

    private fun onBubbleTapped() {
        Log.i(TAG, "bubble tapped in ${engine.state}")
        when (engine.state) {
            State.RECORDING -> engine.stopRecording()
            State.READY, State.ERROR -> start()
            else -> Unit
        }
    }

    private fun onBubbleHeld() {
        if (engine.state == State.RECORDING) engine.cancelRecording()
    }

    private fun start() {
        val focused = focusedEditable()
        if (focused != null && focused.isPassword) {
            focused.recycle()
            toast(getString(R.string.bubble_no_password))
            return
        }
        target = Target(
            packageName = focused?.packageName?.toString() ?: rootInActiveWindow?.packageName?.toString(),
            fieldId = focused?.viewIdResourceName?.hashCode() ?: 0,
        )
        focused?.recycle()
        engine.ensureModelLoaded()
        if (!engine.canStartRecording()) {
            toast(getString(R.string.panel_loading))
            return
        }
        bubble?.mode = Bubble.Mode.LISTEN
        if (!engine.startRecording(target ?: Target(null, 0), sink)) bubble?.mode = Bubble.Mode.REST
    }

    private fun render(event: DictationController.Event) {
        val b = bubble ?: return
        when (event.state) {
            State.RECORDING -> b.mode = Bubble.Mode.LISTEN
            State.PROCESSING -> b.mode = Bubble.Mode.WORK
            State.ERROR -> { b.mode = Bubble.Mode.REST; event.error?.let { toast(getString(R.string.status_error, it)) } }
            State.READY -> {
                b.mode = Bubble.Mode.REST
                when (event.message) {
                    DictationController.Messages.NOTHING_HEARD, DictationController.Messages.TOO_SHORT -> toast(getString(R.string.panel_nothing_heard))
                    DictationController.Messages.HELD -> toast(getString(R.string.bubble_held))
                }
            }
            else -> b.mode = Bubble.Mode.REST
        }
        refreshVisibility()
    }

    // ── putting the words where the cursor is ────────────────────

    /** Main thread. The transcript is already persisted; this only decides where it goes. */
    private fun deliver(result: Result) {
        val node = focusedEditable()
        val nowIn = node?.packageName?.toString() ?: rootInActiveWindow?.packageName?.toString()
        if (focusPolicy(result.target.packageName, nowIn) == "hold" || node == null) {
            node?.recycle()
            engine.hold(result)
            return
        }
        node.recycleAfter { n ->
            val method = insertInto(n, result.text)
            if (method == null) {
                engine.hold(result)
            } else {
                engine.reportOutcome(result, true, method)
            }
        }
    }

    /** SET_TEXT at the cursor, else paste, else null. */
    private fun insertInto(node: AccessibilityNodeInfo, text: String): String? {
        node.refresh()
        val existing = if (node.isShowingHintText) "" else (node.text?.toString() ?: "")
        val splice = TextSplice.splice(existing, node.textSelectionStart, node.textSelectionEnd, text)
        val setArgs = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, splice.text)
        }
        if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, setArgs)) {
            val selArgs = Bundle().apply {
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, splice.cursor)
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, splice.cursor)
            }
            node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, selArgs)
            return "a11y"
        }
        // Editors that refuse SET_TEXT (some web views, custom editors) still
        // accept a paste. The clipboard is put back afterwards, as on the Mac.
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val previous = runCatching { cm.primaryClip }.getOrNull()
        val prefix = if (existing.isEmpty() || node.textSelectionStart <= 0 || existing.getOrNull(node.textSelectionStart - 1)?.isWhitespace() == true) "" else " "
        cm.setPrimaryClip(ClipData.newPlainText("Say", prefix + text))
        val pasted = node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
        handler.postDelayed({
            runCatching { if (previous != null) cm.setPrimaryClip(previous) else cm.clearPrimaryClip() }
        }, 400)
        return if (pasted) "a11y-paste" else null
    }

    private fun toast(text: String) {
        handler.post { Toast.makeText(this, text, Toast.LENGTH_SHORT).show() }
    }

    companion object {
        private const val TAG = "Say.Overlay"

        /** The running service, or null when the user has not enabled it. */
        @Volatile var instance: SayAccessibilityService? = null
            private set

        fun isEnabled(context: Context): Boolean {
            val enabled = Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
            return enabled.split(':').any { it.startsWith(context.packageName + "/") }
        }
    }
}
