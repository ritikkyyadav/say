package com.ritikyadav.say.debug

import com.ritikyadav.say.engine.AudioSource
import com.ritikyadav.say.engine.Config
import java.io.DataInputStream
import java.io.File
import java.io.FileInputStream
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * A stored dictation standing in for the microphone: 16-bit PCM WAV, read one
 * block at a time. `realtime` paces the blocks at wall-clock speed so the glyph
 * and the segmenter see what they would see from a live mic.
 */
class WavFileSource(private val file: File, private val realtime: Boolean) : AudioSource {
    private var input: DataInputStream? = null
    private var remainingBytes = 0L
    private var channels = 1

    override fun open(): Boolean {
        val stream = DataInputStream(FileInputStream(file).buffered())
        try {
            val riff = ByteArray(12)
            stream.readFully(riff)
            if (String(riff, 0, 4) != "RIFF" || String(riff, 8, 4) != "WAVE") throw IOException("not a WAV")
            while (true) {
                val head = ByteArray(8)
                stream.readFully(head)
                val id = String(head, 0, 4)
                val size = ByteBuffer.wrap(head, 4, 4).order(ByteOrder.LITTLE_ENDIAN).int.toLong() and 0xffffffffL
                if (id == "fmt ") {
                    val fmt = ByteArray(size.toInt())
                    stream.readFully(fmt)
                    val bb = ByteBuffer.wrap(fmt).order(ByteOrder.LITTLE_ENDIAN)
                    val format = bb.short.toInt()
                    channels = bb.short.toInt()
                    val rate = bb.int
                    bb.int; bb.short
                    val bits = bb.short.toInt()
                    if (format != 1 || bits != 16) throw IOException("need 16-bit PCM, got format $format / $bits bits")
                    if (rate != Config.SAMPLE_RATE) throw IOException("need ${Config.SAMPLE_RATE} Hz, got $rate")
                } else if (id == "data") {
                    remainingBytes = size
                    break
                } else {
                    stream.skipBytes(size.toInt() + (size.toInt() and 1))
                }
            }
        } catch (e: IOException) {
            stream.close()
            throw e
        }
        input = stream
        return true
    }

    override fun read(buffer: ShortArray, count: Int): Int {
        val stream = input ?: return -1
        if (remainingBytes <= 0) return -1
        val frameBytes = 2 * channels
        val want = minOf(count.toLong() * frameBytes, remainingBytes - remainingBytes % frameBytes).toInt()
        if (want <= 0) return -1
        val bytes = ByteArray(want)
        stream.readFully(bytes)
        remainingBytes -= want
        val bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        val frames = want / frameBytes
        for (i in 0 until frames) {
            buffer[i] = bb.short
            for (c in 1 until channels) bb.short   // keep the first channel only
        }
        if (realtime) Thread.sleep(frames * 1000L / Config.SAMPLE_RATE)
        return frames
    }

    override fun close() {
        input?.close()
        input = null
    }
}
