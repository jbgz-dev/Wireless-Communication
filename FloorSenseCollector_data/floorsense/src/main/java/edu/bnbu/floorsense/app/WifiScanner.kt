package edu.bnbu.floorsense.app

import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.wifi.ScanResult
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.core.content.ContextCompat
import edu.bnbu.floorsense.app.core.ApReading
import edu.bnbu.floorsense.app.core.WifiScan

/**
 * 循环触发 Wi-Fi 扫描，把每次回调交给 onScan。扫描节奏和 fresh 的判断与 Collector 的 WifiCollector 相同：
 * 每 1.5 秒最多请求一次，10 秒没有回调就重新请求；结果里最新的 AP 时间戳比上一次新才算 fresh。
 * 训练数据就是按这个规则筛的，所以这里不能改。
 */
class WifiScanner(context: Context, private val onScan: (WifiScan) -> Unit) {
    private val ctx = context.applicationContext
    private val wifi = ctx.getSystemService(Context.WIFI_SERVICE) as WifiManager
    private val handler = Handler(Looper.getMainLooper())

    var requests = 0; private set
    var rejected = 0; private set

    private var running = false
    private var pending = false
    private var lastRequestMs = 0L
    private var lastNewestTsUs = 0L
    private var nextId = 0

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

    val isRunning: Boolean get() = running

    fun start() {
        if (running) return
        running = true
        // 开始前系统缓存的结果不算新扫描
        lastNewestTsUs = currentResults().maxOfOrNull { it.timestamp } ?: 0L
        ContextCompat.registerReceiver(
            ctx, receiver,
            IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION),
            ContextCompat.RECEIVER_EXPORTED,
        )
        handler.post(tick)
    }

    fun stop() {
        if (!running) return
        running = false
        pending = false
        handler.removeCallbacksAndMessages(null)
        try { ctx.unregisterReceiver(receiver) } catch (_: IllegalArgumentException) {}
    }

    @Suppress("DEPRECATION")
    private fun request() {
        lastRequestMs = SystemClock.elapsedRealtime()
        requests++
        val ok = try { wifi.startScan() } catch (_: SecurityException) { false }
        pending = ok
        if (!ok) rejected++
    }

    private fun onResults(updated: Boolean) {
        if (!running) return
        val wasPending = pending
        pending = false
        val results = currentResults()
        val newest = results.maxOfOrNull { it.timestamp } ?: 0L
        // 空结果没有时间戳可比，只要是对我们请求的回应就算新扫描（即真实的信号丢失）
        val fresh = if (results.isEmpty()) wasPending else newest > lastNewestTsUs
        if (fresh) lastNewestTsUs = newest
        val elapsedMs = SystemClock.elapsedRealtime()
        val aps = results.map {
            ApReading(
                bssid = it.BSSID ?: "",
                rssi = it.level.toDouble(),
                ageMs = elapsedMs - it.timestamp / 1000,
                freqMhz = it.frequency,
            )
        }
        onScan(WifiScan(nextId++, System.currentTimeMillis(), fresh, updated, aps))
    }

    @SuppressLint("MissingPermission")
    private fun currentResults(): List<ScanResult> =
        try { wifi.scanResults ?: emptyList() } catch (_: SecurityException) { emptyList() }

    companion object {
        private const val MIN_INTERVAL_MS = 1500L
        private const val TIMEOUT_MS = 10_000L
    }
}
