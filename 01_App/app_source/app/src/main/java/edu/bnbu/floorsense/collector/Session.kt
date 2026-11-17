package edu.bnbu.floorsense.collector

import android.content.Context
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/** 界面上填写的标签。每次采集会原样写进 meta.json。 */
data class Form(
    val collector: String,
    val building: String,
    val floor: Int,
    val zone: String,
    val pointId: String,
    val split: String,        // train / test
    val mode: String,         // static / walk / transition
    val orientation: String,  // N / E / S / W / free
    val posture: String,      // hand_flat / hand_upright / call / pocket / bag
    val targetScans: Int,
    val toFloor: Int?,        // 仅 transition
    val method: String,       // 仅 transition：stairs / elevator / escalator
    val notes: String,
    val reverseWalk: Boolean = false,
) {
    fun validate(): String? = when {
        collector.isBlank() -> "请填写采集人代号"
        building.isBlank() -> "请填写楼名"
        mode != "transition" && pointId.isBlank() -> "请填写点位编号"
        mode == "static" && targetScans < 1 -> "目标扫描次数至少为 1"
        mode == "transition" && toFloor == null -> "请填写目标楼层"
        mode == "transition" && toFloor == floor -> "目标楼层不能和起始楼层相同"
        else -> null
    }

    /** 防止数据泄漏用的点位键：同一个键只能属于一个 split。 */
    fun pointKey(): String? = if (mode == "transition") null else "$building|$floor|$pointId"
}

/** 一次采集会话：一个目录，里面是 meta.json 和各类 CSV。 */
class Session(private val ctx: Context, val form: Form, val dir: File, val walk: WalkProgress? = null) {
    private val startWall = System.currentTimeMillis()
    private val startElapsed = SystemClock.elapsedRealtime()
    private val envAtStart = Env.snapshot(ctx)

    private val events = CsvWriter(File(dir, "events.csv"), listOf("t_wall_ms", "t_elapsed_ms", "event", "value"))
    private val nodeMarks = walk?.let { CsvWriter(File(dir, "node_marks.csv"), listOf("action") + NODE_FIELDS) }
    val wifi = WifiCollector(ctx, dir, ::mark)
    val ble = BleCollector(ctx, dir, ::mark)
    val cell = CellCollector(ctx, dir, ::mark)
    val sensors = SensorCollector(ctx, dir)

    val elapsedSec: Long get() = (SystemClock.elapsedRealtime() - startElapsed) / 1000

    fun start() {
        walk?.let { w ->
            val nodes = JSONArray()
            w.nodes.forEachIndexed { i, n -> nodes.put(JSONObject()
                .put("node_index", i + 1).put("id", n.id).put("route_order", n.order)
                .put("x_m", n.xM ?: JSONObject.NULL).put("y_m", n.yM ?: JSONObject.NULL)) }
            File(dir, "route_snapshot.json").writeText(JSONObject()
                .put("schema_version", 1).put("session_id", dir.name)
                .put("building", form.building).put("floor", form.floor).put("route_id", form.pointId)
                .put("direction", if (w.reverse) "reverse" else "forward").put("nodes", nodes).toString(2))
        }
        writeMeta("recording")
        mark("session_start", form.mode)
        if (walk != null) recordNextNode() // 开始按钮同时记录本次方向的起点。
        sensors.start()
        wifi.start()
        ble.start()
        cell.start()
        if (form.mode == "transition") mark("still_before_start", form.floor.toString())
    }

    @Synchronized
    fun mark(event: String, value: String) {
        events.row(System.currentTimeMillis(), SystemClock.elapsedRealtime(), event, value)
        events.flush()
    }

    fun recordNextNode(): NodeVisit? {
        val visit = walk?.recordNext(System.currentTimeMillis(), SystemClock.elapsedRealtime()) ?: return null
        saveNodeAction("reached", visit, visit.wallMs, visit.elapsedMs)
        return visit
    }

    fun undoNode(): NodeVisit? {
        val wall = System.currentTimeMillis()
        val elapsed = SystemClock.elapsedRealtime()
        val visit = walk?.undo(elapsed) ?: return null
        saveNodeAction("undo", visit, wall, elapsed)
        return visit
    }

    private fun nodeValues(v: NodeVisit, wall: Long = v.wallMs, elapsed: Long = v.elapsedMs): Array<Any?> = arrayOf(
        dir.name, form.building, form.floor, form.pointId, if (walk?.reverse == true) "reverse" else "forward",
        v.markId, v.nodeIndex, v.node.id, v.node.order, v.node.xM, v.node.yM,
        wall, elapsed, if (v.automatic) "auto_start" else "manual",
    )

    private fun saveNodeAction(action: String, v: NodeVisit, wall: Long, elapsed: Long) {
        nodeMarks?.row(action, *nodeValues(v, wall, elapsed))
        nodeMarks?.flush()
        // 与 node_marks.csv 使用完全相同的事件时刻；undo 按 mark_id 关联原标记。
        events.row(wall, elapsed, if (action == "reached") "node_reached" else "node_undo",
            JSONObject().put("mark_id", v.markId).put("node_id", v.node.id)
                .put("node_index", v.nodeIndex).put("route_id", form.pointId).toString())
        events.flush()
        // 有效节点表可直接交给模型组使用；撤销的行仅保留在审计表中。
        val tmp = File(dir, "node_visits.csv.tmp")
        CsvWriter(tmp, NODE_FIELDS).use { out -> walk!!.visits.forEach { out.row(*nodeValues(it)) } }
        val target = File(dir, "node_visits.csv")
        if (!tmp.renameTo(target)) { target.writeText(tmp.readText()); tmp.delete() }
        writeMeta("recording")
    }

    /** complete=false 表示中途手动停止，处理脚本默认会跳过。 */
    fun stop(complete: Boolean) {
        mark("session_stop", if (complete) "complete" else "incomplete")
        wifi.stop()
        ble.stop()
        cell.stop()
        sensors.stop()
        nodeMarks?.close()
        events.close()
        writeMeta(if (complete) "complete" else "incomplete")
    }

    private fun writeMeta(status: String) {
        val now = System.currentTimeMillis()
        val labels = JSONObject()
            .put("collector", form.collector)
            .put("building", form.building)
            .put("floor", form.floor)
            .put("zone", form.zone)
            .put("point_id", form.pointId)
            .put("split", form.split)
            .put("mode", form.mode)
            .put("orientation", form.orientation)
            .put("posture", form.posture)
            .put("target_scans", form.targetScans)
            .put("notes", form.notes)
        if (form.mode == "transition") {
            labels.put("to_floor", form.toFloor).put("method", form.method)
        }
        if (walk != null) labels.put("walk_direction", if (walk.reverse) "reverse" else "forward")
        val stats = JSONObject()
            .put("duration_s", (now - startWall) / 1000.0)
            .put("wifi_scans", wifi.scansTotal)
            .put("wifi_scans_fresh", wifi.scansFresh)
            .put("wifi_scan_rejected", wifi.rejected)
            .put("wifi_scan_requests", wifi.requests)
            .put("wifi_results_not_updated", wifi.notUpdated)
            .put("wifi_mean_fresh_interval_s", wifi.meanFreshIntervalSec.let { if (it.isNaN()) JSONObject.NULL else it })
            .put("ble_packets", ble.packets)
            .put("ble_unique_devices", ble.uniqueDevices)
            .put("cell_samples", cell.samples)
            .put("sensor_events", sensors.events)
            .put("mean_azimuth_deg", sensors.meanAzimuthDeg().let { if (it.isNaN()) JSONObject.NULL else it })
        val meta = JSONObject()
            .put("schema_version", 1)
            .put("session_id", dir.name)
            .put("status", status)
            .put("start_wall_ms", startWall)
            .put("start_local", fmtLocal(startWall))
            .put("timezone", TimeZone.getDefault().id)
            .put("end_wall_ms", if (status == "recording") JSONObject.NULL else now)
            .put("labels", labels)
            .put("device", Env.device())
            .put("env_at_start", envAtStart.toJson())
            .put("stats", stats)
        walk?.let { w -> meta.put("walk", JSONObject()
            .put("route_id", form.pointId).put("direction", if (w.reverse) "reverse" else "forward")
            .put("nodes_expected", w.nodes.size).put("nodes_recorded", w.reachedCount)
            .put("route_complete", w.complete).put("wifi_sufficient", wifi.scansFresh >= 5)) }
        File(dir, "meta.json").writeText(meta.toString(2))
    }

    companion object {
        private val NODE_FIELDS = listOf("session_id", "building", "floor", "route_id", "direction",
            "mark_id", "node_index", "node_id", "route_order", "x_m", "y_m",
            "t_wall_ms", "t_elapsed_ms", "source")
        fun fmtLocal(ms: Long): String =
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).format(Date(ms))

        fun newDir(ctx: Context, f: Form): File {
            val ts = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
            val point = if (f.mode == "transition") "to${f.toFloor}_${f.method}" else f.pointId
            val name = listOf(ts, f.building, "F${f.floor}", f.mode, point, f.collector)
                .joinToString("_") { safe(it) }
            val root = SessionStore.root(ctx)
            var dir = File(root, name)
            var suffix = 2
            while (dir.exists()) dir = File(root, "${name}_${suffix++}")
            check(dir.mkdirs()) { "无法创建采集目录" }
            return dir
        }

        private fun safe(s: String) = s.trim().replace(Regex("[^A-Za-z0-9-]+"), "-").ifEmpty { "x" }
    }
}
