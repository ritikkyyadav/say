package com.ritikyadav.say.engine

import ai.moonshine.voice.JNI

/** One installable speech model. */
data class ModelInfo(
    val id: String,
    val name: String,
    val arch: Int,
    val files: List<String>,
    val downloadBytes: Long,
    val blurb: String,
    /** 1..5, relative to the other entries. */
    val speed: Int,
    val accuracy: Int,
    /** Ships inside the APK and is copied out on first launch. */
    val bundled: Boolean = false,
    val recommended: Boolean = false,
) {
    fun urlFor(file: String): String = "${ModelCatalog.BASE_URL}/$id/quantized/$file"
}

/**
 * Source of truth for installable models — the Android `model_catalog.py`.
 *
 * All three are Moonshine's English streaming architectures, downloaded
 * component by component from the same host the Mac's `moonshine_voice`
 * package uses. The optional `decoder_kv_with_attention.ort` (word timestamps
 * only) is deliberately left out: it would double every download for a
 * feature dictation never uses. Sizes are the summed Content-Length of each
 * component as served on 2026-09-07.
 */
object ModelCatalog {
    const val BASE_URL = "https://download.moonshine.ai/model"

    private val STREAMING_FILES = listOf(
        "adapter.ort", "cross_kv.ort", "decoder_kv.ort", "encoder.ort",
        "frontend.ort", "streaming_config.json", "tokenizer.bin",
    )

    val CATALOG: List<ModelInfo> = listOf(
        ModelInfo(
            id = "tiny-streaming-en",
            name = "Moonshine Tiny",
            arch = JNI.MOONSHINE_MODEL_ARCH_TINY_STREAMING,
            files = STREAMING_FILES,
            downloadBytes = 51_131_795,
            blurb = "Built in. Quick on any phone and works before you have downloaded anything.",
            speed = 5,
            accuracy = 2,
            bundled = true,
        ),
        ModelInfo(
            id = "small-streaming-en",
            name = "Moonshine Small",
            arch = JNI.MOONSHINE_MODEL_ARCH_SMALL_STREAMING,
            files = STREAMING_FILES,
            downloadBytes = 164_689_974,
            blurb = "Hears noticeably more than Tiny and is still fast on a recent phone. The one to get.",
            speed = 4,
            accuracy = 4,
            recommended = true,
        ),
        ModelInfo(
            id = "medium-streaming-en",
            name = "Moonshine Medium",
            arch = JNI.MOONSHINE_MODEL_ARCH_MEDIUM_STREAMING,
            files = STREAMING_FILES,
            downloadBytes = 303_329_727,
            blurb = "The model the Mac app ships with. Most accurate; wants a flagship phone.",
            speed = 2,
            accuracy = 5,
        ),
    )

    val IDS: Set<String> = CATALOG.map { it.id }.toSet()

    fun get(id: String): ModelInfo? = CATALOG.firstOrNull { it.id == id }

    fun bundled(): ModelInfo = CATALOG.first { it.bundled }
}
