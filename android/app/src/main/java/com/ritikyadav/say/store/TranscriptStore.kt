package com.ritikyadav.say.store

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.concurrent.CopyOnWriteArrayList

data class Transcript(
    val id: Long,
    val tsUtc: String,
    val text: String,
    val wordCount: Int,
    val audioSeconds: Double,
    val inferenceSeconds: Double,
    val model: String,
    val injectMethod: String,
    val targetApp: String?,
    val success: Boolean,
)

data class Summary(
    val totalWords: Int,
    val wpm30d: Double,
    val streakDays: Int,
    val wordsThisMonth: Int,
    val wordsPrevMonth: Int,
    val transcriptCount: Int,
)

/**
 * SQLite history — the same `transcripts` table the Mac keeps, column for
 * column, so anything that reads one can read the other. Audio is never
 * stored on Android; `audio_path` exists for schema parity and stays NULL.
 */
class TranscriptStore(context: Context, name: String? = "transcripts.db") :
    SQLiteOpenHelper(context.applicationContext, name, null, VERSION) {

    private val listeners = CopyOnWriteArrayList<() -> Unit>()

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS transcripts (
              id                INTEGER PRIMARY KEY AUTOINCREMENT,
              ts_utc            TEXT NOT NULL,
              text              TEXT NOT NULL,
              word_count        INTEGER NOT NULL,
              audio_seconds     REAL NOT NULL,
              inference_seconds REAL NOT NULL,
              model             TEXT NOT NULL,
              inject_method     TEXT NOT NULL,
              target_app        TEXT,
              success           INTEGER NOT NULL,
              audio_path        TEXT,
              observed          INTEGER NOT NULL DEFAULT 0
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_ts ON transcripts(ts_utc)")
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS corrections (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              transcript_id   INTEGER NOT NULL,
              ts_utc          TEXT NOT NULL,
              source          TEXT NOT NULL,
              inserted        TEXT NOT NULL,
              corrected       TEXT NOT NULL,
              edit_distance   INTEGER NOT NULL,
              words           INTEGER NOT NULL,
              target_app      TEXT
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_corr_ts ON corrections(ts_utc)")
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_corr_transcript ON corrections(transcript_id)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    fun addListener(listener: () -> Unit) { listeners.add(listener) }
    fun removeListener(listener: () -> Unit) { listeners.remove(listener) }
    private fun changed() { for (l in listeners) runCatching { l() } }

    /** Persist a dictation. Called BEFORE the text is committed, so a failed commit can never lose it. */
    fun insert(
        text: String,
        audioSeconds: Double,
        inferenceSeconds: Double,
        model: String,
        injectMethod: String,
        targetApp: String?,
        success: Boolean,
        tsUtc: String = nowUtc(),
    ): Long {
        val values = ContentValues().apply {
            put("ts_utc", tsUtc)
            put("text", text)
            put("word_count", wordCount(text))
            put("audio_seconds", audioSeconds)
            put("inference_seconds", inferenceSeconds)
            put("model", model)
            put("inject_method", injectMethod)
            put("target_app", targetApp)
            put("success", if (success) 1 else 0)
        }
        val id = writableDatabase.insertOrThrow("transcripts", null, values)
        changed()
        return id
    }

    fun setOutcome(id: Long, success: Boolean, injectMethod: String) {
        val values = ContentValues().apply {
            put("success", if (success) 1 else 0)
            put("inject_method", injectMethod)
        }
        writableDatabase.update("transcripts", values, "id = ?", arrayOf(id.toString()))
        changed()
    }

    fun delete(id: Long) {
        writableDatabase.delete("transcripts", "id = ?", arrayOf(id.toString()))
        changed()
    }

    fun list(limit: Int = 200): List<Transcript> {
        val out = ArrayList<Transcript>()
        readableDatabase.query(
            "transcripts", null, null, null, null, null, "ts_utc DESC, id DESC", limit.toString()
        ).use { c ->
            while (c.moveToNext()) {
                out.add(
                    Transcript(
                        id = c.getLong(c.getColumnIndexOrThrow("id")),
                        tsUtc = c.getString(c.getColumnIndexOrThrow("ts_utc")),
                        text = c.getString(c.getColumnIndexOrThrow("text")),
                        wordCount = c.getInt(c.getColumnIndexOrThrow("word_count")),
                        audioSeconds = c.getDouble(c.getColumnIndexOrThrow("audio_seconds")),
                        inferenceSeconds = c.getDouble(c.getColumnIndexOrThrow("inference_seconds")),
                        model = c.getString(c.getColumnIndexOrThrow("model")),
                        injectMethod = c.getString(c.getColumnIndexOrThrow("inject_method")),
                        targetApp = c.getString(c.getColumnIndexOrThrow("target_app")),
                        success = c.getInt(c.getColumnIndexOrThrow("success")) == 1,
                    )
                )
            }
        }
        return out
    }

    fun count(): Int = readableDatabase.rawQuery("SELECT COUNT(*) FROM transcripts", null).use {
        if (it.moveToFirst()) it.getInt(0) else 0
    }

    /** Aggregate stats for the Home screen — the Mac's `summary()`, minus the correction ledger. */
    fun summary(today: LocalDate = LocalDate.now(ZoneOffset.UTC)): Summary {
        val cutoff30 = today.minusDays(30).atStartOfDay().toInstant(ZoneOffset.UTC).toString()
        val cutoff60 = today.minusDays(60).atStartOfDay().toInstant(ZoneOffset.UTC).toString()
        val db = readableDatabase
        val total = db.rawQuery("SELECT COALESCE(SUM(word_count), 0) FROM transcripts WHERE success = 1", null)
            .use { if (it.moveToFirst()) it.getInt(0) else 0 }
        var words30 = 0
        var seconds30 = 0.0
        db.rawQuery(
            "SELECT COALESCE(SUM(word_count), 0), COALESCE(SUM(audio_seconds), 0.0) FROM transcripts WHERE success = 1 AND ts_utc >= ?",
            arrayOf(cutoff30)
        ).use { if (it.moveToFirst()) { words30 = it.getInt(0); seconds30 = it.getDouble(1) } }
        val wordsPrev = db.rawQuery(
            "SELECT COALESCE(SUM(word_count), 0) FROM transcripts WHERE success = 1 AND ts_utc >= ? AND ts_utc < ?",
            arrayOf(cutoff60, cutoff30)
        ).use { if (it.moveToFirst()) it.getInt(0) else 0 }
        val days = HashSet<LocalDate>()
        db.rawQuery("SELECT DISTINCT substr(ts_utc, 1, 10) FROM transcripts WHERE success = 1", null).use {
            while (it.moveToNext()) runCatching { days.add(LocalDate.parse(it.getString(0))) }
        }
        val wpm = if (seconds30 > 0) words30 / (seconds30 / 60.0) else 0.0
        // Streak — consecutive days ending today; if nothing yet today, anchor to yesterday.
        var cursor = today
        if (cursor !in days && cursor.minusDays(1) in days) cursor = cursor.minusDays(1)
        var streak = 0
        while (cursor in days) { streak++; cursor = cursor.minusDays(1) }
        return Summary(total, Math.round(wpm * 10) / 10.0, streak, words30, wordsPrev, count())
    }

    companion object {
        const val VERSION = 1
        private val ISO = DateTimeFormatter.ISO_INSTANT

        fun nowUtc(): String = ISO.format(Instant.now())

        fun wordCount(text: String): Int = text.split(Regex("\\s+")).count { it.isNotEmpty() }
    }
}
