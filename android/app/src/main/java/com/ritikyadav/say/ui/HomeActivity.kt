package com.ritikyadav.say.ui

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.provider.Settings as SystemSettings
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.android.material.materialswitch.MaterialSwitch
import com.ritikyadav.say.BuildConfig
import com.ritikyadav.say.R
import com.ritikyadav.say.SayApp
import com.ritikyadav.say.engine.DictationController
import com.ritikyadav.say.engine.DictationController.State
import com.ritikyadav.say.engine.Download
import com.ritikyadav.say.engine.InsufficientStorage
import com.ritikyadav.say.engine.ModelCatalog
import com.ritikyadav.say.engine.ModelInfo
import com.ritikyadav.say.engine.Result
import com.ritikyadav.say.engine.Target
import com.ritikyadav.say.glyph.GlyphView
import com.ritikyadav.say.overlay.SayAccessibilityService
import com.ritikyadav.say.store.Transcript
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * The one screen: what the engine is doing, a field you can dictate into from
 * right here, how to reach Say from other apps, three numbers, the history by
 * day, the models, the settings. One scroll on purpose.
 *
 * The Dictate button matters more than it looks: it runs the whole pipeline
 * without any keyboard switching, so it is both the first thing a new user
 * can try and the way to see, on this exact phone, whether the microphone and
 * the model work — with the reason on screen when they do not.
 */
class HomeActivity : AppCompatActivity() {
    private lateinit var app: SayApp
    private lateinit var engineStatus: TextView
    private lateinit var tryField: EditText
    private lateinit var glyph: GlyphView
    private lateinit var dictate: Button
    private lateinit var setupRows: LinearLayout
    private lateinit var transcripts: LinearLayout
    private lateinit var transcriptsEmpty: TextView
    private lateinit var models: LinearLayout
    private lateinit var dictionary: EditText
    private lateinit var replacements: EditText

    /** The user tapped Dictate before the mic was allowed; start once it is. */
    private var startAfterPermission = false

    private val storeListener: () -> Unit = { runOnUiThread { refreshTranscripts(); refreshKpis() } }
    private val modelListener: (String, Download) -> Unit = { _, _ -> runOnUiThread { refreshModels() } }
    private val engineListener: (DictationController.Event) -> Unit = { render(it) }

    /** Where an in-app dictation lands: the field on this screen. */
    private val homeSink: (Result) -> Unit = { result ->
        val existing = tryField.text?.toString() ?: ""
        val prefix = if (existing.isEmpty() || existing.last().isWhitespace()) "" else " "
        tryField.append(prefix + result.text)
        app.engine.reportOutcome(result, true, "app")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_home)
        app = SayApp.of(this)
        engineStatus = findViewById(R.id.engine_status)
        tryField = findViewById(R.id.try_field)
        glyph = findViewById<GlyphView>(R.id.glyph).also { it.analyzer = app.engine.analyzer }
        dictate = findViewById<Button>(R.id.btn_dictate).also { it.setOnClickListener { onDictateTapped() } }
        findViewById<Button>(R.id.btn_switch).setOnClickListener { showPicker() }
        findViewById<Button>(R.id.btn_bubble).setOnClickListener {
            startActivity(Intent(SystemSettings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        setupRows = findViewById(R.id.setup_rows)
        transcripts = findViewById(R.id.transcripts)
        transcriptsEmpty = findViewById(R.id.transcripts_empty)
        models = findViewById(R.id.models)
        dictionary = findViewById(R.id.dictionary)
        replacements = findViewById(R.id.replacements)

        val s = app.settings
        bindSwitch(R.id.sw_auto_correct, s.autoCorrect) { s.autoCorrect = it }
        bindSwitch(R.id.sw_capitalise, s.autoCapitalise) { s.autoCapitalise = it }
        bindSwitch(R.id.sw_punctuation, s.autoTerminalPunctuation) { s.autoTerminalPunctuation = it }
        bindSwitch(R.id.sw_return, s.returnToKeyboard) { s.returnToKeyboard = it }
        dictionary.setText(s.dictionaryText)
        replacements.setText(s.replacementsText)
        dictionary.addTextChangedListener(afterChange { s.dictionaryText = it; app.engine.refreshKeyterms() })
        replacements.addTextChangedListener(afterChange { s.replacementsText = it })

        findViewById<TextView>(R.id.build_stamp).text =
            getString(R.string.build_stamp, BuildConfig.VERSION_NAME)

        // Warm the model now so the first dictation is instant.
        app.engine.ensureModelLoaded()
    }

    override fun onResume() {
        super.onResume()
        app.store.addListener(storeListener)
        app.models.addListener(modelListener)
        app.engine.addListener(engineListener)
        render(app.engine.lastEvent)
        refreshSetup()
        refreshBubble()
        refreshKpis()
        refreshTranscripts()
        refreshModels()
    }

    override fun onPause() {
        app.store.removeListener(storeListener)
        app.models.removeListener(modelListener)
        app.engine.removeListener(engineListener)
        super.onPause()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        refreshSetup()
        if (startAfterPermission && micGranted()) {
            startAfterPermission = false
            onDictateTapped()
        }
    }

    // ── dictating from here ──────────────────────────────────────

    private fun onDictateTapped() {
        val engine = app.engine
        when (engine.state) {
            State.RECORDING -> engine.stopRecording()
            State.PROCESSING, State.LOADING -> Unit
            else -> {
                if (!micGranted()) {
                    startAfterPermission = true
                    Toast.makeText(this, R.string.mic_request_hint, Toast.LENGTH_SHORT).show()
                    ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), REQUEST_MIC)
                    return
                }
                engine.ensureModelLoaded()
                if (!engine.canStartRecording()) return
                glyph.visibility = View.VISIBLE
                glyph.mode = GlyphView.Mode.LISTEN
                if (!engine.startRecording(Target(packageName, R.id.try_field), homeSink)) {
                    glyph.mode = GlyphView.Mode.HIDDEN
                }
            }
        }
    }

    private fun render(event: DictationController.Event) {
        val ok = getColor(R.color.say_ink_secondary)
        val bad = getColor(R.color.signal)
        engineStatus.setTextColor(ok)
        when (event.state) {
            State.IDLE, State.LOADING -> {
                val p = event.progress
                if (p != null) engineStatus.text = getString(R.string.status_downloading, p.first)
                else engineStatus.setText(R.string.status_loading)
                dictate.setText(R.string.dictate)
                dictate.isEnabled = false
                glyph.mode = GlyphView.Mode.REST
            }
            State.READY -> {
                val name = ModelCatalog.get(app.engine.loadedModel ?: app.settings.model)?.name ?: app.settings.model
                engineStatus.text = when (event.message) {
                    DictationController.Messages.NOTHING_HEARD, DictationController.Messages.TOO_SHORT -> getString(R.string.status_nothing_heard)
                    DictationController.Messages.HELD -> getString(R.string.status_held)
                    else -> getString(R.string.status_ready, name)
                }
                dictate.setText(R.string.dictate)
                dictate.isEnabled = true
                glyph.mode = GlyphView.Mode.HIDDEN
                glyph.postDelayed({ if (app.engine.state == State.READY) glyph.visibility = View.GONE }, 400)
            }
            State.RECORDING -> {
                engineStatus.setText(R.string.status_recording)
                dictate.setText(R.string.dictate_stop)
                dictate.isEnabled = true
                glyph.visibility = View.VISIBLE
                glyph.mode = GlyphView.Mode.LISTEN
            }
            State.PROCESSING -> {
                val p = event.progress
                engineStatus.text = if (p != null && p.second > 1) getString(R.string.panel_progress, p.first, p.second) else getString(R.string.status_processing)
                dictate.setText(R.string.dictate_stop)
                dictate.isEnabled = false
                glyph.visibility = View.VISIBLE
                glyph.mode = GlyphView.Mode.WORK
            }
            State.ERROR -> {
                engineStatus.setTextColor(bad)
                engineStatus.text = getString(R.string.status_error, event.error ?: "unknown")
                dictate.setText(R.string.dictate)
                dictate.isEnabled = true
                glyph.mode = GlyphView.Mode.HIDDEN
            }
        }
        refreshModels()
    }

    private fun showPicker() {
        (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager).showInputMethodPicker()
    }

    // ── setup ────────────────────────────────────────────────────

    private fun imeEnabled(): Boolean {
        val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        return imm.enabledInputMethodList.any { it.packageName == packageName }
    }

    private fun micGranted(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun refreshSetup() {
        val enabled = imeEnabled()
        val mic = micGranted()
        setupRows.removeAllViews()
        setupRow(R.string.how_enable, R.string.setup_enable_action, enabled) {
            startActivity(Intent(SystemSettings.ACTION_INPUT_METHOD_SETTINGS))
        }
        setupRow(R.string.how_mic, R.string.setup_mic_action, mic) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), REQUEST_MIC)
        }
        setupRow(R.string.how_pick, R.string.how_pick_action, null) { showPicker() }
    }

    private fun refreshBubble() {
        val on = SayAccessibilityService.isEnabled(this)
        findViewById<TextView>(R.id.bubble_status).setText(if (on) R.string.bubble_on else R.string.bubble_off)
        findViewById<TextView>(R.id.bubble_dot).setTextColor(getColor(if (on) R.color.olive else R.color.cloud_mid))
        findViewById<Button>(R.id.btn_bubble).setText(if (on) R.string.setup_done else R.string.bubble_action)
    }

    /** `done` null means a step that is never "done" — an instruction with a shortcut. */
    private fun setupRow(text: Int, action: Int, done: Boolean?, onAction: () -> Unit) {
        val row = LayoutInflater.from(this).inflate(R.layout.row_setup, setupRows, false)
        row.findViewById<TextView>(R.id.text).setText(text)
        row.findViewById<TextView>(R.id.dot).setTextColor(
            getColor(if (done == true) R.color.olive else if (done == false) R.color.signal else R.color.cloud_mid)
        )
        row.findViewById<Button>(R.id.action).apply {
            setText(if (done == true) R.string.how_done else action)
            isEnabled = done != true
            setOnClickListener { onAction() }
        }
        setupRows.addView(row)
    }

    // ── numbers ──────────────────────────────────────────────────

    private fun refreshKpis() {
        val s = app.store.summary()
        kpi(R.id.kpi_words, fmt(s.totalWords), R.string.kpi_words)
        kpi(R.id.kpi_wpm, if (s.wpm30d > 0) s.wpm30d.toInt().toString() else "0", R.string.kpi_wpm)
        kpi(R.id.kpi_streak, s.streakDays.toString(), R.string.kpi_streak)
    }

    private fun kpi(id: Int, value: String, label: Int) {
        val v = findViewById<View>(id)
        v.findViewById<TextView>(R.id.value).text = value
        v.findViewById<TextView>(R.id.label).setText(label)
    }

    // ── transcripts ──────────────────────────────────────────────

    private fun refreshTranscripts() {
        val rows = app.store.list(200)
        transcripts.removeAllViews()
        transcriptsEmpty.visibility = if (rows.isEmpty()) View.VISIBLE else View.GONE
        val inflater = LayoutInflater.from(this)
        val zone = ZoneId.systemDefault()
        val today = LocalDate.now(zone)
        var lastDay: LocalDate? = null
        for (row in rows) {
            val instant = runCatching { Instant.parse(row.tsUtc) }.getOrNull() ?: continue
            val local = instant.atZone(zone)
            val day = local.toLocalDate()
            if (day != lastDay) {
                lastDay = day
                val header = inflater.inflate(R.layout.row_day, transcripts, false) as TextView
                header.text = when (day) {
                    today -> "Today"
                    today.minusDays(1) -> "Yesterday"
                    else -> day.format(DateTimeFormatter.ofPattern("MMMM d, yyyy", Locale.getDefault()))
                }
                transcripts.addView(header)
            }
            val view = inflater.inflate(R.layout.row_transcript, transcripts, false)
            val meta = view.findViewById<TextView>(R.id.meta)
            val text = view.findViewById<TextView>(R.id.text)
            val parts = mutableListOf(local.format(DateTimeFormatter.ofPattern("HH:mm")))
            appLabel(row.targetApp)?.let { parts.add(it) }
            parts.add("${row.audioSeconds.toInt()}s")
            parts.add("${row.wordCount} words")
            if (!row.success) parts.add(if (row.injectMethod == "clipboard") "held" else "not typed")
            meta.text = parts.joinToString(" · ")
            text.text = row.text
            view.setOnClickListener { text.maxLines = if (text.maxLines == 2) Int.MAX_VALUE else 2 }
            view.setOnLongClickListener { copy(row); true }
            transcripts.addView(view)
        }
    }

    private fun copy(row: Transcript) {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("Say", row.text))
        Toast.makeText(this, R.string.copied, Toast.LENGTH_SHORT).show()
    }

    private fun appLabel(pkg: String?): String? {
        if (pkg.isNullOrEmpty()) return null
        if (pkg == packageName) return getString(R.string.app_name)
        return try {
            packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString()
        } catch (e: PackageManager.NameNotFoundException) {
            pkg.substringAfterLast('.').replaceFirstChar { it.uppercase() }
        }
    }

    // ── models ───────────────────────────────────────────────────

    private fun refreshModels() {
        models.removeAllViews()
        val inflater = LayoutInflater.from(this)
        val active = app.settings.model
        val ordered = ModelCatalog.CATALOG.sortedWith(
            compareBy<ModelInfo> { it.id != active }.thenBy { !(it.bundled || app.models.isInstalled(it.id)) }.thenBy { -it.accuracy }
        )
        for (info in ordered) {
            val view = inflater.inflate(R.layout.row_model, models, false)
            val installed = info.bundled || app.models.isInstalled(info.id)
            val download = app.models.download(info.id)
            val inUse = info.id == active
            view.findViewById<TextView>(R.id.name).text = info.name
            view.findViewById<TextView>(R.id.pill).apply {
                setText(
                    when {
                        inUse -> R.string.model_in_use
                        info.bundled && BuildConfig.BUNDLED_MODEL -> R.string.model_bundled
                        installed -> R.string.model_installed
                        else -> R.string.model_not_installed
                    }
                )
                setTextColor(getColor(if (inUse) R.color.say_accent else R.color.say_ink_secondary))
            }
            view.findViewById<TextView>(R.id.blurb).text = info.blurb
            view.findViewById<TextView>(R.id.detail).text =
                "English · ${mb(info.downloadBytes)} · speed ${info.speed}/5 · accuracy ${info.accuracy}/5" +
                    if (download.state == Download.State.ERROR) "\n" + getString(R.string.model_error, download.error) else ""
            val progress = view.findViewById<ProgressBar>(R.id.progress)
            val action = view.findViewById<Button>(R.id.action)
            when {
                download.state == Download.State.DOWNLOADING -> {
                    progress.visibility = View.VISIBLE
                    progress.progress = download.percent
                    action.setText(R.string.model_cancel)
                    action.setOnClickListener { app.models.cancelDownload(info.id) }
                }
                inUse -> {
                    action.setText(R.string.model_in_use)
                    action.isEnabled = false
                }
                installed -> {
                    action.setText(R.string.model_use)
                    action.setOnClickListener {
                        app.settings.model = info.id
                        app.engine.reloadModel()
                        refreshModels()
                    }
                    if (!info.bundled) view.findViewById<TextView>(R.id.name).setOnLongClickListener {
                        runCatching { app.models.delete(info.id, app.settings.model) }
                            .onFailure { Toast.makeText(this, it.message, Toast.LENGTH_SHORT).show() }
                        refreshModels(); true
                    }
                }
                else -> {
                    action.text = getString(R.string.model_download, mb(info.downloadBytes))
                    action.setOnClickListener {
                        try {
                            app.models.startDownload(info.id)
                        } catch (e: InsufficientStorage) {
                            Toast.makeText(this, R.string.model_no_space, Toast.LENGTH_LONG).show()
                        }
                        refreshModels()
                    }
                }
            }
            models.addView(view)
        }
    }

    // ── helpers ──────────────────────────────────────────────────

    private fun bindSwitch(id: Int, value: Boolean, onChange: (Boolean) -> Unit) {
        findViewById<MaterialSwitch>(id).apply {
            isChecked = value
            setOnCheckedChangeListener { _, checked -> onChange(checked) }
        }
    }

    private fun afterChange(block: (String) -> Unit) = object : TextWatcher {
        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
        override fun afterTextChanged(s: Editable?) = block(s?.toString() ?: "")
    }

    private fun fmt(n: Int): String = String.format(Locale.getDefault(), "%,d", n)

    private fun mb(bytes: Long): String = "${(bytes / 1_000_000.0).let { if (it >= 100) it.toInt().toString() else "%.0f".format(it) }} MB"

    private companion object { const val REQUEST_MIC = 7 }
}
