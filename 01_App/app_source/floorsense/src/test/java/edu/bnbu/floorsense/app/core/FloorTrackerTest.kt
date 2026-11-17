package edu.bnbu.floorsense.app.core

import org.junit.Assert.assertEquals
import org.junit.Test

class FloorTrackerTest {
    /** 只看第一个 AP 的 RSSI：-40 判 3 楼，其他判 5 楼。 */
    private val fake = object : FloorModel {
        override val name = "fake"
        override val config = FeatureConfig(List(10) { "aa:00:00:00:00:%02x".format(it) })
        override fun predict(x: DoubleArray): Prediction {
            val three = x[0] == -40.0
            return Prediction(listOf(3, 5), if (three) doubleArrayOf(1.0, 0.0) else doubleArrayOf(0.0, 1.0),
                listOf("East", "West"), doubleArrayOf(0.5, 0.5))
        }
    }

    private var id = 0
    private fun scan(first: Double, n: Int = 6, t: Long = id * 2000L, fresh: Boolean = true) =
        WifiScan(id++, t, fresh, true, List(n) { ApReading(fake.config.bssids[it], if (it == 0) first else -60.0 - it) })

    @Test
    fun cachedAndLowSignalScansDoNotChangeFloor() {
        val t = FloorTracker(fake)
        assertEquals(3, t.onScan(scan(-40.0)).state.floor)
        assertEquals(FloorTracker.Outcome.CACHED, t.onScan(scan(-80.0, fresh = false)).outcome)
        val low = t.onScan(scan(-80.0, n = 3))
        assertEquals(FloorTracker.Outcome.LOW_SIGNAL, low.outcome)
        assertEquals(FloorTracker.Status.LOW_SIGNAL, low.state.status)
        assertEquals(3, low.state.floor)
    }

    @Test
    fun duplicateFingerprintsAreNotWeightedTwice() {
        val t = FloorTracker(fake, window = 5)
        t.onScan(scan(-40.0))
        t.onScan(scan(-80.0))
        // 同一个 5 楼指纹重复三次：只算一次，3 楼和 5 楼各占一半，同分取较小类别
        repeat(3) { assertEquals(FloorTracker.Outcome.DUPLICATE, t.onScan(scan(-80.0)).outcome) }
        assertEquals(2, t.state.windowSize)
        assertEquals(3, t.state.floor)
        assertEquals(5, t.onScan(scan(-81.0)).state.floor)
    }

    @Test
    fun staleAndRecovery() {
        val t = FloorTracker(fake, staleAfterMs = 15_000)
        t.onScan(scan(-40.0, t = 0))
        assertEquals(FloorTracker.Status.OK, t.tick(10_000).status)
        assertEquals(FloorTracker.Status.STALE, t.tick(16_000).status)
        assertEquals(3, t.state.floor)
        assertEquals(FloorTracker.Status.OK, t.onScan(scan(-41.0, t = 20_000)).state.status)
    }

    @Test
    fun transitionResetsWindow() {
        val t = FloorTracker(fake, window = 5)
        // 用不同的第二个 AP 做出 5 个不同的 3 楼指纹，填满窗口
        repeat(5) { k ->
            val aps = List(6) { ApReading(fake.config.bssids[it], if (it == 0) -40.0 else -60.0 - it - k) }
            t.onScan(WifiScan(id++, id * 2000L, true, true, aps))
        }
        assertEquals(5, t.state.windowSize)
        t.onTransitionStart()
        val moving = t.onScan(scan(-80.0))
        assertEquals(FloorTracker.Outcome.MOVING, moving.outcome)
        assertEquals(3, moving.state.floor)
        t.onTransitionEnd()
        assertEquals(0, t.state.windowSize)
        // 到达后第一次扫描就决定新楼层，不用等 5 个旧的 3 楼分数被冲掉
        assertEquals(5, t.onScan(scan(-81.0)).state.floor)
    }
}
