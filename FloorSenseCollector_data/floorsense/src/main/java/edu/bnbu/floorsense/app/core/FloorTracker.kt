package edu.bnbu.floorsense.app.core

/**
 * 把逐次扫描的预测变成界面上稳定显示的楼层。
 *
 * - 只用 fresh 且 results_updated 的扫描；缓存结果直接忽略。
 * - 已知 AP 少于 minKnownAps 时不猜，保持上一次的楼层，状态显示“信号不足”。
 * - 与上一次完全相同的指纹不重复计入平滑（训练数据里 73% 的扫描是这种重复）。
 * - 对最近 window 个不同指纹的投票分数取平均，取最高的楼层/Zone 显示。
 * - 超过 staleAfterMs 没有新扫描时状态变为“扫描过期”，楼层保持不变，恢复扫描后自动继续。
 * - onTransitionStart/onTransitionEnd 留给 E 的跨层检测：移动中冻结显示，到达后清空平滑窗口，
 *   下一次扫描直接决定新楼层，不用等旧楼层的分数被慢慢冲掉。
 */
class FloorTracker(
    private val model: FloorModel,
    private val minKnownAps: Int = 5,
    private val window: Int = 5,
    private val staleAfterMs: Long = 15_000,
) {
    enum class Status { WAITING, OK, LOW_SIGNAL, STALE, MOVING }

    /** 一次扫描被怎样处理，评估模式会记录下来。 */
    enum class Outcome { CACHED, LOW_SIGNAL, DUPLICATE, USED, MOVING }

    data class State(
        val status: Status = Status.WAITING,
        val floor: Int? = null,
        val floorScore: Double = 0.0,
        val zone: String? = null,
        val zoneScore: Double = 0.0,
        val knownAps: Int = 0,
        val observedAps: Int = 0,
        val lastFreshMs: Long? = null,
        val windowSize: Int = 0,
    )

    data class Update(val state: State, val outcome: Outcome, val raw: Prediction?)

    var state = State()
        private set

    private val history = ArrayDeque<Prediction>()
    private var lastFp: Map<String, Double>? = null
    private var moving = false

    fun onScan(scan: WifiScan): Update {
        if (!scan.usable) return Update(state, Outcome.CACHED, null)
        val fp = model.config.fingerprint(scan.aps)
        val known = model.config.knownCount(fp)
        val base = state.copy(knownAps = known, observedAps = fp.size, lastFreshMs = scan.tMs)
        if (known < minKnownAps) {
            state = base.copy(status = if (moving) Status.MOVING else Status.LOW_SIGNAL)
            return Update(state, Outcome.LOW_SIGNAL, null)
        }
        val raw = model.predict(model.config.encode(fp))
        if (moving) {
            state = base.copy(status = Status.MOVING)
            return Update(state, Outcome.MOVING, raw)
        }
        val duplicate = fp == lastFp
        lastFp = fp
        if (!duplicate) {
            history.addLast(raw)
            while (history.size > window) history.removeFirst()
        }
        state = withAverage(base.copy(status = Status.OK))
        return Update(state, if (duplicate) Outcome.DUPLICATE else Outcome.USED, raw)
    }

    /** 定时调用，用来发现扫描停止（例如 Wi-Fi 被关掉）。 */
    fun tick(nowMs: Long): State {
        val last = state.lastFreshMs
        if (!moving && last != null && nowMs - last > staleAfterMs && state.status != Status.STALE) {
            state = state.copy(status = Status.STALE)
        }
        return state
    }

    fun onTransitionStart() {
        moving = true
        state = state.copy(status = Status.MOVING)
    }

    fun onTransitionEnd() {
        moving = false
        history.clear()
        lastFp = null
        state = state.copy(status = Status.WAITING, windowSize = 0)
    }

    private fun withAverage(s: State): State {
        if (history.isEmpty()) return s
        val first = history.first()
        val floorAvg = average { it.floorProba }
        val zoneAvg = average { it.zoneProba }
        val fi = Prediction.argmax(floorAvg)
        val zi = Prediction.argmax(zoneAvg)
        return s.copy(
            floor = first.floorClasses[fi], floorScore = floorAvg[fi],
            zone = first.zoneClasses[zi], zoneScore = zoneAvg[zi],
            windowSize = history.size,
        )
    }

    private fun average(pick: (Prediction) -> DoubleArray): DoubleArray {
        val out = DoubleArray(pick(history.first()).size)
        for (p in history) pick(p).forEachIndexed { i, v -> out[i] += v / history.size }
        return out
    }
}
