package edu.bnbu.floorsense.collector

import java.io.BufferedWriter
import java.io.File

/** 线程安全的 CSV 追加写入器。多个采集线程各自持有一个。 */
class CsvWriter(file: File, header: List<String>) : java.io.Closeable {
    private val out: BufferedWriter = file.bufferedWriter(Charsets.UTF_8)
    private var closed = false
    var rows = 0L
        private set

    init {
        out.write(header.joinToString(","))
        out.newLine()
    }

    @Synchronized
    fun row(vararg values: Any?) {
        if (closed) return
        out.write(values.joinToString(",") { escape(it) })
        out.newLine()
        rows++
    }

    @Synchronized
    fun flush() {
        if (!closed) out.flush()
    }

    @Synchronized
    override fun close() {
        if (closed) return
        closed = true
        out.flush()
        out.close()
    }

    private fun escape(v: Any?): String {
        val s = when (v) {
            null -> ""
            is Float -> if (v.isNaN()) "" else v.toString()
            is Double -> if (v.isNaN()) "" else v.toString()
            else -> v.toString()
        }
        return if (s.any { it == ',' || it == '"' || it == '\n' || it == '\r' }) {
            "\"" + s.replace("\"", "\"\"") + "\""
        } else s
    }
}
