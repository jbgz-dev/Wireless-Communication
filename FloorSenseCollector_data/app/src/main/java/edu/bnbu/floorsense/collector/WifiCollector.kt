package edu.bnbu.floorsense.collector

import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.wifi.ScanResult
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.core.content.ContextCompat
import java.io.File

/**
 * 循环触发 Wi-Fi 扫描并把每次结果写入 wifi.csv（每个 AP 一行）。
 *
 * fresh=1 表示这次扫描里至少有一个 AP 的时间戳比上一次新，即真正的新扫描；
 * fresh=0 表示系统返回了缓存结果（通常是扫描节流造成的），训练时应丢弃。
 * 扫描到 0 个 AP 时也写一行（bssid 为空），用来记录信号丢失。
 * results_updated 是系统广播里的 EXTRA_RESULTS_UPDATED：0 表示系统这次没有真正扫描
 * （节流时 startScan() 照样返回 true，但只把旧结果再发一遍）。
 */
class WifiCollector(
    private val context: Context,
    dir: File,
    private val mark: (String, String) -> Unit,
) {
    private val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    private val handler = Handler(Looper.getMainLooper())
    private val out = CsvWriter(
        File(dir, "wifi.csv"),
        listOf(
            "scan_id", "t_wall_ms", "t_elapsed_ms", "fresh", "n_ap",
            "bssid", "ssid", "rssi", "freq_mhz", "channel_width", "wifi_standard", "age_ms",
            "results_updated",
        ),
    )

    @Volatile var scansTotal = 0; private set
    @Volatile var scansFresh = 0; private set
    @Volatile var lastApCount = 0; private set
    @Volatile var rejected = 0; private set
    @Volatile var requests = 0; private set
    /** 系统回了广播但 EXTRA_RESULTS_UPDATED=false 的次数。 */
    @Volatile var notUpdated = 0; private set
    private var firstFreshMs = 0L
    private var lastFreshMs = 0L

    /** 相邻两次新扫描的平均间隔（秒），少于 2 次新扫描时为 NaN。 */
    val meanFreshIntervalSec: Double
        get() = if (scansFresh < 2) Double.NaN else (lastFreshMs - firstFreshMs) / 1000.0 / (scansFresh - 1)

    private var running = false
    private var pending = false
    private var lastRequestMs = 0L
    private var lastNewestTsUs = 0L

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) =
            onResults(intent.getBooleanExtra(WifiManager.EXTRA_RESULTS_UPDATED, true))
    }

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            val now = SystemClock.elapsedRealtime()
            val idle = !pending && now - lastRequestMs >= MIN_INTERVAL_MS
            val stuck = pending && now - lastRequestMs >= TIMEOUT_MS
            if (idle || stuck) request()
            handler.postDelayed(this, 500)
        }
    }

    fun start() {
        running = true
        // 会话开始前系统缓存的结果不算新扫描
        lastNewestTsUs = currentResults().maxOfOrNull { it.timestamp } ?: 0L
        ContextCompat.registerReceiver(
            context, receiver,
            IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION),
            ContextCompat.RECEIVER_EXPORTED,
        )
        handler.post(tick)
    }

    fun stop() {
        running = false
        handler.removeCallbacksAndMessages(null)
        try { context.unregisterReceiver(receiver) } catch (_: IllegalArgumentException) {}
        out.close()
    }

    @Suppress("DEPRECATION")
    private fun request() {
        lastRequestMs = SystemClock.elapsedRealtime()
        requests++
        val ok = try { wifi.startScan() } catch (_: SecurityException) { false }
        pending = ok
        if (!ok) {
            rejected++
            mark("wifi_scan_rejected", rejected.toString())
        }
    }

    @SuppressLint("MissingPermission")
    private fun onResults(updated: Boolean) {
        if (!running) return
        if (!updated) notUpdated++
        val wasPending = pending
        pending = false
        val results = currentResults()
        val wall = System.currentTimeMillis()
        val elapsedMs = SystemClock.elapsedRealtime()
        val newest = results.maxOfOrNull { it.timestamp } ?: 0L
        // 空结果没有时间戳可比，只要是对我们请求的回应就算新扫描（即真实的信号丢失）
        val fresh = if (results.isEmpty()) wasPending else newest > lastNewestTsUs
        if (fresh) lastNewestTsUs = newest

        val id = scansTotal++
        if (fresh) {
            scansFresh++
            if (firstFreshMs == 0L) firstFreshMs = elapsedMs
            lastFreshMs = elapsedMs
        }
        lastApCount = results.size
        val f = if (fresh) 1 else 0
        val u = if (updated) 1 else 0

        if (results.isEmpty()) {
            out.row(id, wall, elapsedMs, f, 0, null, null, null, null, null, null, null, u)
        }
        for (r in results) {
            out.row(
                id, wall, elapsedMs, f, results.size,
                r.BSSID, ssidOf(r), r.level, r.frequency, r.channelWidth,
                if (Build.VERSION.SDK_INT >= 30) r.wifiStandard else null,
                elapsedMs - r.timestamp / 1000, u,
            )
        }
        out.flush()
    }

    /**
     * 判断扫描慢是系统限制还是 App 的问题。App 每 1.5 秒请求一次扫描，
     * 不节流时一次完整扫描（2.4G + 5G）通常 3–8 秒。
     */
    fun diagnosis(): String {
        if (requests < 6) return "Wi-Fi 诊断：数据还不够"
        val interval = meanFreshIntervalSec
        val iv = if (interval.isNaN()) "—" else "%.1f 秒".format(interval)
        val head = "Wi-Fi 诊断：App 请求 $requests 次，系统拒绝 $rejected 次，回旧结果 $notUpdated 次，新扫描间隔 $iv"
        return head + "→ " + when {
            rejected * 2 > requests -> "系统限制：大部分扫描请求被系统直接拒绝（扫描节流）"
            notUpdated * 2 > requests -> "系统限制：系统接受了请求，但只返回旧结果（扫描节流或厂商省电策略）"
            !interval.isNaN() && interval > 12 -> "系统限制：每次新扫描间隔超过 12 秒，比正常的 3–8 秒慢很多"
            else -> "正常"
        }
    }

    @SuppressLint("MissingPermission")
    private fun currentResults(): List<ScanResult> =
        try { wifi.scanResults ?: emptyList() } catch (_: SecurityException) { emptyList() }

    @Suppress("DEPRECATION")
    private fun ssidOf(r: ScanResult): String {
        val s = if (Build.VERSION.SDK_INT >= 33) r.wifiSsid?.toString() ?: "" else r.SSID ?: ""
        return s.removeSurrounding("\"")
    }

    companion object {
        private const val MIN_INTERVAL_MS = 1500L
        private const val TIMEOUT_MS = 10_000L
    }
}
