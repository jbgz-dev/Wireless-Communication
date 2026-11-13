package edu.bnbu.floorsense.app.core

import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * 在电脑上回放一批 session 并打印结果表（和手机上的回放模式是同一份代码）。
 * 只有设置了环境变量 FS_REPLAY_DIR（里面是 session 文件夹）时才运行：
 *   FS_REPLAY_DIR=/path/to/dynamic_train ./gradlew :floorsense:testDebugUnitTest --tests '*ReplayReport*' -i
 */
class ReplayReportTest {
    @Test
    fun report() {
        val root = System.getenv("FS_REPLAY_DIR")?.let(::File)
        assumeTrue(root != null && root.isDirectory)
        val model = KnnModel.fromJson(KnnParityTest.resource("knn_reference.json"))
        val dirs = root!!.listFiles().orEmpty().filter { File(it, "wifi.csv").exists() }.sortedBy { it.name }
        println("session,start,scored,wifi_only_acc,with_events_acc,raw_acc,settle_s_wifi_only,settle_s_with_events,low_signal_scans")
        for (d in dirs) {
            val base = ReplayRunner.run(d, model, useEvents = false)
            val ev = ReplayRunner.run(d, model, useEvents = true)
            println(listOf(
                d.name, base.startFloor, base.scored.size, f(base.accuracy), f(ev.accuracy), f(base.rawAccuracy),
                base.settleMs?.let { "%.1f".format(it / 1000.0) } ?: "",
                ev.settleMs?.let { "%.1f".format(it / 1000.0) } ?: "",
                base.rows.count { it.outcome == FloorTracker.Outcome.LOW_SIGNAL },
            ).joinToString(","))
        }
    }

    private fun f(v: Double) = if (v.isNaN()) "" else "%.3f".format(v)
}
