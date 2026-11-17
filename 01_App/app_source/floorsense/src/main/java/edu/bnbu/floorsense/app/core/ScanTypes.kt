package edu.bnbu.floorsense.app.core

/** 一个 AP 的一次读数。ageMs 为 null 表示来源没有记录年龄（例如手工构造的测试输入）。 */
data class ApReading(
    val bssid: String,
    val rssi: Double,
    val ageMs: Long? = null,
    val freqMhz: Int? = null,
)

/**
 * 一次 Wi-Fi 扫描回调。
 *
 * fresh=false 表示系统返回了缓存结果；resultsUpdated 对应系统广播的 EXTRA_RESULTS_UPDATED。
 * 两者都为 true 才进入模型，与训练数据的筛选规则一致。
 */
data class WifiScan(
    val scanId: Int,
    val tMs: Long,
    val fresh: Boolean,
    val resultsUpdated: Boolean,
    val aps: List<ApReading>,
    /** SystemClock.elapsedRealtime()，和传感器时间戳（elapsedRealtimeNanos）同一时钟，融合用它对齐。 */
    val elapsedMs: Long = tMs,
) {
    val usable: Boolean get() = fresh && resultsUpdated
}
