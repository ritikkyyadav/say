package com.ritikyadav.say.glyph

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.view.Choreographer
import android.view.View
import com.ritikyadav.say.R

/**
 * The dictation glyph: one continuous base with twelve frequency columns
 * standing on it, redrawn every display frame from live mic audio.
 *
 * A pure renderer. The analysis lives in the engine's [BandAnalyzer] (set
 * [analyzer]); this view only runs the motion and draws the silhouette — as a
 * single closed path, never twelve rectangles, so adjacent columns can never
 * show a seam. Clocked by the Choreographer at the display's rate; every value
 * in [Waveform] integrates against the measured frame interval, so a dropped
 * frame changes nothing about where the motion ends up.
 */
class GlyphView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    enum class Mode { HIDDEN, REST, LISTEN, WORK }

    /** Where the levels come from. Null draws the resting ripple only. */
    @Volatile var analyzer: BandAnalyzer? = null

    var mode: Mode = Mode.HIDDEN
        set(value) {
            if (field == value) return
            field = value
            reveal.target = if (value == Mode.HIDDEN) 0.0 else 1.0
            if (value == Mode.HIDDEN && reveal.value < 0.01) reveal.snap(0.0)
            scheduleFrame()
        }

    private val motion = BarMotion()
    private val reveal = Spring(0.0, omega = 44.0, zeta = 1.0)
    private val path = Path()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = context.getColor(R.color.say_ink)
    }
    private var lastFrameNanos = 0L
    private var frameScheduled = false
    private val geometry = GlyphGeometry()

    private val frameCallback = Choreographer.FrameCallback { nanos ->
        frameScheduled = false
        val dt = if (lastFrameNanos == 0L) 1.0 / 60.0 else ((nanos - lastFrameNanos) / 1e9).coerceIn(1e-4, 0.25)
        lastFrameNanos = nanos
        step(dt)
        invalidate()
        if (mode != Mode.HIDDEN || !reveal.settled) scheduleFrame() else lastFrameNanos = 0L
    }

    private var levels = DoubleArray(Waveform.BAND_COUNT)

    private fun step(dt: Double) {
        reveal.tick(dt)
        val state = when (mode) {
            Mode.WORK -> BarMotion.State.WORK
            Mode.LISTEN -> BarMotion.State.LISTEN
            // At rest the glyph holds the Say mark, so an idle bubble is
            // recognisably Say and is something to aim a thumb at.
            else -> BarMotion.State.MARK
        }
        val snap = if (mode == Mode.LISTEN) analyzer?.snapshot() else null
        levels = motion.tick(dt, snap?.levels, snap?.gate ?: 0.0, state)
    }

    private fun scheduleFrame() {
        if (frameScheduled) return
        frameScheduled = true
        Choreographer.getInstance().postFrameCallback(frameCallback)
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (mode != Mode.HIDDEN) scheduleFrame()
    }

    override fun onDetachedFromWindow() {
        Choreographer.getInstance().removeFrameCallback(frameCallback)
        frameScheduled = false
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toDouble()
        val h = height.toDouble()
        if (w <= 0 || h <= 0) return
        // Fit the mark's own proportions (90×19 pt on the Mac) into the view.
        val scale = minOf(w / geometry.width, h / geometry.height)
        val g = GlyphGeometry(
            columnWidth = geometry.columnWidth * scale,
            baseHeight = geometry.baseHeight * scale,
            barHeight = geometry.barHeight * scale,
        )
        val originX = (w - g.width) / 2.0
        val bottom = (h + g.height) / 2.0
        val pts = silhouette(levels, g, reveal.value, originX, 0.0, snap = 1.0)
        path.rewind()
        var i = 0
        while (i < pts.size) {
            val x = pts[i].toFloat()
            val y = (bottom - pts[i + 1]).toFloat()
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
            i += 2
        }
        path.close()
        canvas.drawPath(path, paint)
    }
}
