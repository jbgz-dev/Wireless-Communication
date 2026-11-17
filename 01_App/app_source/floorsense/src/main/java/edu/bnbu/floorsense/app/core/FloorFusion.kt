package edu.bnbu.floorsense.app.core

import kotlin.math.pow

/**
 * Wi-Fi + 运动传感器融合：楼层 2..6 上的离散贝叶斯滤波，和 validation_d2/dynamic/fuse.py 的 run() 相同。
 *
 * - 每个新的（不重复的）Wi-Fi 指纹：p ← p · (proba + EPS)^BETA，归一化后再混入 LEAK 的均匀分布；
 * - 检测到跨层（MotionDetector 输出 t0..t1、层数 delta）：回到 t0 之前的概率，按 delta 平移
 *   （电梯 0.9 / ±1 层各 0.05；楼梯 0.7 / 没换层 0.2 / 反方向 0.1），移动途中 t0..t1 的 Wi-Fi 丢弃，
 *   t1 之后的 Wi-Fi 重新乘进去。
 *
 * 在线检测要等事件结束约 3 秒才能确认，所以这里保留最近 [LOG_MS] 毫秒的 Wi-Fi 观测，确认时重算，
 * 结果和 Python 离线一次算完的一致。
 */
class FloorFusion(val floors: List<Int> = listOf(2, 3, 4, 5, 6), startFloor: Int? = null) {

    private class Obs(val tMs: Double, val proba: DoubleArray)

    private var base: DoubleArray = init(startFloor)
    private var baseUntilMs = Double.NEGATIVE_INFINITY
    private val log = ArrayDeque<Obs>()

    var p: DoubleArray = base.copyOf()
        private set

    val floor: Int get() = floors[Prediction.argmax(p)]
    val confidence: Double get() = p.max()

    /** 有足够把握时才显示楼层，否则返回 null（界面显示“—”）。均匀分布取最大值会落在 2F，那不是判断。 */
    val shownFloor: Int? get() = if (confidence >= SHOW_MIN) floor else null

    private fun init(start: Int?): DoubleArray {
        if (start == null || start !in floors) return DoubleArray(floors.size) { 1.0 / floors.size }
        return DoubleArray(floors.size) { if (floors[it] == start) 0.96 else 0.01 }
    }

    /** proba 的类别顺序必须和 floors 一样（RF 的 floor classes 是 2..6）。 */
    fun onWifi(tMs: Double, floorClasses: List<Int>, proba: DoubleArray) {
        require(floorClasses == floors) { "楼层类别不一致：$floorClasses" }
        log.addLast(Obs(tMs, proba.copyOf()))
        p = update(p, proba)
        while (log.isNotEmpty() && log.first().tMs < tMs - LOG_MS) {
            val o = log.removeFirst()
            base = update(base, o.proba)
            baseUntilMs = o.tMs
        }
    }

    fun onTransition(t: MotionDetector.Transition) {
        // t0 之前的状态
        var q = base.copyOf()
        val before = log.filter { it.tMs < t.t0Ms }
        for (o in before) q = update(q, o.proba)
        q = when (t.kind) {
            MotionDetector.Kind.ELEVATOR -> shift(q, listOf(t.delta to 0.9, t.delta - 1 to 0.05, t.delta + 1 to 0.05))
            MotionDetector.Kind.STAIRS -> shift(q, listOf(t.delta to 0.7, 0 to 0.2, -t.delta to 0.1))
        }
        val after = log.filter { it.tMs > t.t1Ms }
        base = q
        baseUntilMs = t.t1Ms
        log.clear()
        log.addAll(after)
        p = base.copyOf()
        for (o in log) p = update(p, o.proba)
    }

    private fun update(p0: DoubleArray, proba: DoubleArray): DoubleArray {
        val q = DoubleArray(p0.size) { p0[it] * (proba[it] + EPS).pow(BETA) }
        val s = q.sum()
        return DoubleArray(q.size) { (1 - LEAK) * q[it] / s + LEAK / q.size }
    }

    private fun shift(p0: DoubleArray, spread: List<Pair<Int, Double>>): DoubleArray {
        val q = DoubleArray(p0.size)
        for ((delta, w) in spread) {
            for (i in p0.indices) {
                val j = i + delta
                if (j in q.indices) q[j] += w * p0[i]
            }
        }
        val s = q.sum()
        return if (s > 0) DoubleArray(q.size) { q[it] / s } else DoubleArray(q.size) { 1.0 / q.size }
    }

    companion object {
        const val BETA = 0.5
        const val EPS = 0.02
        const val LEAK = 0.02
        const val SHOW_MIN = 0.5
        const val LOG_MS = 150_000.0
    }
}
