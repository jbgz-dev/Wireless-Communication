package edu.bnbu.floorsense.app.core

import java.io.File

/**
 * 读取 Collector 采集的 session 文件夹（wifi.csv、events.csv、meta.json），
 * 按时间顺序重放给 FloorTracker。手机上的回放模式和电脑上的单元测试用的是同一份代码。
 */
object SessionReplay {
    data class Event(val tMs: Long, val name: String, val value: String)

    class Session(val dir: File, val scans: List<WifiScan>, val events: List<Event>) {
        val name: String get() = dir.name
    }

    fun load(dir: File): Session {
        val wifi = Csv.read(File(dir, "wifi.csv"))
        val byScan = LinkedHashMap<Int, MutableList<Map<String, String>>>()
        for (row in wifi) byScan.getOrPut(row.getValue("scan_id").toInt()) { mutableListOf() }.add(row)
        val scans = byScan.map { (id, rows) ->
            val head = rows.first()
            WifiScan(
                scanId = id,
                tMs = head.getValue("t_wall_ms").toLong(),
                fresh = head["fresh"] == "1",
                resultsUpdated = head["results_updated"] != "0",
                aps = rows.filter { !it["bssid"].isNullOrBlank() }.map {
                    ApReading(
                        bssid = it.getValue("bssid"),
                        rssi = it["rssi"]?.toDoubleOrNull() ?: Double.NaN,
                        ageMs = it["age_ms"]?.toLongOrNull(),
                        freqMhz = it["freq_mhz"]?.toIntOrNull(),
                    )
                },
                elapsedMs = head["t_elapsed_ms"]?.toLongOrNull() ?: head.getValue("t_wall_ms").toLong(),
            )
        }.sortedBy { it.tMs }
        val eventsFile = File(dir, "events.csv")
        val events = if (!eventsFile.exists()) emptyList() else Csv.read(eventsFile).map {
            Event(it.getValue("t_wall_ms").toLong(), it.getValue("event"), it["value"].orEmpty())
        }
        return Session(dir, scans, events)
    }

    /**
     * 按 events.csv 给每个时刻标真实楼层：move_start 之前是起点楼层，move_end 之后是终点楼层，
     * 移动途中返回 null（不计入准确率）。walk/static 没有移动事件，整段都是 meta 里的楼层。
     */
    fun truthAt(session: Session, startFloor: Int, tMs: Long): Int? {
        val start = session.events.firstOrNull { it.name == "move_start" } ?: return startFloor
        val end = session.events.firstOrNull { it.name == "move_end" }
        return when {
            tMs < start.tMs -> startFloor
            end != null && tMs > end.tMs -> end.value.toIntOrNull()
            else -> null
        }
    }
}

/** 足够读 Collector 输出的 CSV 解析器：支持带引号的字段（SSID 里可能有逗号）。 */
internal object Csv {
    fun read(file: File): List<Map<String, String>> {
        val lines = parse(file.readText(Charsets.UTF_8))
        if (lines.isEmpty()) return emptyList()
        val header = lines.first()
        return lines.drop(1).filter { it.size > 1 || it.firstOrNull()?.isNotEmpty() == true }.map { cells ->
            header.indices.associate { header[it] to cells.getOrElse(it) { "" } }
        }
    }

    private fun parse(text: String): List<List<String>> {
        val rows = mutableListOf<List<String>>()
        var row = mutableListOf<String>()
        val cell = StringBuilder()
        var quoted = false
        var i = 0
        while (i < text.length) {
            val c = text[i]
            when {
                quoted && c == '"' && i + 1 < text.length && text[i + 1] == '"' -> { cell.append('"'); i++ }
                c == '"' -> quoted = !quoted
                !quoted && c == ',' -> { row.add(cell.toString()); cell.clear() }
                !quoted && (c == '\n' || c == '\r') -> {
                    if (c == '\r' && i + 1 < text.length && text[i + 1] == '\n') i++
                    row.add(cell.toString()); cell.clear()
                    rows.add(row); row = mutableListOf()
                }
                else -> cell.append(c)
            }
            i++
        }
        if (cell.isNotEmpty() || row.isNotEmpty()) { row.add(cell.toString()); rows.add(row) }
        return rows
    }
}
