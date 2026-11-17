package edu.bnbu.floorsense.app.core

/**
 * 一次推理的结果。proba 是投票分数，没有校准，不能当成正确概率。
 * 类别按升序排列（楼层 2..6，Zone East/Middle/West），与 scikit-learn 的 classes_ 一致，
 * 分数相同时取排在前面的类别。
 */
class Prediction(
    val floorClasses: List<Int>,
    val floorProba: DoubleArray,
    val zoneClasses: List<String>,
    val zoneProba: DoubleArray,
) {
    val floor: Int get() = floorClasses[argmax(floorProba)]
    val floorScore: Double get() = floorProba.max()
    val zone: String get() = zoneClasses[argmax(zoneProba)]
    val zoneScore: Double get() = zoneProba.max()

    companion object {
        fun argmax(p: DoubleArray): Int {
            var best = 0
            for (i in 1 until p.size) if (p[i] > p[best]) best = i
            return best
        }
    }
}

/**
 * 模型自带的实时更新门限（C 的 realtime_policy）：已知 AP ≥ minKnownAps 且楼层分数 ≥ minFloorVote 才更新楼层。
 * 门限是 C 在 D2 Validation 上定的，D3 的 70 点静态测试就是按这个规则评分的。
 */
data class RealtimePolicy(val minKnownAps: Int, val minFloorVote: Double)

/** 换模型（例如以后的 ONNX 随机森林）时只需要实现这个接口。 */
interface FloorModel {
    val name: String
    val config: FeatureConfig
    /** 没有自带门限的模型（KNN、旧 RF）返回 null，由调用方决定。 */
    val policy: RealtimePolicy? get() = null
    fun predict(x: DoubleArray): Prediction
}
