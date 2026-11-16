package com.ritikyadav.say.overlay

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.graphics.PixelFormat
import android.view.ContextThemeWrapper
import android.view.GestureDetector
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import com.ritikyadav.say.R
import com.ritikyadav.say.SayApp
import com.ritikyadav.say.glyph.GlyphView
import kotlin.math.roundToInt

/**
 * The floating glyph: a small pill at rest, a bar with the live spectrum
 * while listening, the processing sweep while the model works. Tap to start,
 * tap to finish, hold to cancel, drag to move. Sits just above the keyboard
 * when one is up, so it is where the thumb already is.
 *
 * An accessibility overlay window (no "draw over other apps" permission),
 * non-focusable and non-touch-modal, so it can never take the focus the
 * transcript is about to land in — the same rule as the Mac's NSPanel.
 */
class Bubble(
    private val service: AccessibilityService,
    private val onTap: () -> Unit,
    private val onLongPress: () -> Unit,
) {
    enum class Mode { REST, LISTEN, WORK }

    private val wm = service.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val density = service.resources.displayMetrics.density
    private val view: View
    private val glyph: GlyphView
    private val params: WindowManager.LayoutParams
    private var attached = false
    private var keyboardTop = -1
    private val settings = SayApp.of(service).settings

    private val restWidth = dp(72)
    private val liveWidth = dp(232)
    private val height = dp(40)

    var visible: Boolean = false
        set(value) {
            if (field == value) return
            field = value
            if (value) attach() else detach()
        }

    var mode: Mode = Mode.REST
        set(value) {
            if (field == value) return
            field = value
            glyph.mode = when (value) {
                Mode.REST -> GlyphView.Mode.REST
                Mode.LISTEN -> GlyphView.Mode.LISTEN
                Mode.WORK -> GlyphView.Mode.WORK
            }
            params.width = if (value == Mode.REST) restWidth else liveWidth
            if (attached) wm.updateViewLayout(view, params)
        }

    init {
        val themed = LayoutInflater.from(ContextThemeWrapper(service, R.style.Theme_Say))
        view = themed.inflate(R.layout.overlay_bubble, null)
        glyph = view.findViewById<GlyphView>(R.id.glyph).also {
            it.analyzer = SayApp.of(service).engine.analyzer
            it.mode = GlyphView.Mode.REST
        }
        params = WindowManager.LayoutParams(
            restWidth, height,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = settings.bubbleX.takeIf { it >= 0 } ?: (service.resources.displayMetrics.widthPixels - restWidth - dp(16))
            y = settings.bubbleY.takeIf { it >= 0 } ?: (service.resources.displayMetrics.heightPixels * 0.62).roundToInt()
        }

        val gestures = GestureDetector(service, object : GestureDetector.SimpleOnGestureListener() {
            override fun onSingleTapUp(e: MotionEvent): Boolean { onTap(); return true }
            override fun onLongPress(e: MotionEvent) { onLongPress() }
            override fun onDown(e: MotionEvent): Boolean = true
        })
        var downX = 0f; var downY = 0f; var startX = 0; var startY = 0; var dragging = false
        val slop = dp(8)
        view.setOnTouchListener { v, event ->
            if (com.ritikyadav.say.BuildConfig.DEBUG) android.util.Log.d("Say.Overlay", "touch ${event.actionMasked} at ${event.rawX.toInt()},${event.rawY.toInt()}")
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX; downY = event.rawY; startX = params.x; startY = params.y; dragging = false
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downX
                    val dy = event.rawY - downY
                    if (dragging || dx * dx + dy * dy > slop * slop) {
                        dragging = true
                        params.x = (startX + dx).roundToInt()
                        params.y = (startY + dy).roundToInt()
                        if (attached) wm.updateViewLayout(view, params)
                    }
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    if (dragging) {
                        settings.bubbleX = params.x
                        settings.bubbleY = params.y
                        v.performClick()
                    }
                }
            }
            if (dragging) true else gestures.onTouchEvent(event)
        }
    }

    /**
     * Place the mark. It goes just above the keyboard, because that is where
     * the thumb already is — but never on top of the field being dictated
     * into, which on a messaging app is exactly the strip between the two. So
     * if the keyboard-anchored spot would cover the focused field, it moves
     * above the field instead.
     *
     * A mark the user has dragged stays where they put it, except that it is
     * always clamped above the keyboard: a bubble underneath one cannot be
     * tapped, and "it stays where I left it" is worth less than "it is there".
     *
     * @param keyboardTopPx top of the keyboard window, or -1 when none is up
     * @param fieldTopPx    top of the focused text field, or -1 when unknown
     */
    fun anchorAbove(keyboardTopPx: Int, fieldTopPx: Int = -1) {
        keyboardTop = keyboardTopPx
        val gap = dp(10)
        val screenHeight = service.resources.displayMetrics.heightPixels
        val ceiling = (if (keyboardTopPx > 0) keyboardTopPx else screenHeight) - height - gap

        val draggedY = settings.bubbleY
        params.y = if (draggedY >= 0) {
            minOf(draggedY, ceiling)
        } else if (fieldTopPx > 0 && fieldTopPx <= ceiling + height) {
            // The field reaches into where the mark would sit: go above it.
            maxOf(dp(8), fieldTopPx - height - gap)
        } else {
            maxOf(dp(8), ceiling)
        }
        settings.bubbleX.takeIf { it >= 0 }?.let { params.x = it }
        if (attached) wm.updateViewLayout(view, params)
    }

    private fun attach() {
        if (attached) return
        runCatching { wm.addView(view, params) }
            .onSuccess {
                attached = true
                android.util.Log.i("Say.Overlay", "bubble shown at ${params.x},${params.y} ${params.width}x${params.height}")
            }
            .onFailure { android.util.Log.w("Say.Overlay", "bubble could not be shown", it) }
    }

    private fun detach() {
        if (!attached) return
        runCatching { wm.removeView(view) }
        attached = false
    }

    fun destroy() {
        visible = false
    }

    private fun dp(v: Int): Int = (v * density).roundToInt()
}
