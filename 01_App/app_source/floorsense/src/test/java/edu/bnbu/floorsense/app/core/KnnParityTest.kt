package edu.bnbu.floorsense.app.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * 和 scikit-learn 对照：fixtures.json 由 tools/make_fixtures.py 用 C 的 KNN.joblib 生成。
 * 指纹、已知 AP 数、预测类别和投票分数都必须一致，才说明特征和推理没写错。
 */
class KnnParityTest {
    private val model = KnnModel.fromJson(resource("knn_reference.json"))
    private val fixtures = JSONObject(resource("fixtures.json")).getJSONArray("cases")

    @Test
    fun matchesScikitLearn() {
        assertTrue("测试样例太少", fixtures.length() >= 20)
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
            val expected = c.getJSONObject("fingerprint")
            assertEquals("$name 指纹 AP 数", expected.length(), fp.size)
            for (b in expected.keys()) assertEquals("$name $b", expected.getDouble(b), fp.getValue(b), 1e-9)
            val known = model.config.knownCount(fp)
            assertEquals("$name 已知 AP 数", c.getInt("known_aps"), known)
            if (known == 0) continue
            val p = model.predict(model.config.encode(fp))
            assertEquals("$name 楼层", c.getInt("floor"), p.floor)
            assertEquals("$name Zone", c.getString("zone"), p.zone)
            assertProba(name, c, "floor", p.floorClasses.map { it.toString() }, p.floorProba)
            assertProba(name, c, "zone", p.zoneClasses, p.zoneProba)
        }
    }

    private fun assertProba(name: String, c: JSONObject, target: String, classes: List<String>, proba: DoubleArray) {
        val ec = c.getJSONArray("${target}_classes")
        val ep = c.getJSONArray("${target}_proba")
        assertEquals("$name $target 类别", List(ec.length()) { ec.get(it).toString() }, classes)
        // scikit-learn 用 float32 算距离，Kotlin 用 double，分数会有极小的差异
        for (j in proba.indices) assertEquals("$name $target 分数 $j", ep.getDouble(j), proba[j], 1e-4)
    }

    companion object {
        fun resource(name: String): String =
            File("src/test/resources/$name").takeIf { it.exists() }?.readText()
                ?: File("src/main/assets/$name").readText()
    }
}
