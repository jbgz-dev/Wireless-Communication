package edu.bnbu.floorsense.app.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FusionTest {
    private val floors = listOf(2, 3, 4, 5, 6)
    private fun onehot(f: Int) = DoubleArray(5) { if (floors[it] == f) 1.0 else 0.0 }

    @Test
    fun uniformStartShowsNothing() {
        val f = FloorFusion()
        assertNull("不知道起点时不能显示 2F", f.shownFloor)
    }

    @Test
    fun elevatorShiftsFromCheckpointAndIgnoresWifiDuringRide() {
        val f = FloorFusion(startFloor = 3)
        f.onWifi(1_000.0, floors, onehot(3))
        // 电梯里的扫描（t0..t1 之间）判成 2 楼，确认后应被丢弃
        f.onWifi(12_000.0, floors, onehot(2))
        f.onWifi(13_000.0, floors, onehot(2))
        f.onTransition(MotionDetector.Transition(MotionDetector.Kind.ELEVATOR, 2, 10_000.0, 20_000.0, dispM = 8.0))
        assertEquals(5, f.shownFloor)
        f.onWifi(22_000.0, floors, onehot(5))
        assertEquals(5, f.shownFloor)
        assertTrue(f.confidence > 0.9)
    }

    @Test
    fun wifiCanRecoverAWrongStart() {
        val f = FloorFusion(startFloor = 2)
        repeat(6) { f.onWifi(1_000.0 * it, floors, onehot(4)) }
        assertEquals(4, f.shownFloor)
    }

    /** 合成一段电梯上行：静止 5 s → +0.6 m/s² 2 s → 匀速 6 s → −0.6 m/s² 2 s → 静止 5 s，位移 1.2·2·(1+6+1)/… ≈ 8 m。 */
    @Test
    fun detectsSyntheticElevatorRide() {
        // 电梯检测不用陀螺仪；useGyro=true 时没有陀螺仪数据检测器会一直等待
        val d = MotionDetector(useGyro = false)
        val g = 9.81
        var found = emptyList<MotionDetector.Transition>()
        val hz = 50
        for (i in 0 until 30 * hz) {
            val t = i * 1000.0 / hz
            val s = t / 1000.0
            val a = when {
                s in 5.0..7.0 -> 0.6
                s in 13.0..15.0 -> -0.6
                else -> 0.0
            }
            d.onGravity(t, 0.0, 0.0, g)
            found = found + d.onAccelerometer(t, 0.0, 0.0, g + a)
        }
        assertEquals(1, found.size)
        assertEquals(MotionDetector.Kind.ELEVATOR, found[0].kind)
        // v = 1.2 m/s，位移 = 0.5·0.6·4 + 1.2·6 + 0.5·0.6·4 ≈ 9.6 m → 2 层
        assertEquals(2, found[0].delta)
    }

    /**
     * 合成 30 s：静止 5 s → 走 20 s（竖直加速度 ±1.5 m/s² 的 2 Hz 步伐）→ 静止 5 s。
     * turnDegPerS 在第 13–16 s 加一次绕竖直轴的转身（3 s × 60°/s = 180°），模拟楼梯平台。
     */
    private fun walk(turnDegPerS: Double): List<MotionDetector.Transition> {
        val d = MotionDetector(useGyro = true)
        val g = 9.81
        val hz = 50
        var found = emptyList<MotionDetector.Transition>()
        for (i in 0 until 30 * hz) {
            val t = i * 1000.0 / hz
            val s = t / 1000.0
            val a = if (s in 5.0..25.0) 1.5 * kotlin.math.sin(2 * Math.PI * 2 * s) else 0.0
            val w = if (s in 13.0..16.0) Math.toRadians(turnDegPerS) else 0.0
            d.onGravity(t, 0.0, 0.0, g)
            d.onGyroscope(t, 0.0, 0.0, w)
            found = found + d.onAccelerometer(t, 0.0, 0.0, g + a)
        }
        return found
    }

    @Test
    fun gyroLandingTurnGivesStairsDirection() {
        val up = walk(60.0)
        assertEquals(1, up.size)
        assertEquals(MotionDetector.Kind.STAIRS, up[0].kind)
        assertEquals(1, up[0].delta)
        assertEquals(180.0, up[0].turnDeg!!, 5.0)
        val down = walk(-60.0)
        assertEquals(listOf(-1), down.map { it.delta })
        // 直走（走廊）不转身：不是楼梯
        assertTrue(walk(0.0).isEmpty())
    }

    @Test
    fun pandasStyleCenteredWindows() {
        val x = DoubleArray(20) { it.toDouble() }
        val m = MotionDetector.rollingMean(x, 12)
        assertEquals(2.5, m[0], 1e-12)
        assertEquals(16.0, m[19], 1e-12)
        assertEquals(3.8944404818493075, MotionDetector.rollingStd(x, 25)[0], 1e-12)
    }
}
