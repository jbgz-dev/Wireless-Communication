package edu.bnbu.floorsense.app.core

import org.json.JSONObject

/**
 * 把一次扫描变成模型输入向量。规则与 floorsense_c/pipeline.py 的 load_data + encode_transform 一致：
 * BSSID 转小写；丢弃 RSSI 不在 [-127, 0) 的读数和 age 超过 maxAgeMs 的 AP；
 * 先裁剪到 [clipMin, clipMax]，再对同一 BSSID 的多个读数取中位数；
 * 按 bssids 的顺序排列，没扫到的填 missingRssi，不认识的 BSSID 忽略。
 */
class FeatureConfig(
    val bssids: List<String>,
    val missingRssi: Double = -100.0,
    val clipMin: Double = -100.0,
    val clipMax: Double = -20.0,
    val maxAgeMs: Long = 10_000,
) {
    private val index = HashMap<String, Int>(bssids.size * 2).apply {
        bssids.forEachIndexed { i, b -> put(b, i) }
    }

    init {
        require(index.size == bssids.size) { "feature config 里有重复的 BSSID" }
    }

    val size: Int get() = bssids.size

    fun fingerprint(aps: List<ApReading>): Map<String, Double> {
        val values = HashMap<String, MutableList<Double>>()
        for (ap in aps) {
            if (ap.bssid.isBlank()) continue
            val r = ap.rssi
            if (!r.isFinite() || r < -127.0 || r >= 0.0) continue
            val age = ap.ageMs
            if (age != null && (age < 0 || age > maxAgeMs)) continue
            values.getOrPut(ap.bssid.lowercase()) { mutableListOf() }.add(r.coerceIn(clipMin, clipMax))
        }
        return values.mapValues { median(it.value) }
    }

    fun knownCount(fp: Map<String, Double>): Int = fp.keys.count { it in index }

    fun encode(fp: Map<String, Double>): DoubleArray {
        val x = DoubleArray(size) { missingRssi }
        for ((b, r) in fp) index[b]?.let { x[it] = r.coerceIn(clipMin, clipMax) }
        return x
    }

    companion object {
        fun fromJson(o: JSONObject): FeatureConfig {
            val arr = o.getJSONArray("bssids")
            val clip = o.optJSONArray("clip_rssi")
            return FeatureConfig(
                bssids = List(arr.length()) { arr.getString(it) },
                missingRssi = o.optDouble("missing_rssi", -100.0),
                clipMin = clip?.getDouble(0) ?: -100.0,
                clipMax = clip?.getDouble(1) ?: -20.0,
                maxAgeMs = o.optLong("max_age_ms", 10_000),
            ).also {
                val agg = o.optString("duplicate_bssid_aggregation", "median")
                require(agg == "median") { "不支持的重复 BSSID 聚合方式：$agg" }
            }
        }

        /** 与 numpy.median 相同：偶数个时取中间两个的平均。 */
        internal fun median(v: List<Double>): Double {
            val s = v.sorted()
            val m = s.size / 2
            return if (s.size % 2 == 1) s[m] else (s[m - 1] + s[m]) / 2.0
        }
    }
}
