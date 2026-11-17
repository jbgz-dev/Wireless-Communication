package edu.bnbu.floorsense.app.core

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * 用 App 的 FloorTracker + V1 重放 C 的 D3 静态 Test session，和 C 的 scan_events.csv 逐条对照，
 * 再按 C 的规则（从 session 开始 30 秒内最后一次通过门限的楼层/Zone）算点位准确率。
 * 只在设置了环境变量时运行（Test 数据不放进仓库）：
 *   FS_D3_EVENTS=<d3_results/2026-11-16_70pt/scan_events.csv>
 *   FS_D3_DIRS=<sessions 目录>:<另一个 sessions 目录>
 */
class D3ReplayTest {
    @Test
    fun matchesCScanEvents() {
        val eventsFile = System.getenv("FS_D3_EVENTS")?.let(::File)
        val roots = System.getenv("FS_D3_DIRS")?.split(':')?.map(::File)?.filter { it.isDirectory }.orEmpty()
        assumeTrue(eventsFile != null && eventsFile.exists() && roots.isNotEmpty())
        val expected = Csv.read(eventsFile!!).filter { it["model"] == "V1" }
        val bySession = expected.groupBy { it.getValue("session_id") }
        val dirs = roots.flatMap { it.listFiles().orEmpty().toList() }.associateBy { it.name }
        val model = V1ParityTest.loadV1()
        var scans = 0; var mismatches = 0
        var pts = 0; var floorOk = 0; var zoneOk = 0; var jointOk = 0; var basicN = 0; var basicOk = 0
        for ((sid, rows) in bySession.toSortedMap()) {
            val dir = dirs[sid] ?: error("找不到 session $sid")
            val start = JSONObject(File(dir, "meta.json").readText()).getLong("start_wall_ms")
            val session = SessionReplay.load(dir)
            val want = rows.associateBy { it.getValue("scan_id").toInt() }
            val tracker = FloorTracker.forPolicy(model)
            var lastFloor: Int? = null; var lastZone: String? = null
            for (scan in session.scans.filter { it.tMs in start..start + 30_000 }) {
                val u = tracker.onScan(scan)
                val e = want[scan.scanId] ?: continue
                scans++
                val accepted = u.outcome == FloorTracker.Outcome.USED || u.outcome == FloorTracker.Outcome.DUPLICATE
                val ok = accepted == (e["status"] == "ok") &&
                    (!accepted || (u.state.floor.toString() == e["floor"] && u.state.zone == e["zone"] &&
                        kotlin.math.abs(u.raw!!.floorScore - e.getValue("floor_vote_score").toDouble()) < 1e-9))
                if (!ok) { mismatches++; println("不一致 $sid scan ${scan.scanId}: ${u.outcome} ${u.state.floor} ${u.state.zone} vs $e") }
                if (accepted) { lastFloor = u.state.floor; lastZone = u.state.zone }
            }
            val r = rows.first()
            val tf = r.getValue("expected_floor").toInt(); val tz = r.getValue("expected_zone")
            pts++
            if (lastFloor == tf) floorOk++
            if (lastZone == tz) zoneOk++
            if (lastFloor == tf && lastZone == tz) jointOk++
            if (tf <= 4) { basicN++; if (lastFloor == tf) basicOk++ }
        }
        println("D3 replay: $pts 点，$scans 次扫描，不一致 $mismatches")
        println("楼层 $floorOk/$pts，2–4F $basicOk/$basicN，Zone $zoneOk/$pts，楼层+Zone $jointOk/$pts")
        assertEquals(0, mismatches)
    }
}
