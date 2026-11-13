package edu.bnbu.floorsense.app.core

import org.json.JSONObject
import kotlin.math.sqrt

/**
 * 读取 floorsense_c 导出的 knn_reference.json，复现 scikit-learn
 * KNeighborsClassifier(n_neighbors=k, weights="distance", metric="euclidean")。
 *
 * 距离加权：权重为 1/距离；只要有零距离邻居，就只让零距离邻居投票（权重 1）。
 * 第 k 和第 k+1 个邻居距离完全相同时，scikit-learn 的取舍没有保证；这里按训练样本顺序取前者。
 */
class KnnModel(
    override val config: FeatureConfig,
    private val x: Array<DoubleArray>,
    private val floors: IntArray,
    private val zones: Array<String>,
    private val k: Int,
) : FloorModel {
    private val floorClasses = floors.distinct().sorted()
    private val zoneClasses = zones.distinct().sorted()
    private val floorIdx = IntArray(floors.size) { floorClasses.indexOf(floors[it]) }
    private val zoneIdx = IntArray(zones.size) { zoneClasses.indexOf(zones[it]) }

    override val name: String = "KNN k=$k（${x.size} 条指纹，${config.size} 个 AP）"

    init {
        require(x.isNotEmpty() && x.size == floors.size && x.size == zones.size) { "训练矩阵和标签数量不一致" }
        require(x.all { it.size == config.size }) { "训练矩阵维度和 feature config 不一致" }
        require(k in 1..x.size) { "k 超出范围" }
    }

    override fun predict(x: DoubleArray): Prediction {
        require(x.size == config.size) { "输入维度 ${x.size} 和模型 ${config.size} 不一致" }
        val dist = DoubleArray(this.x.size) { i -> sqrt(squaredDistance(this.x[i], x)) }
        val nearest = dist.indices.sortedWith(compareBy<Int> { dist[it] }.thenBy { it }).take(k)
        val exact = nearest.filter { dist[it] == 0.0 }
        val voters = exact.ifEmpty { nearest }
        val weight = { i: Int -> if (exact.isEmpty()) 1.0 / dist[i] else 1.0 }
        return Prediction(
            floorClasses, vote(voters, weight, floorIdx, floorClasses.size),
            zoneClasses, vote(voters, weight, zoneIdx, zoneClasses.size),
        )
    }

    private fun vote(voters: List<Int>, weight: (Int) -> Double, labels: IntArray, n: Int): DoubleArray {
        val p = DoubleArray(n)
        for (i in voters) p[labels[i]] += weight(i)
        val total = p.sum()
        for (c in p.indices) p[c] /= total
        return p
    }

    private fun squaredDistance(a: DoubleArray, b: DoubleArray): Double {
        var s = 0.0
        for (j in a.indices) {
            val d = a[j] - b[j]
            s += d * d
        }
        return s
    }

    companion object {
        fun fromJson(text: String): KnnModel {
            val o = JSONObject(text)
            require(o.optString("weights") == "distance" && o.optString("metric") == "euclidean") {
                "只支持 weights=distance、metric=euclidean 的 KNN"
            }
            val config = FeatureConfig.fromJson(o.getJSONObject("config"))
            val rows = o.getJSONArray("x")
            val x = Array(rows.length()) { i ->
                val r = rows.getJSONArray(i)
                DoubleArray(r.length()) { r.getDouble(it) }
            }
            val f = o.getJSONArray("floor")
            val z = o.getJSONArray("zone")
            return KnnModel(
                config, x,
                IntArray(f.length()) { f.getInt(it) },
                Array(z.length()) { z.getString(it) },
                o.getInt("k"),
            )
        }
    }
}
