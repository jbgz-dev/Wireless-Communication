package edu.bnbu.floorsense.app.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * 和 C 交付的 V1 对照（floorsense_c/d2_validation/D_handoff/realtime）：
 * - parity_vectors.json：分类器输出（楼层、Zone、投票分数）；
 * - realtime_policy_vectors.json：冷启动 waiting、弱信号 held、信号恢复 ok。
 * 两份向量和模型都是 C 的原文件，没有改动。
 */
class V1ParityTest {
    private val model = loadV1()

    private fun scanOf(o: JSONObject): Map<String, Double> {
        val m = o.getJSONObject("bssid_rssi")
        val aps = m.keys().asSequence().map { ApReading(it, m.getDouble(it)) }.toList()
        return model.config.fingerprint(aps)
    }

    @Test
    fun loadsFrozenV1() {
        assertEquals(934, model.config.size)
        assertEquals(RealtimePolicy(15, 0.50), model.policy)
    }

    @Test
    fun classifierMatchesReference() {
        val cases = JSONArray(KnnParityTest.resource("v1_parity_vectors.json"))
        assertTrue(cases.length() >= 5)
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val e = c.getJSONObject("expected_model_output")
            val fp = scanOf(c)
            assertEquals("case $i 已知 AP", e.getInt("known_aps"), model.config.knownCount(fp))
            val p = model.predict(model.config.encode(fp))
            assertEquals("case $i 楼层", e.getInt("floor"), p.floor)
            assertEquals("case $i Zone", e.getString("zone"), p.zone)
            assertEquals("case $i 楼层分数", e.getDouble("floor_vote_score"), p.floorScore, 1e-12)
            assertEquals("case $i Zone 分数", e.getDouble("zone_vote_score"), p.zoneScore, 1e-12)
        }
    }

    /** 每个向量是独立的一步：previous_floor 是上一次通过门限的楼层。用一次“种子”扫描把 tracker 带到这个楼层。 */
    @Test
    fun realtimePolicyMatchesReference() {
        val cases = JSONArray(KnnParityTest.resource("v1_realtime_policy_vectors.json"))
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val e = c.getJSONObject("expected")
            val prev = if (c.isNull("previous_floor")) null else c.getInt("previous_floor")
            val t = FloorTracker.forPolicy(model)
            if (prev != null) t.seed(prev)
            val m = c.getJSONObject("bssid_rssi")
            val aps = m.keys().asSequence().map { ApReading(it, m.getDouble(it)) }.toList()
            val u = t.onScan(WifiScan(1, 1_000, true, true, aps))
            val name = c.getString("case")
            assertEquals(name, e.getInt("known_aps"), u.state.knownAps)
            when (e.getString("status")) {
                "ok" -> {
                    assertEquals(name, FloorTracker.Outcome.USED, u.outcome)
                    assertEquals(name, e.getInt("floor"), u.state.floor)
                    assertEquals(name, e.getString("zone"), u.state.zone)
                }
                "held", "waiting" -> {
                    assertTrue(name, u.outcome == FloorTracker.Outcome.LOW_SIGNAL || u.outcome == FloorTracker.Outcome.LOW_VOTE)
                    val reason = if (u.outcome == FloorTracker.Outcome.LOW_SIGNAL) "low_known_aps" else "low_floor_vote"
                    assertEquals(name, e.getString("reason"), reason)
                    assertEquals(name, prev, u.state.floor)
                    assertNull(name, u.state.zone)
                }
                else -> error("未知状态 ${e.getString("status")}")
            }
        }
    }

    companion object {
        fun loadV1(): ForestModel {
            val f = listOf("src/main/assets/wifi_model.json.gz").map(::File).first { it.exists() }
            return f.inputStream().use { ForestModel.load(it) }
        }
    }
}
