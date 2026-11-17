package edu.bnbu.floorsense.app.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** 和 scikit-learn 对照：rf_fixtures.json 由 tools/export_rf.py 用同一个随机森林生成。 */
class ForestParityTest {
    private val model = ForestModel.fromJson(KnnParityTest.resource("rf_model.json"))
    private val fixtures = JSONObject(KnnParityTest.resource("rf_fixtures.json")).getJSONArray("cases")

    @Test
    fun matchesScikitLearn() {
        assertTrue("测试样例太少", fixtures.length() >= 50)
        var maxDiff = 0.0
        for (i in 0 until fixtures.length()) {
            val c = fixtures.getJSONObject(i)
            val name = c.getString("name")
            val aps = c.getJSONArray("aps").let { a ->
                List(a.length()) {
                    val o = a.getJSONObject(it)
                    ApReading(o.getString("bssid"), o.getDouble("rssi"), if (o.isNull("age_ms")) null else o.getLong("age_ms"))
                }
            }
            val fp = model.config.fingerprint(aps)
            assertEquals("$name 已知 AP 数", c.getInt("known_aps"), model.config.knownCount(fp))
            val p = model.predict(model.config.encode(fp))
            assertEquals("$name 楼层", c.getInt("floor"), p.floor)
            assertEquals("$name Zone", c.getString("zone"), p.zone)
            for ((target, proba) in listOf("floor" to p.floorProba, "zone" to p.zoneProba)) {
                val e = c.getJSONArray("${target}_proba")
                assertEquals(e.length(), proba.size)
                for (j in proba.indices) {
                    maxDiff = maxOf(maxDiff, kotlin.math.abs(e.getDouble(j) - proba[j]))
                    assertEquals("$name $target 分数 $j", e.getDouble(j), proba[j], 1e-9)
                }
            }
        }
        println("RF parity: ${fixtures.length()} cases, max |Δproba| = $maxDiff")
    }
}
