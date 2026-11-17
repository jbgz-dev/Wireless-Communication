package edu.bnbu.floorsense.app.core

/**
 * 把 Wi-Fi 识别（FloorTracker）、跨层检测（MotionDetector）和融合（FloorFusion）接在一起。
 *
 * - Wi-Fi：FloorTracker 接受的扫描（USED）才进入融合。模型带 realtime_policy（C 的 V1）时就是 C 的规则：
 *   已知 AP ≥ 15、楼层分数 ≥ 0.50、和上一次指纹不重复；没有门限的旧模型用已知 AP ≥ 10（和 fuse.py 相同）。
 *   区域来自 FloorTracker（V1 时就是这次扫描的 Zone）；
 * - 传感器：加速度计、gravity 和陀螺仪的读数交给 MotionDetector，确认一次跨层后平移楼层概率，并清空 Wi-Fi 平滑窗口；
 * - 界面楼层 = 融合后概率最高的楼层；最高概率 < 0.5 时显示“—”。
 *
 * 时间都用 SystemClock.elapsedRealtime 毫秒。
 */
class FusionEngine(model: FloorModel, startFloor: Int? = null, useGyro: Boolean = true) {
    val tracker = FloorTracker.forPolicy(model, MIN_KNOWN_APS)
    val detector = MotionDetector(useGyro)
    val fusion = FloorFusion(model.floorClasses(), startFloor)

    var lastTransition: MotionDetector.Transition? = null
        private set

    /** 界面状态：楼层和楼层分数来自融合，其他字段（状态、区域、AP 数）来自 FloorTracker。 */
    val state: FloorTracker.State get() = fused(tracker.state)

    fun onScan(scan: WifiScan): FloorTracker.Update {
        val u = tracker.onScan(scan)
        if (u.outcome == FloorTracker.Outcome.USED) {
            val r = u.raw!!
            fusion.onWifi(scan.elapsedMs.toDouble(), r.floorClasses, r.floorProba)
        }
        return u.copy(state = fused(u.state))
    }

    fun tick(nowWallMs: Long): FloorTracker.State = fused(tracker.tick(nowWallMs))

    /** 返回这次读数确认的跨层事件（通常为空）。 */
    fun onAccelerometer(tMs: Double, x: Double, y: Double, z: Double) = handle(detector.onAccelerometer(tMs, x, y, z))

    fun onGravity(tMs: Double, x: Double, y: Double, z: Double) = handle(detector.onGravity(tMs, x, y, z))

    fun onGyroscope(tMs: Double, x: Double, y: Double, z: Double) = handle(detector.onGyroscope(tMs, x, y, z))

    private fun handle(events: List<MotionDetector.Transition>): List<MotionDetector.Transition> {
        for (e in events) {
            fusion.onTransition(e)
            tracker.onTransitionEnd()
            lastTransition = e
        }
        return events
    }

    private fun fused(s: FloorTracker.State) = s.copy(floor = fusion.shownFloor, floorScore = fusion.confidence)

    companion object {
        const val MIN_KNOWN_APS = 10

        private fun FloorModel.floorClasses(): List<Int> =
            predict(DoubleArray(config.bssids.size) { config.missingRssi }).floorClasses
    }
}
