package edu.bnbu.floorsense.collector

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** 读取已保存的会话：防泄漏检查、覆盖统计、打包导出。 */
object SessionStore {
    fun root(ctx: Context): File = File(ctx.getExternalFilesDir(null), "sessions").apply { mkdirs() }

    data class Info(val dir: File, val meta: JSONObject) {
        val labels: JSONObject get() = meta.getJSONObject("labels")
        val status: String get() = meta.optString("status")
        val mode: String get() = labels.optString("mode")
        val pointKey: String? get() =
            if (mode == "transition") null
            else "${labels.optString("building")}|${labels.optInt("floor")}|${labels.optString("point_id")}"
    }

    fun list(ctx: Context): List<Info> =
        root(ctx).listFiles().orEmpty()
            .filter { it.isDirectory }
            .mapNotNull { d ->
                try { Info(d, JSONObject(File(d, "meta.json").readText())) } catch (_: Exception) { null }
            }
            .sortedBy { it.dir.name }

    /** 如果这个点位已经以另一个 split 采集过，返回那个 split。 */
    fun conflictingSplit(ctx: Context, form: Form): String? =
        form.pointKey()?.let { conflictingSplit(ctx, it, form.split, exclude = null) }

    /** exclude：修改历史记录时排除记录自己。 */
    fun conflictingSplit(ctx: Context, key: String, split: String, exclude: File?): String? =
        list(ctx).firstOrNull {
            it.dir != exclude && it.status != "incomplete" && it.pointKey == key &&
                it.labels.optString("split") != split
        }?.labels?.optString("split")

    /**
     * 修改一条记录的标签。只改 meta.json，不改目录名：目录名就是 session_id，
     * 电脑端按它去重，改名会让同一条记录在多次导出里变成两条。
     * 修改前后的值记在 label_history 里，labels_modified_ms 让电脑端优先采用新版本。
     */
    fun updateLabels(info: Info, changes: Map<String, Any>) {
        val meta = info.meta
        val labels = meta.getJSONObject("labels")
        val before = JSONObject()
        val after = JSONObject()
        for ((k, v) in changes) {
            before.put(k, labels.opt(k) ?: JSONObject.NULL)
            after.put(k, v)
            labels.put(k, v)
        }
        val now = System.currentTimeMillis()
        val history = meta.optJSONArray("label_history") ?: JSONArray()
        history.put(JSONObject().put("t_wall_ms", now).put("before", before).put("after", after))
        meta.put("label_history", history).put("labels_modified_ms", now)
        val tmp = File(info.dir, "meta.json.tmp")
        tmp.writeText(meta.toString(2))
        if (!tmp.renameTo(File(info.dir, "meta.json"))) {
            File(info.dir, "meta.json").writeText(meta.toString(2))
            tmp.delete()
        }
    }

    /** 每层的采集进度，显示在界面底部。 */
    fun summary(ctx: Context): String {
        val done = list(ctx).filter { it.status == "complete" }
        if (done.isEmpty()) return "还没有完成的采集"
        val sb = StringBuilder()
        val byFloor = done.filter { it.mode != "transition" }
            .groupBy { it.labels.optString("building") to it.labels.optInt("floor") }
            .toSortedMap(compareBy<Pair<String, Int>>({ it.first }, { it.second }))
        for ((key, items) in byFloor) {
            fun pts(split: String) = items
                .filter { it.mode == "static" && it.labels.optString("split") == split }
                .map { it.labels.optString("point_id") }.toSet().size
            val statics = items.count { it.mode == "static" }
            val walks = items.count { it.mode == "walk" }
            sb.append("${key.first} ${key.second}F：训练点 ${pts("train")}，测试点 ${pts("test")}，")
                .append("静态 $statics 次，行走 $walks 次\n")
        }
        val trans = done.filter { it.mode == "transition" }
        if (trans.isNotEmpty()) {
            val byMethod = trans.groupingBy { it.labels.optString("method") }.eachCount()
            sb.append("跨层：").append(byMethod.entries.joinToString { "${it.key} ${it.value} 次" }).append('\n')
        }
        val devices = done.map { it.meta.getJSONObject("device").optString("model") }.toSet()
        sb.append("本机共 ${done.size} 个完成的会话，机型：${devices.joinToString()}")
        return sb.toString()
    }

    /** 把所有会话打包成一个 zip，返回 zip 文件。 */
    fun exportZip(ctx: Context): File {
        val exports = File(ctx.getExternalFilesDir(null), "exports").apply { mkdirs() }
        val src = root(ctx)
        val zip = File(exports, "floorsense_${android.os.Build.MODEL.replace(Regex("[^A-Za-z0-9]+"), "-")}_" +
            "${System.currentTimeMillis()}.zip")
        ZipOutputStream(zip.outputStream().buffered()).use { z ->
            src.walkTopDown().filter { it.isFile }.forEach { f ->
                z.putNextEntry(ZipEntry("sessions/" + f.relativeTo(src).invariantSeparatorsPath))
                f.inputStream().use { it.copyTo(z) }
                z.closeEntry()
            }
            val routes = RouteStore.file(ctx)
            if (routes.isFile) {
                z.putNextEntry(ZipEntry("routes.json"))
                routes.inputStream().use { it.copyTo(z) }
                z.closeEntry()
            }
        }
        return zip
    }
}
