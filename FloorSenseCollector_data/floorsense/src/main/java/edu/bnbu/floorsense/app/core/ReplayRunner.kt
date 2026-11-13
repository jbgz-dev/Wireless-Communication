package edu.bnbu.floorsense.app.core

import org.json.JSONObject
import java.io.File

/**
 * 把一个 session 快速重放一遍，统计显示楼层和真实楼层的一致程度。
 * 真实楼层来自 meta.json 和 events.csv；移动途中（move_start 到 move_end）不计分。
 */
object ReplayRunner {
    data class Row(
        val tMs: Long,
        val truth: Int?,
        val outcome: FloorTracker.Outcome,
        val rawFloor: Int?,
        val shown: FloorTracker.State,
    )

    class Result(val session: SessionReplay.Session, val startFloor: Int, val rows: List<Row>) {
        /** 有真实楼层、且界面已经有楼层可显示的扫描。 */
        val scored: List<Row> get() = rows.filter { it.truth != null && it.shown.floor != null }
        val correct: Int get() = scored.count { it.shown.floor == it.truth }
        val accuracy: Double get() = if (scored.isEmpty()) Double.NaN else correct.toDouble() / scored.size
        val rawScored: List<Row> get() = rows.filter { it.truth != null && it.rawFloor != null }
        val rawAccuracy: Double
            get() = if (rawScored.isEmpty()) Double.NaN else rawScored.count { it.rawFloor == it.truth }.toDouble() / rawScored.size

        /** 到达后多少毫秒界面第一次显示正确楼层；没有移动或始终没显示正确时为 null。 */
        val settleMs: Long?
            get() {
                val end = session.events.firstOrNull { it.name == "move_end" } ?: return null
                val target = end.value.toIntOrNull() ?: return null
                return rows.firstOrNull { it.tMs > end.tMs && it.shown.floor == target }?.let { it.tMs - end.tMs }
            }
    }

    /**
     * useEvents=true 时把 events.csv 里的 move_start/move_end 当作跨层检测结果喂给 tracker，
     * 用来模拟“检测完全正确”的上限；false 时就是纯 Wi-Fi 基线。
     */
    fun run(dir: File, model: FloorModel, useEvents: Boolean = false): Result {
        val session = SessionReplay.load(dir)
        val startFloor = JSONObject(File(dir, "meta.json").readText()).getJSONObject("labels").getInt("floor")
        val tracker = FloorTracker(model)
        val events = if (useEvents) session.events.filter { it.name == "move_start" || it.name == "move_end" } else emptyList()
        var ei = 0
        val rows = session.scans.map { scan ->
            while (ei < events.size && events[ei].tMs <= scan.tMs) {
                if (events[ei].name == "move_start") tracker.onTransitionStart() else tracker.onTransitionEnd()
                ei++
            }
            tracker.tick(scan.tMs)
            val u = tracker.onScan(scan)
            Row(scan.tMs, SessionReplay.truthAt(session, startFloor, scan.tMs), u.outcome, u.raw?.floor, u.state)
        }
        return Result(session, startFloor, rows)
    }
}
