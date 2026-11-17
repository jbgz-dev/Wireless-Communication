package edu.bnbu.floorsense.app.core

import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * 用 Kotlin 的检测和融合重放动态 session，打印和 fuse.py 可直接对照的表。
 * 只有设置了环境变量 FS_DYNAMIC_DIRS（多个目录用 : 分隔，每个目录下是 session 文件夹）时才运行：
 * FS_LOOKAHEAD_MS=2000 时提前喂传感器，用来和 fuse.py 的离线结果逐条对照。FS_GYRO=0 时只用加速度计（D2 规则）。
 *   FS_DYNAMIC_DIRS=/path/dynamic_train:/path/dynamic_validation ./gradlew :floorsense:testDebugUnitTest --tests '*FusionReplay*' -i
 */
class FusionReplayTest {
    @Test
    fun report() {
        val roots = System.getenv("FS_DYNAMIC_DIRS")?.split(':')?.map(::File)?.filter { it.isDirectory }.orEmpty()
        assumeTrue(roots.isNotEmpty())
        // 默认用 App 里的 V1；FS_MODEL=rf 时用 D2 的旧 RF（rf_model.json），用来和 fuse.py 对照
        val model = if (System.getenv("FS_MODEL") == "rf") ForestModel.fromJson(KnnParityTest.resource("rf_model.json")) else V1ParityTest.loadV1()
        println("model: ${model.name}")
        println("run,init,detected,detected_delta,n,wifi_only,fused_shown,fused_argmax,after_n,after_wifi_only,after_fused_shown,after_fused_argmax")
        for (root in roots) {
            val dirs = root.listFiles().orEmpty().filter { File(it, "sensors.csv").exists() }.sortedBy { it.name }
            val lookahead = System.getenv("FS_LOOKAHEAD_MS")?.toLong() ?: 0L
            for (d in dirs) for (known in listOf(false, true)) {
                val r = FusionReplay.run(d, model, knownStart = known, lookaheadMs = lookahead, useGyro = System.getenv("FS_GYRO") != "0")
                val run = org.json.JSONObject(File(d, "meta.json").readText()).getJSONObject("labels").optString("notes")
                println(listOf(
                    run, if (known) "known_start" else "uniform", r.summary(), r.detectedDelta, r.n,
                    r.wifiOnlyCorrect, r.fusedCorrect, r.argmaxCorrect,
                    r.afterN, r.afterWifiOnlyCorrect, r.afterFusedCorrect, r.afterArgmaxCorrect,
                ).joinToString(","))
            }
        }
    }
}
