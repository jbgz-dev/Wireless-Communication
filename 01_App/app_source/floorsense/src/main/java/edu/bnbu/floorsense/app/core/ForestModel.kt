package edu.bnbu.floorsense.app.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.InputStream
import java.util.zip.GZIPInputStream

/**
 * 随机森林推理，读两种格式：
 * - C 交付的冻结版 V1（wifi_model.json.gz，schema_version 1，floorsense_c/d2_validation/D_handoff/realtime）；
 * - tools/export_rf.py 导出的 rf_model.json（format floorsense-rf-1，D2 时的 RF，现在只用于单元测试）。
 *
 * - 每棵树：x[feature] <= threshold 走左边，否则走右边，到叶子取类别比例（叶子先归一化）；
 * - 森林的分数是所有树叶子比例的平均（和 predict_proba 一样）。
 * floorsense-rf-1 和 scikit-learn 一样先把输入舍入到 float32；V1 的参考实现 mobile_reference.py 用双精度，
 * 这里跟着各自的参考实现走。RSSI 是整数或 .5，两种做法结果相同。
 */
class ForestModel(
    override val name: String,
    override val config: FeatureConfig,
    private val floor: Forest,
    private val zone: Forest,
    override val policy: RealtimePolicy? = null,
    private val roundToFloat: Boolean = true,
) : FloorModel {

    class Tree(
        private val feature: IntArray,
        private val threshold: DoubleArray,
        private val left: IntArray,
        private val right: IntArray,
        private val value: Array<DoubleArray?>,
    ) {
        fun leaf(x: DoubleArray): DoubleArray {
            var n = 0
            while (feature[n] >= 0) n = if (x[feature[n]] <= threshold[n]) left[n] else right[n]
            return value[n]!!
        }
    }

    class Forest(val classes: List<String>, private val trees: List<Tree>) {
        val size: Int get() = trees.size
        fun proba(x: DoubleArray): DoubleArray {
            val sum = DoubleArray(classes.size)
            for (t in trees) {
                val leaf = t.leaf(x)
                for (i in leaf.indices) sum[i] = sum[i] + leaf[i]
            }
            val n = trees.size.toDouble()
            return DoubleArray(sum.size) { sum[it] / n }
        }
    }

    override fun predict(x: DoubleArray): Prediction {
        val xf = if (roundToFloat) DoubleArray(x.size) { x[it].toFloat().toDouble() } else x
        return Prediction(floor.classes.map { it.toInt() }, floor.proba(xf), zone.classes, zone.proba(xf))
    }

    companion object {
        /**
         * 按内容判断是否 gzip（开头 1f 8b），不看文件名：
         * 打包 APK 时 aapt 会把 assets 里的 .gz 自动解压并去掉后缀，所以手机上读到的是 wifi_model.json。
         */
        fun load(input: InputStream): ForestModel {
            val b = java.io.BufferedInputStream(input)
            b.mark(2)
            val gz = b.read() == 0x1f && b.read() == 0x8b
            b.reset()
            val stream: InputStream = if (gz) GZIPInputStream(b) else b
            return fromJson(stream.bufferedReader().use { it.readText() })
        }

        fun fromJson(text: String): ForestModel {
            val o = JSONObject(text)
            if (o.optInt("schema_version", 0) == 1 && !o.has("format")) return fromV1(o)
            require(o.optString("format") == "floorsense-rf-1") { "不是 floorsense-rf-1 格式" }
            val config = FeatureConfig.fromJson(o.getJSONObject("config"))
            val models = o.getJSONObject("models")
            val floor = forest(models.getJSONObject("floor"))
            val zone = forest(models.getJSONObject("zone"))
            return ForestModel("RF ×300（${o.getInt("train_scans")} 条指纹，${config.bssids.size} 个 AP）", config, floor, zone)
        }

        private fun forest(m: JSONObject): Forest {
            val classes = m.getJSONArray("classes").let { a -> List(a.length()) { a.get(it).toString() } }
            val trees = m.getJSONArray("trees")
            return Forest(classes, List(trees.length()) { tree(trees.getJSONObject(it)) })
        }

        private fun tree(t: JSONObject): Tree {
            val feature = ints(t.getJSONArray("feature"))
            val th = t.getJSONArray("threshold")
            val values = t.getJSONArray("value")
            return Tree(
                feature,
                DoubleArray(th.length()) { th.getDouble(it) },
                ints(t.getJSONArray("left")),
                ints(t.getJSONArray("right")),
                Array(values.length()) { i ->
                    val v = values.getJSONArray(i)
                    if (v.length() == 0) null else DoubleArray(v.length()) { v.getDouble(it) }
                },
            )
        }

        /** C 的 V1：树里是 left/right/feature/threshold，叶子在 leaves{"结点号": [各类别计数或比例]}。 */
        private fun fromV1(o: JSONObject): ForestModel {
            val config = FeatureConfig.fromJson(o.getJSONObject("config"))
            val models = o.getJSONObject("models")
            fun v1Forest(m: JSONObject): Forest {
                require(m.getString("kind") == "random_forest") { "V1 里不是随机森林：${m.getString("kind")}" }
                val classes = m.getJSONArray("classes").let { a -> List(a.length()) { a.get(it).toString() } }
                val trees = m.getJSONArray("trees")
                return Forest(classes, List(trees.length()) { i ->
                    val t = trees.getJSONObject(i)
                    val left = ints(t.getJSONArray("left"))
                    val leaves = t.getJSONObject("leaves")
                    val feature = ints(t.getJSONArray("feature"))
                    // 叶子结点 left = -1；sklearn 导出的叶子 feature 是 -2，这里统一成负数
                    for (n in left.indices) if (left[n] == -1) feature[n] = -1
                    val th = t.getJSONArray("threshold")
                    Tree(
                        feature,
                        DoubleArray(th.length()) { th.getDouble(it) },
                        left,
                        ints(t.getJSONArray("right")),
                        Array(left.size) { n ->
                            if (left[n] != -1) null else {
                                val v = leaves.getJSONArray(n.toString())
                                val raw = DoubleArray(v.length()) { v.getDouble(it) }
                                val total = raw.sum()
                                DoubleArray(raw.size) { raw[it] / total }
                            }
                        },
                    )
                })
            }
            val floor = v1Forest(models.getJSONObject("floor"))
            val zone = v1Forest(models.getJSONObject("zone"))
            val rp = o.optJSONObject("realtime_policy")
            val policy = rp?.let { RealtimePolicy(it.getInt("min_known_aps"), it.getDouble("min_floor_vote_score")) }
            val label = o.optString("name").ifEmpty { "V1" }
            return ForestModel(
                "C 冻结版 V1（$label，RF ×${floor.size}，${config.bssids.size} 个 AP）", config, floor, zone, policy, roundToFloat = false,
            )
        }

        private fun ints(a: JSONArray) = IntArray(a.length()) { a.getInt(it) }
    }
}
