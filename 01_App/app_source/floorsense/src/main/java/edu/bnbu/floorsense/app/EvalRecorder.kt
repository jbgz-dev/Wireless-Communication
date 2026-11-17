package edu.bnbu.floorsense.app

import android.os.Build
import edu.bnbu.floorsense.app.core.FloorTracker
import edu.bnbu.floorsense.app.core.Prediction
import edu.bnbu.floorsense.app.core.WifiScan
import org.json.JSONObject
import java.io.BufferedWriter
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * 评估模式：在测试点上输入真实楼层，App 记录接下来 scansPerPoint 次可用扫描的预测。
 *
 * 每次评估写一个文件夹：
 * - wifi.csv：原始扫描，列和 Collector 的 wifi.csv 相同（缺少的列留空），以后换模型可以离线重算；
 * - predictions.csv：每次扫描的单次预测和界面显示的平滑结果；
 * - points.csv：每个测试点一行，以记录结束时界面显示的楼层作为这个点的结果；
 * - meta.json：设备、模型、开始时间。
 */
class EvalRecorder(root: File, modelName: String) {
    val dir: File = File(root, SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date()) + "_eval").apply { mkdirs() }

    private val wifi = writer("wifi.csv", "scan_id,t_wall_ms,t_elapsed_ms,fresh,n_ap,bssid,ssid,rssi,freq_mhz,channel_width,wifi_standard,age_ms,results_updated")
    private val preds = writer(
        "predictions.csv",
        "point_id,true_floor,true_zone,scan_id,t_wall_ms,outcome,known_aps,observed_aps," +
            "raw_floor,raw_floor_score,raw_zone,raw_zone_score,shown_floor,shown_floor_score,shown_zone,shown_zone_score,status",
    )
    private val points = writer("points.csv", "point_id,true_floor,true_zone,shown_floor,shown_zone,scans_used,raw_floor_correct,start_ms,end_ms")

    data class Target(val pointId: String, val floor: Int, val zone: String?)

    private var target: Target? = null
    private var used = 0
    private var rawCorrect = 0
    private var startMs = 0L
    private var lastState: FloorTracker.State? = null

    var pointsDone = 0; private set
    var pointsCorrect = 0; private set
    var rawScans = 0; private set
    var rawScansCorrect = 0; private set
    /** key = 真实楼层 to 显示楼层 */
    val confusion = sortedMapOf<Pair<Int, Int>, Int>(compareBy<Pair<Int, Int>> { it.first }.thenBy { it.second })

    val recording: Boolean get() = target != null
    val progress: Int get() = used

    init {
        File(dir, "meta.json").writeText(
            JSONObject()
                .put("model", modelName)
                .put("scans_per_point", SCANS_PER_POINT)
                .put("start_wall_ms", System.currentTimeMillis())
                .put("device", JSONObject().put("manufacturer", Build.MANUFACTURER).put("model", Build.MODEL)
                    .put("android_release", Build.VERSION.RELEASE).put("sdk_int", Build.VERSION.SDK_INT))
                .toString(2),
        )
    }

    fun begin(t: Target, nowMs: Long) {
        target = t
        used = 0
        rawCorrect = 0
        startMs = nowMs
        lastState = null
    }

    fun cancel() { target = null }

    /** 每次扫描都调用（不管是否在记录点），原始数据全部保留。返回 true 表示这个点刚记录完。 */
    fun onScan(scan: WifiScan, u: FloorTracker.Update): Boolean {
        writeWifi(scan)
        val t = target ?: return false
        writePrediction(t, scan, u)
        if (u.outcome == FloorTracker.Outcome.CACHED) return false
        lastState = u.state
        if (u.raw == null) return false
        used++
        rawScans++
        if (u.raw.floor == t.floor) { rawCorrect++; rawScansCorrect++ }
        if (used < SCANS_PER_POINT) return false
        finishPoint(t, scan.tMs)
        return true
    }

    private fun finishPoint(t: Target, endMs: Long) {
        val s = lastState
        val shown = s?.floor
        points.line(listOf(t.pointId, t.floor, t.zone, shown, s?.zone, used, rawCorrect, startMs, endMs))
        pointsDone++
        if (shown == t.floor) pointsCorrect++
        if (shown != null) confusion.merge(t.floor to shown, 1) { a, b -> a + b }
        target = null
        flush()
    }

    private fun writeWifi(scan: WifiScan) {
        val f = if (scan.fresh) 1 else 0
        val upd = if (scan.resultsUpdated) 1 else 0
        if (scan.aps.isEmpty()) wifi.line(listOf(scan.scanId, scan.tMs, scan.elapsedMs, f, 0, null, null, null, null, null, null, null, upd))
        for (ap in scan.aps) {
            wifi.line(listOf(scan.scanId, scan.tMs, scan.elapsedMs, f, scan.aps.size, ap.bssid, null, ap.rssi.toInt(), ap.freqMhz, null, null, ap.ageMs, upd))
        }
    }

    private fun writePrediction(t: Target, scan: WifiScan, u: FloorTracker.Update) {
        val r: Prediction? = u.raw
        val s = u.state
        preds.line(listOf(
            t.pointId, t.floor, t.zone, scan.scanId, scan.tMs, u.outcome, s.knownAps, s.observedAps,
            r?.floor, r?.floorScore, r?.zone, r?.zoneScore, s.floor, s.floorScore, s.zone, s.zoneScore, s.status,
        ))
    }

    fun summary(): String = buildString {
        append("已记录 $pointsDone 个点，界面楼层正确 $pointsCorrect 个")
        if (pointsDone > 0) append("（%.1f%%）".format(100.0 * pointsCorrect / pointsDone))
        append("\n单次扫描 $rawScans 次，正确 $rawScansCorrect 次")
        if (rawScans > 0) append("（%.1f%%）".format(100.0 * rawScansCorrect / rawScans))
        if (confusion.isNotEmpty()) {
            append("\n混淆（真实→显示）：")
            append(confusion.entries.joinToString("  ") { (k, v) -> "${k.first}→${k.second}:$v" })
        }
    }

    fun flush() { wifi.flush(); preds.flush(); points.flush() }

    fun close() { flush(); wifi.close(); preds.close(); points.close() }

    fun zip(): File {
        flush()
        val out = File(dir.parentFile, dir.name + ".zip")
        ZipOutputStream(out.outputStream()).use { z ->
            dir.listFiles().orEmpty().sortedBy { it.name }.forEach { f ->
                z.putNextEntry(ZipEntry("${dir.name}/${f.name}"))
                f.inputStream().use { it.copyTo(z) }
                z.closeEntry()
            }
        }
        return out
    }

    private fun writer(name: String, header: String): BufferedWriter =
        File(dir, name).bufferedWriter(Charsets.UTF_8).apply { write(header); newLine() }

    private fun BufferedWriter.line(values: List<Any?>) {
        write(values.joinToString(",") { v ->
            val s = when (v) {
                null -> ""
                is Double -> if (v.isNaN()) "" else "%.4f".format(Locale.US, v)
                else -> v.toString()
            }
            if (s.any { it == ',' || it == '"' || it == '\n' }) "\"" + s.replace("\"", "\"\"") + "\"" else s
        })
        newLine()
    }

    companion object {
        const val SCANS_PER_POINT = 5
    }
}
