package com.ritikyadav.say.engine

import android.content.Context
import android.util.Log
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class InsufficientStorage(message: String) : IOException(message)

/** Progress of one model download, as the Models list shows it. */
data class Download(val state: State, val percent: Int, val error: String?) {
    enum class State { IDLE, DOWNLOADING, DONE, ERROR }

    companion object {
        val IDLE = Download(State.IDLE, 0, null)
    }
}

/**
 * Model files on disk: the bundled one copied out of the APK, the others
 * fetched component by component with resume, real-byte progress, a disk
 * pre-check and a cancel that cleans up partials — `model_manager.py`, with
 * `download_file.py`'s .partial-then-rename discipline.
 */
class ModelManager(context: Context) {
    private val app = context.applicationContext
    private val root = File(app.noBackupFilesDir, "models")
    private val executor = Executors.newSingleThreadExecutor { Thread(it, "say-download") }
    private val downloads = ConcurrentHashMap<String, Download>()
    private val cancels = ConcurrentHashMap<String, AtomicBoolean>()
    private val listeners = CopyOnWriteArrayList<(String, Download) -> Unit>()

    fun dirFor(id: String): File = File(root, id)

    fun addListener(l: (String, Download) -> Unit) { listeners.add(l) }
    fun removeListener(l: (String, Download) -> Unit) { listeners.remove(l) }

    fun download(id: String): Download = downloads[id] ?: Download.IDLE

    /** Every component present and non-empty, plus the marker the last step writes. */
    fun isInstalled(id: String): Boolean {
        val info = ModelCatalog.get(id) ?: return false
        val dir = dirFor(id)
        return File(dir, COMPLETE_MARKER).exists() && info.files.all { File(dir, it).let { f -> f.isFile && f.length() > 0 } }
    }

    fun diskUsage(id: String): Long = dirFor(id).listFiles()?.sumOf { it.length() } ?: 0L

    /** True when this build actually carries `info`'s files inside the APK. */
    fun isInAssets(info: ModelInfo): Boolean = runCatching {
        app.assets.list("models/${info.id}")?.isNotEmpty() == true
    }.getOrDefault(false)

    /**
     * Blocking: put `info`'s files on disk and return their directory, by
     * whichever route this build has — already installed, copied out of the
     * APK, or downloaded now.
     *
     * The third route is what a `-PsayBundleModel=false` build lives on: an
     * APK with no model is 13 MB instead of 64 MB, which matters when the only
     * way onto the phone is a messaging app and a package installer that
     * refuses large sideloaded updates without saying why. First launch then
     * needs the network once, and never again.
     */
    fun ensureAvailable(info: ModelInfo, onProgress: ((Int) -> Unit)? = null): File {
        val dir = dirFor(info.id)
        if (isInstalled(info.id)) return dir
        if (isInAssets(info)) return copyFromAssets(info)
        val cancel = AtomicBoolean(false)
        cancels[info.id] = cancel
        publish(info.id, Download(Download.State.DOWNLOADING, 0, null))
        run(info, cancel, onProgress)
        if (!isInstalled(info.id)) {
            throw IOException(download(info.id).error ?: "could not fetch ${info.name}")
        }
        return dir
    }

    /** The bundled model, by whatever route this build has. */
    fun ensureBundled(): File = ensureAvailable(ModelCatalog.bundled())

    private fun copyFromAssets(info: ModelInfo): File {
        val dir = dirFor(info.id)
        dir.mkdirs()
        val buffer = ByteArray(1 shl 16)
        for (file in info.files) {
            val target = File(dir, file)
            val partial = File(dir, "$file.partial")
            app.assets.open("models/${info.id}/$file").use { input ->
                FileOutputStream(partial).use { output ->
                    while (true) {
                        val n = input.read(buffer)
                        if (n < 0) break
                        output.write(buffer, 0, n)
                    }
                }
            }
            if (!partial.renameTo(target)) throw IOException("could not place $file")
        }
        File(dir, COMPLETE_MARKER).writeText(TranscriptStoreClock.now())
        Log.i(TAG, "bundled model ${info.id} copied to $dir")
        return dir
    }

    /** Start fetching `id` in the background. Idempotent while a download is running. */
    fun startDownload(id: String) {
        val info = ModelCatalog.get(id) ?: throw IllegalArgumentException("unknown model $id")
        if (download(id).state == Download.State.DOWNLOADING) return
        prepare(info)
        val cancel = AtomicBoolean(false)
        cancels[id] = cancel
        publish(id, Download(Download.State.DOWNLOADING, 0, null))
        executor.execute { run(info, cancel, null) }
    }

    fun cancelDownload(id: String): Boolean {
        val flag = cancels[id] ?: return false
        flag.set(true)
        return true
    }

    /** Remove a downloaded model. Refused for the bundled model and for one still downloading. */
    fun delete(id: String, active: String) {
        val info = ModelCatalog.get(id) ?: throw IllegalArgumentException("unknown model $id")
        if (info.bundled) throw IllegalStateException("the built-in model cannot be removed")
        if (id == active) throw IllegalStateException("the model in use cannot be removed")
        if (download(id).state == Download.State.DOWNLOADING) throw IllegalStateException("still downloading")
        val dir = dirFor(id)
        // Paranoia the Mac learned the hard way: only ever delete inside our own root.
        if (!dir.canonicalPath.startsWith(root.canonicalPath + File.separator)) throw IllegalStateException("refusing to delete outside the model root")
        dir.deleteRecursively()
        downloads.remove(id)
        publish(id, Download.IDLE)
    }

    /**
     * Make the model's directory and refuse to start a download that cannot
     * finish. Both entry points go through here: the first version of
     * `ensureAvailable` did not, and its very first run failed with ENOENT on
     * a `.partial` file in a directory nobody had created.
     */
    private fun prepare(info: ModelInfo): File {
        val dir = dirFor(info.id)
        dir.mkdirs()
        val needed = info.downloadBytes - diskUsage(info.id)
        if (needed > 0 && dir.usableSpace < needed + SPARE_BYTES) {
            throw InsufficientStorage("needs ${needed / 1_000_000} MB free, and the phone has ${dir.usableSpace / 1_000_000} MB")
        }
        return dir
    }

    private fun run(info: ModelInfo, cancel: AtomicBoolean, onProgress: ((Int) -> Unit)?) {
        val dir = prepare(info)
        try {
            var done = 0L
            val total = info.downloadBytes
            // Progress is reported per 64 KB read — about 800 times per file.
            // Every report costs a listener fan-out and, on the other end, a
            // whole model list rebuilt on the main thread. Unthrottled, the
            // download outruns the UI thread, the queued work never drains and
            // the heap dies: a real OOM at 192 MB on the first honest test of
            // this path. One report per whole percent, and no faster than
            // every 200 ms, is all a progress bar can show anyway.
            var lastPercent = -1
            var lastReportAt = 0L
            for (file in info.files) {
                val target = File(dir, file)
                if (target.isFile && target.length() > 0) {
                    done += target.length()
                    continue
                }
                val partial = File(dir, "$file.partial")
                done = fetch(info.urlFor(file), partial, done, total, cancel) { d ->
                    val percent = (100 * d / maxOf(1, total)).toInt().coerceIn(0, 99)
                    val now = System.currentTimeMillis()
                    if (percent != lastPercent && now - lastReportAt >= PROGRESS_INTERVAL_MS) {
                        lastPercent = percent
                        lastReportAt = now
                        publish(info.id, Download(Download.State.DOWNLOADING, percent, null))
                        onProgress?.let { cb -> runCatching { cb(percent) } }
                    }
                }
                if (cancel.get()) throw InterruptedException("cancelled")
                if (!partial.renameTo(target)) throw IOException("could not place $file")
            }
            File(dir, COMPLETE_MARKER).writeText(TranscriptStoreClock.now())
            onProgress?.let { cb -> runCatching { cb(100) } }
            publish(info.id, Download(Download.State.DONE, 100, null))
        } catch (e: InterruptedException) {
            dir.listFiles()?.filter { it.name.endsWith(".partial") }?.forEach { it.delete() }
            publish(info.id, Download.IDLE)
        } catch (e: Exception) {
            Log.w(TAG, "download of ${info.id} failed", e)
            publish(info.id, Download(Download.State.ERROR, 0, e.message ?: e.javaClass.simpleName))
        } finally {
            cancels.remove(info.id)
        }
    }

    /** Resume-aware fetch into `partial`. Returns the running byte total for progress. */
    private fun fetch(url: String, partial: File, doneBefore: Long, total: Long, cancel: AtomicBoolean, progress: (Long) -> Unit): Long {
        var offset = if (partial.isFile) partial.length() else 0L
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 30_000
            readTimeout = 60_000
            if (offset > 0) setRequestProperty("Range", "bytes=$offset-")
        }
        try {
            val code = conn.responseCode
            val append = when (code) {
                HttpURLConnection.HTTP_PARTIAL -> true
                HttpURLConnection.HTTP_OK -> { offset = 0; false }
                416 -> return doneBefore + offset            // already complete
                else -> throw IOException("HTTP $code for $url")
            }
            var done = doneBefore + offset
            val buffer = ByteArray(1 shl 16)
            conn.inputStream.use { input ->
                FileOutputStream(partial, append).use { output ->
                    while (true) {
                        if (cancel.get()) throw InterruptedException("cancelled")
                        val n = input.read(buffer)
                        if (n < 0) break
                        output.write(buffer, 0, n)
                        done += n
                        progress(done)
                    }
                }
            }
            return done
        } finally {
            conn.disconnect()
        }
    }

    private fun publish(id: String, d: Download) {
        downloads[id] = d
        for (l in listeners) runCatching { l(id, d) }
    }

    companion object {
        private const val TAG = "Say.Models"
        const val COMPLETE_MARKER = ".complete"
        /** Never fill the disk to the last byte: Android gets unhappy below ~100 MB. */
        const val SPARE_BYTES = 150L * 1024 * 1024

        /** Floor on the gap between two progress reports. See the note in `run`. */
        const val PROGRESS_INTERVAL_MS = 200L
    }
}

/** Tiny seam so the marker file carries a timestamp without dragging the store in here. */
internal object TranscriptStoreClock {
    fun now(): String = java.time.Instant.now().toString()
}
