package edu.bnbu.floorsense.app.core

import org.json.JSONObject
import java.io.File

/**
 * 用一个动态 session（wifi.csv + sensors.csv + events.csv）按时间顺序重放 FusionEngine，
 * 和 fuse.py 一样统计：有真实楼层的扫描里，纯 Wi-Fi 平滑和融合各对了多少。
 * 移动途中（move_start..move_end）不计分；界面没有楼层（“—”）算错。
 */
object FusionReplay {
    /** fused 是界面显示（把握不够时为 null）；fusedArgmax 是概率最高的楼层，和 fuse.py 的 fused 列对应。 */
    data class Row(val elapsedMs: Long, val truth: Int?, val wifiOnly: Int?, val fused: Int?, val fusedArgmax: Int, val afterArrival: Boolean)

    class Result(
        val name: String,
        val startFloor: Int,
        val rows: List<Row>,
        val transitions: List<MotionDetector.Transition>,
    ) {
        private val scored get() = rows.filter { it.truth != null }
        val n: Int get() = scored.size
        val wifiOnlyCorrect: Int get() = scored.count { it.wifiOnly == it.truth }
        val fusedCorrect: Int get() = scored.count { it.fused == it.truth }
        val argmaxCorrect: Int get() = scored.count { it.fusedArgmax == it.truth }
        val afterArgmaxCorrect: Int get() = scored.count { it.afterArrival && it.fusedArgmax == it.truth }
        val afterN: Int get() = scored.count { it.afterArrival }
        val afterWifiOnlyCorrect: Int get() = scored.count { it.afterArrival && it.wifiOnly == it.truth }
        val afterFusedCorrect: Int get() = scored.count { it.afterArrival && it.fused == it.truth }
        val detectedDelta: Int get() = transitions.sumOf { it.delta }
        fun summary() = transitions.joinToString(";") { "${if (it.kind == MotionDetector.Kind.ELEVATOR) "电梯" else "楼梯"}%+d".format(it.delta) }
            .ifEmpty { "无" }
    }

    /**
     * knownStart=true 时用 meta 里的起点楼层初始化（相当于从走廊信号好的地方出发）。
     * lookaheadMs > 0 只用于和 Python 离线结果对照：提前喂入之后这么久的传感器数据，抵消在线检测的确认延迟。
     */
    fun run(dir: File, model: FloorModel, knownStart: Boolean = false, lookaheadMs: Long = 0, useGyro: Boolean = true): Result {
        val session = SessionReplay.load(dir)
        val startFloor = JSONObject(File(dir, "meta.json").readText()).getJSONObject("labels").getInt("floor")
        val engine = FusionEngine(model, if (knownStart) startFloor else null, useGyro)
        val wifiOnly = FloorTracker.forPolicy(model, FusionEngine.MIN_KNOWN_APS)
        val sensors = readSensors(File(dir, "sensors.csv"))
        val moveEnd = session.events.firstOrNull { it.name == "move_end" }
        var si = 0
        // fuse.py 只看 fresh 且 results_updated 的扫描，缓存结果不计分
        val rows = session.scans.filter { it.usable }.map { scan ->
            while (si < sensors.size && sensors[si].tMs <= scan.elapsedMs + lookaheadMs) {
                val s = sensors[si++]
                when (s.type) {
                    Sample.ACC -> engine.onAccelerometer(s.tMs, s.x, s.y, s.z)
                    Sample.GRAVITY -> engine.onGravity(s.tMs, s.x, s.y, s.z)
                    else -> engine.onGyroscope(s.tMs, s.x, s.y, s.z)
                }
            }
            val w = wifiOnly.onScan(scan).state.floor
            val f = engine.onScan(scan).state.floor
            Row(
                scan.elapsedMs, SessionReplay.truthAt(session, startFloor, scan.tMs), w, f, engine.fusion.floor,
                moveEnd != null && scan.tMs > moveEnd.tMs,
            )
        }
        return Result(dir.name, startFloor, rows, engine.detector.transitions.toList())
    }

    class Sample(val tMs: Double, val type: Int, val x: Double, val y: Double, val z: Double) {
        companion object { const val ACC = 0; const val GRAVITY = 1; const val GYRO = 2 }
    }

    /** sensors.csv 很大且没有引号字段，逐行切分；只取 acc、gravity 和 gyro，按时间排序。 */
    fun readSensors(file: File): List<Sample> {
        if (!file.exists()) return emptyList()
        val out = ArrayList<Sample>()
        file.bufferedReader().useLines { lines ->
            val it = lines.iterator()
            if (!it.hasNext()) return@useLines
            val h = it.next().split(',')
            val iT = h.indexOf("t_ns"); val iS = h.indexOf("sensor")
            val i0 = h.indexOf("v0"); val i1 = h.indexOf("v1"); val i2 = h.indexOf("v2")
            for (line in it) {
                val c = line.split(',')
                if (c.size <= i2) continue
                val type = when (c[iS]) { "acc" -> Sample.ACC; "gravity" -> Sample.GRAVITY; "gyro" -> Sample.GYRO; else -> continue }
                out += Sample(c[iT].toLong() / 1e6, type, c[i0].toDouble(), c[i1].toDouble(), c[i2].toDouble())
            }
        }
        out.sortBy { it.tMs }
        return out
    }
}
