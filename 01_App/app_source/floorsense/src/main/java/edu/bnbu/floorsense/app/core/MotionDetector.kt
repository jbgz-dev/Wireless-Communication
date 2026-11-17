package edu.bnbu.floorsense.app.core

import kotlin.math.sqrt

/**
 * E 的跨层检测，在线版。算法和阈值与 validation_d2/dynamic/detect.py、fuse.py 的 events() 相同，
 * 用加速度计、gravity 和陀螺仪，不用气压计。
 *
 * - 竖直加速度 a_v = 加速度在 gravity 方向上的投影 − |g|，重采样到 25 Hz；
 * - 电梯：启动脉冲和方向相反的停止脉冲配对，中间二次积分得到位移，位移 / 4.0 m 取整得到层数；
 * - 楼梯（有陀螺仪时，默认）：T8 的楼梯每层都有平台转身。持续 5–60 s 的行走段里，有一次 ≥ 90° 的转身、
 *   竖直轴净转角 ≥ 150° 才算楼梯；逆时针（从上往下看）是上楼，顺时针是下楼。走廊是直的，不会被当成楼梯；
 * - 楼梯（没有陀螺仪时，D2 的旧规则）：持续 5–30 s 的行走段，竖直加速度标准差 > 1.45 判为下楼，否则上楼。
 *   这个规则换人后会失效（P1 上楼的标准差 1.98–2.82），只在手机没有陀螺仪时用。
 *
 * Python 版是整段数据离线算的。这里保留最近 [BUFFER_S] 秒，每 0.25 秒重算一次，
 * 只输出结束时间早于当前 [LAG_S] 秒的事件：居中滑动窗口最宽 ±1 s，再加上标准差窗口 ±0.5 s，
 * 1.5 s 之后这些窗口的值就和离线计算一样了。所以到达后约 1.5–1.75 秒确认一次跨层。
 * 时间统一用 SystemClock.elapsedRealtime 毫秒（传感器时间戳 / 1e6）。
 */
class MotionDetector(private val useGyro: Boolean = true) {
    enum class Kind { ELEVATOR, STAIRS }

    data class Transition(
        val kind: Kind,
        val delta: Int,
        val t0Ms: Double,
        val t1Ms: Double,
        val dispM: Double? = null,
        val std: Double? = null,
        /** 楼梯段在竖直轴上的净转角（度，逆时针为正），只有陀螺仪规则才有。 */
        val turnDeg: Double? = null,
    )

    private val acc = ArrayDeque<DoubleArray>()
    private val grav = ArrayDeque<DoubleArray>()
    private val gyro = ArrayDeque<DoubleArray>()
    private val yr = ArrayList<Double>()
    private var gridStart = Double.NaN
    private var nextK = 0L
    private val gt = ArrayList<Double>()
    private val av = ArrayList<Double>()
    private var lastRunMs = Double.NEGATIVE_INFINITY
    private var emittedUntilMs = Double.NEGATIVE_INFINITY
    private var trimmed = false

    /** 已经输出的全部事件，界面和回放统计用。 */
    val transitions = mutableListOf<Transition>()

    fun onAccelerometer(tMs: Double, x: Double, y: Double, z: Double): List<Transition> {
        acc.addLast(doubleArrayOf(tMs, x, y, z))
        return advance()
    }

    fun onGravity(tMs: Double, x: Double, y: Double, z: Double): List<Transition> {
        grav.addLast(doubleArrayOf(tMs, x, y, z))
        return advance()
    }

    /** 陀螺仪（rad/s）。useGyro=false 时忽略。 */
    fun onGyroscope(tMs: Double, x: Double, y: Double, z: Double): List<Transition> {
        if (!useGyro) return emptyList()
        gyro.addLast(doubleArrayOf(tMs, x, y, z))
        return advance()
    }

    private fun advance(): List<Transition> {
        if (acc.isEmpty() || grav.isEmpty() || (useGyro && gyro.isEmpty())) return emptyList()
        if (gridStart.isNaN()) gridStart = maxOf(acc.first()[0], grav.first()[0])
        val out = mutableListOf<Transition>()
        while (true) {
            val t = gridStart + nextK * STEP_MS
            if (acc.last()[0] < t || grav.last()[0] < t || (useGyro && gyro.last()[0] < t)) break
            val a = interp(acc, t)
            val g = interp(grav, t)
            val gn = sqrt(g[0] * g[0] + g[1] * g[1] + g[2] * g[2])
            gt.add(t)
            av.add((a[0] * g[0] + a[1] * g[1] + a[2] * g[2]) / gn - gn)
            if (useGyro) {
                // 竖直轴角速度（度/秒，逆时针为正）= 陀螺仪在 gravity 方向上的投影
                val w = interp(gyro, t)
                yr.add(Math.toDegrees((w[0] * g[0] + w[1] * g[1] + w[2] * g[2]) / gn))
            }
            nextK++
            if (t - lastRunMs >= RUN_EVERY_MS) {
                lastRunMs = t
                trim()
                out += analyze()
            }
        }
        return out
    }

    /** 和 np.interp 一样做线性插值；调用前保证队列里最后一个样本不早于 t。 */
    private fun interp(q: ArrayDeque<DoubleArray>, t: Double): DoubleArray {
        while (q.size >= 2 && q[1][0] <= t) q.removeFirst()
        val p0 = q[0]
        if (q.size == 1 || p0[0] >= t) return doubleArrayOf(p0[1], p0[2], p0[3])
        val p1 = q[1]
        val w = (t - p0[0]) / (p1[0] - p0[0])
        return DoubleArray(3) { p0[it + 1] + w * (p1[it + 1] - p0[it + 1]) }
    }

    private fun trim() {
        val keep = (BUFFER_S * HZ).toInt()
        if (gt.size > keep) {
            val drop = gt.size - keep
            gt.subList(0, drop).clear()
            av.subList(0, drop).clear()
            if (useGyro) yr.subList(0, drop).clear()
            trimmed = true
        }
    }

    private fun analyze(): List<Transition> {
        val n = gt.size
        if (n < 2 * HZ) return emptyList()
        val t = gt.toDoubleArray()
        val aV = av.toDoubleArray()
        val rawStd = rollingStd(aV, HZ)
        val quiet = aV.indices.filter { rawStd[it] < 0.15 }
        val bias = if (quiet.size > HZ) median(DoubleArray(quiet.size) { aV[quiet[it]] }) else median(aV)
        val a = rollingMean(DoubleArray(n) { aV[it] - bias }, HZ / 2)
        val finalBefore = n - (LAG_S * HZ).toInt()

        val found = mutableListOf<Transition>()
        // 电梯
        data class Pulse(val i: Int, val j: Int, val sign: Int)
        val pulses = mutableListOf<Pulse>()
        for (sign in intArrayOf(1, -1)) {
            for ((i, j) in runs(BooleanArray(n) { sign * a[it] > PULSE_A && rawStd[it] < HAND_STD_MAX + 0.25 })) {
                if ((j - i).toDouble() / HZ >= PULSE_MIN_S && median(rawStd.copyOfRange(i, j)) < HAND_STD_MAX) {
                    pulses += Pulse(i, j, sign)
                }
            }
        }
        pulses.sortWith(compareBy<Pulse> { it.i }.thenBy { it.j }.thenBy { it.sign })
        var k = 0
        while (k < pulses.size - 1) {
            val p0 = pulses[k]
            val p1 = pulses[k + 1]
            val gap = (p1.i - p0.j).toDouble() / HZ
            if (p1.sign == -p0.sign && gap <= MAX_RIDE_GAP_S) {
                if (p1.j <= finalBefore) {
                    val from = maxOf(p0.i - HZ / 2, 0)
                    val to = minOf(p1.j + HZ / 2, n)
                    val v = DoubleArray(to - from)
                    var s = 0.0
                    for (m in v.indices) { s += a[from + m]; v[m] = s / HZ }
                    val last = v.last()
                    var disp = 0.0
                    for (m in v.indices) {
                        // np.linspace(0, v[-1], len)：车厢两端都是静止的
                        val lin = if (v.size == 1) 0.0 else last * m / (v.size - 1)
                        disp += v[m] - lin
                    }
                    disp /= HZ
                    val delta = Math.rint(disp / FLOOR_H).toInt()  // 和 numpy round 一样，.5 取偶数
                    if (delta != 0) {
                        found += Transition(Kind.ELEVATOR, delta, t[p0.i], t[minOf(p1.j, n - 1)], dispM = disp)
                    }
                }
                k += 2
            } else {
                k += 1
            }
        }
        // 楼梯
        val yaw = if (useGyro) yr.toDoubleArray() else null
        val yawSmooth = yaw?.let { rollingMean(it, HZ) }
        val moving = rollingMean(DoubleArray(n) { if (rawStd[it] > 0.5) 1.0 else 0.0 }, 2 * HZ)
        for ((i, j) in runs(BooleanArray(n) { moving[it] > 0.5 })) {
            if (j > finalBefore || (i == 0 && trimmed)) continue
            val dur = (j - i).toDouble() / HZ
            val std = std0(aV, i, j)
            if (yaw == null || yawSmooth == null) {
                if (dur < MIN_BOUT_S || dur < STAIRS_MIN_S || dur > STAIRS_MAX_S) continue
                found += Transition(Kind.STAIRS, if (std > STAIRS_DOWN_STD) -1 else 1, t[i], t[j - 1], std = std)
                continue
            }
            // detect.py 的 stairs_by_turns：行走段取 t0..t1（含 t1 对应的样本 j-1）
            if (dur < MIN_BOUT_S || dur < STAIRS_MIN_S || dur > STAIRS_GYRO_MAX_S) continue
            var net = 0.0
            for (q in i until j) net += yaw[q]
            net /= HZ
            var maxTurn = 0.0
            for ((a0, a1) in runs(BooleanArray(j - i) { kotlin.math.abs(yawSmooth[i + it]) > TURN_RATE })) {
                var turn = 0.0
                for (q in a0 until a1) turn += yawSmooth[i + q]
                maxTurn = maxOf(maxTurn, kotlin.math.abs(turn / HZ))
            }
            if (maxTurn < TURN_MIN || kotlin.math.abs(net) < STAIRS_NET_MIN) continue
            found += Transition(Kind.STAIRS, if (net > 0) 1 else -1, t[i], t[j - 1], std = std, turnDeg = net)
        }

        val emitted = mutableListOf<Transition>()
        for (e in found.sortedBy { it.t1Ms }) {
            if (e.t0Ms <= emittedUntilMs) continue
            emitted += e
            emittedUntilMs = e.t1Ms
        }
        transitions += emitted
        return emitted
    }

    companion object {
        const val HZ = 25
        const val STEP_MS = 1000.0 / HZ
        const val PULSE_A = 0.25
        const val PULSE_MIN_S = 0.6
        const val HAND_STD_MAX = 0.35
        const val MAX_RIDE_GAP_S = 60.0
        const val FLOOR_H = 4.0
        const val STAIRS_DOWN_STD = 1.45
        const val STAIRS_MIN_S = 5.0
        const val STAIRS_MAX_S = 30.0
        const val TURN_RATE = 30.0
        const val TURN_MIN = 90.0
        const val STAIRS_NET_MIN = 150.0
        const val STAIRS_GYRO_MAX_S = 60.0
        const val MIN_BOUT_S = 6.0
        const val BUFFER_S = 120
        const val LAG_S = 1.5
        const val RUN_EVERY_MS = 250.0

        /** pandas rolling(w, center=True, min_periods=1)：窗口是 [i − w/2, i + (w−1)/2]。 */
        internal fun rollingMean(x: DoubleArray, w: Int): DoubleArray {
            val pre = DoubleArray(x.size + 1)
            for (i in x.indices) pre[i + 1] = pre[i] + x[i]
            return DoubleArray(x.size) {
                val lo = maxOf(it - w / 2, 0)
                val hi = minOf(it + (w - 1) / 2, x.size - 1)
                (pre[hi + 1] - pre[lo]) / (hi - lo + 1)
            }
        }

        /** 同上，样本标准差（ddof=1），只有一个样本时为 0（Python 里是 NaN 再 fillna(0)）。 */
        internal fun rollingStd(x: DoubleArray, w: Int): DoubleArray = DoubleArray(x.size) {
            val lo = maxOf(it - w / 2, 0)
            val hi = minOf(it + (w - 1) / 2, x.size - 1)
            val m = hi - lo + 1
            if (m < 2) 0.0 else {
                var mean = 0.0
                for (q in lo..hi) mean += x[q]
                mean /= m
                var ss = 0.0
                for (q in lo..hi) ss += (x[q] - mean) * (x[q] - mean)
                sqrt(ss / (m - 1))
            }
        }

        internal fun std0(x: DoubleArray, from: Int, to: Int): Double {
            val m = to - from
            var mean = 0.0
            for (q in from until to) mean += x[q]
            mean /= m
            var ss = 0.0
            for (q in from until to) ss += (x[q] - mean) * (x[q] - mean)
            return sqrt(ss / m)
        }

        internal fun median(x: DoubleArray): Double {
            val s = x.sorted()
            val n = s.size
            return if (n % 2 == 1) s[n / 2] else (s[n / 2 - 1] + s[n / 2]) / 2
        }

        /** 连续 true 的 [start, end) 区间。 */
        internal fun runs(mask: BooleanArray): List<Pair<Int, Int>> {
            val out = mutableListOf<Pair<Int, Int>>()
            var i = 0
            while (i < mask.size) {
                if (!mask[i]) { i++; continue }
                var j = i
                while (j < mask.size && mask[j]) j++
                out += i to j
                i = j
            }
            return out
        }
    }
}
