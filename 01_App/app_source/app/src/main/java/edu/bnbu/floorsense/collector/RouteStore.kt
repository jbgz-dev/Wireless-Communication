package edu.bnbu.floorsense.collector

import android.content.Context
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * 每层的行走路线和路线上的节点（起点、转弯点、门口等）。
 * 节点只需填编号，列表顺序就是正向行走经过的顺序；坐标 x、y（米）可选，未测时写 null。
 * 保存在 routes.json，“导出全部”时一起打包。
 */
object RouteStore {
    class Node(var id: String, var xM: Double? = null, var yM: Double? = null)

    class Route(val building: String, val floor: Int, val routeId: String, val nodes: MutableList<Node> = mutableListOf()) {
        val floorKey get() = FloorKey(building, floor)
    }

    data class FloorKey(val building: String, val floor: Int) {
        override fun toString() = "$building · ${floor}F"
    }

    /** floors 只记手动添加、还没有路线的楼层；有路线的楼层从 routes 里推出来。 */
    class Data(val floors: MutableSet<FloorKey>, val routes: MutableList<Route>, val warning: String? = null) {
        fun floorKeys(extra: FloorKey?): List<FloorKey> =
            (floors + routes.map { it.floorKey } + listOfNotNull(extra)).distinct()
                .sortedWith(compareBy({ it.building }, { it.floor }))

        fun routesOn(f: FloorKey): List<Route> = routes.filter { it.floorKey == f }.sortedBy { it.routeId }
    }

    fun file(ctx: Context) = File(ctx.getExternalFilesDir(null), "routes.json")

    /** 文件损坏时改名备份（不覆盖），返回空数据并带上提示。 */
    fun load(ctx: Context): Data {
        val f = file(ctx)
        if (!f.exists()) return Data(mutableSetOf(), mutableListOf())
        return try {
            parse(JSONObject(f.readText()))
        } catch (e: Exception) {
            val backup = File(f.parentFile, "routes.json.broken-${System.currentTimeMillis()}")
            f.renameTo(backup)
            Data(mutableSetOf(), mutableListOf(), "routes.json 读取失败（${e.message}），已备份为 ${backup.name}")
        }
    }

    private fun parse(o: JSONObject): Data {
        val floors = mutableSetOf<FloorKey>()
        o.optJSONArray("floors")?.let { a ->
            for (i in 0 until a.length()) {
                val f = a.getJSONObject(i)
                floors.add(FloorKey(f.getString("building"), f.getInt("floor")))
            }
        }
        val routes = mutableListOf<Route>()
        o.optJSONArray("routes")?.let { a ->
            for (i in 0 until a.length()) {
                val r = a.getJSONObject(i)
                val route = Route(r.getString("building"), r.getInt("floor"), r.getString("route_id"))
                val nodes = r.optJSONArray("nodes") ?: JSONArray()
                // 旧版文件带 dist_m，按它排一次即可得到经过顺序；新版直接用数组顺序（sortedBy 是稳定排序）
                (0 until nodes.length()).map { nodes.getJSONObject(it) }
                    .sortedBy { it.optDouble("dist_m", 0.0) }
                    .forEach { n -> route.nodes.add(Node(n.getString("id"), n.optNum("x_m"), n.optNum("y_m"))) }
                routes.add(route)
            }
        }
        return Data(floors, routes)
    }

    /** 缺失或 null 返回 null（JSONObject.optDouble 会返回 NaN）。 */
    private fun JSONObject.optNum(k: String): Double? = if (isNull(k)) null else getDouble(k)

    fun save(ctx: Context, d: Data) {
        val floors = JSONArray()
        for (f in d.floorKeys(null)) floors.put(JSONObject().put("building", f.building).put("floor", f.floor))
        val routes = JSONArray()
        for (r in d.routes.sortedWith(compareBy({ it.building }, { it.floor }, { it.routeId }))) {
            val nodes = JSONArray()
            r.nodes.forEachIndexed { i, n ->
                nodes.put(
                    JSONObject().put("order", i + 1).put("id", n.id)
                        .put("x_m", n.xM ?: JSONObject.NULL).put("y_m", n.yM ?: JSONObject.NULL),
                )
            }
            routes.put(
                JSONObject().put("building", r.building).put("floor", r.floor)
                    .put("route_id", r.routeId).put("nodes", nodes),
            )
        }
        val o = JSONObject()
            .put("schema_version", 2)
            .put("note", "nodes 按正向经过顺序排列（order 从 1 开始）；x_m、y_m：平面坐标（米），null 表示还没测")
            .put("modified_ms", System.currentTimeMillis())
            .put("device_model", Build.MODEL)
            .put("floors", floors)
            .put("routes", routes)
        val f = file(ctx)
        val tmp = File(f.parentFile, "routes.json.tmp")
        tmp.writeText(o.toString(2))
        if (!tmp.renameTo(f)) {
            f.writeText(o.toString(2))
            tmp.delete()
        }
    }

    /** 复制到剪贴板用的 CSV，可以直接粘贴进表格。 */
    fun csv(routes: List<Route>): String {
        val sb = StringBuilder("building,floor,route_id,order,node_id,x_m,y_m\n")
        for (r in routes) r.nodes.forEachIndexed { i, n ->
            sb.append("${r.building},${r.floor},${r.routeId},${i + 1},${n.id},${n.xM ?: ""},${n.yM ?: ""}\n")
        }
        return sb.toString()
    }
}
