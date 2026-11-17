package edu.bnbu.floorsense.collector

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.Spinner
import org.json.JSONObject
import java.io.File
import java.util.zip.ZipFile

/** 仅在测试模拟器运行，无第三方测试依赖；覆盖真实按钮与 Session 导出。 */
class WalkFlowInstrumentation : Instrumentation() {
    private lateinit var activity: MainActivity
    private val sessions = mutableListOf<File>()
    private var checks = 0

    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); start() }

    private fun verify(condition: Boolean, message: String) {
        check(condition) { message }
        checks++
    }

    private fun active(): Session? {
        var result: Session? = null
        runOnMainSync {
            val field = MainActivity::class.java.getDeclaredField("session").apply { isAccessible = true }
            result = field.get(activity) as Session?
        }
        return result
    }

    private fun click(id: Int) {
        runOnMainSync { check(activity.findViewById<View>(id).performClick()) }
        waitForIdleSync()
    }

    private fun dialogButton(text: String) {
        repeat(30) {
            val nodes = uiAutomation.rootInActiveWindow?.findAccessibilityNodeInfosByText(text).orEmpty()
            val button = nodes.firstOrNull { it.text?.toString() == text && it.isClickable }
            if (button != null && button.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                waitForIdleSync()
                return
            }
            Thread.sleep(100)
        }
        error("Dialog button missing: $text")
    }

    private fun begin(reverse: Boolean): Session {
        runOnMainSync { activity.findViewById<CheckBox>(R.id.cbReverseWalk).isChecked = reverse }
        click(R.id.btnStart)
        if (active() == null) dialogButton("开始") // 模拟器可能提示蓝牙未开启等环境警告。
        val s = checkNotNull(active()) { "Start did not create a session" }
        sessions.add(s.dir)
        verify(s.walk?.visits?.single()?.node?.id == if (reverse) "C" else "A", "Automatic start node")
        verify(File(s.dir, "route_snapshot.json").exists(), "Route snapshot persisted")
        return s
    }

    override fun onStart() {
        val result = Bundle()
        val routeFile = RouteStore.file(targetContext)
        val original = if (routeFile.exists()) routeFile.readText() else null
        var resultCode = Activity.RESULT_CANCELED
        try {
            val data = RouteStore.load(targetContext)
            data.routes.removeAll { it.building == "NODE_QA" }
            data.routes.add(RouteStore.Route("NODE_QA", 3, "TW01", mutableListOf(
                RouteStore.Node("A"), RouteStore.Node("B"), RouteStore.Node("C"))))
            RouteStore.save(targetContext, data)
            activity = startActivitySync(Intent(targetContext, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
            waitForIdleSync()
            runOnMainSync {
                activity.findViewById<EditText>(R.id.etCollector).setText("QA")
                activity.findViewById<EditText>(R.id.etBuilding).setText("NODE_QA")
                activity.findViewById<EditText>(R.id.etFloor).setText("3")
                activity.findViewById<EditText>(R.id.etPoint).setText("TW01")
                activity.findViewById<Spinner>(R.id.spMode).setSelection(1)
            }
            waitForIdleSync()
            val forward = begin(false)
            val progress = checkNotNull(forward.walk)
            verify(!progress.canUndo, "Cannot undo start")
            Thread.sleep(1100)
            click(R.id.btnNodeReached)
            verify(progress.reachedCount == 2, "B button records B")
            click(R.id.btnNodeReached)
            verify(progress.reachedCount == 2, "Double tap does not record C")
            click(R.id.btnUndoNode)
            verify(progress.reachedCount == 1 && progress.nextNode?.id == "B", "Undo B")
            val screenshot = File(targetContext.getExternalFilesDir(null), "exports/node-qa.png")
            screenshot.parentFile?.mkdirs()
            screenshot.outputStream().use {
                uiAutomation.takeScreenshot().compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
            }
            Thread.sleep(1100)
            click(R.id.btnNodeReached)
            Thread.sleep(1100)
            click(R.id.btnNodeReached)
            verify(active() === forward && progress.complete, "Endpoint keeps continuous session running")
            click(R.id.btnStop)
            val meta = JSONObject(File(forward.dir, "meta.json").readText())
            verify(active() == null && meta.getString("status") == "complete", "Manual stop saves complete")
            verify(meta.getJSONObject("walk").getBoolean("route_complete"), "Route completion metadata")
            verify(File(forward.dir, "node_visits.csv").readLines().size == 4, "Effective table has three visits")
            verify(File(forward.dir, "node_marks.csv").readLines().size == 6, "Audit retains undo and re-record")
            verify(File(forward.dir, "sensors.csv").readLines().size > 1, "Sensors record between marks")

            val reverse = begin(true)
            Thread.sleep(1100)
            click(R.id.btnNodeReached)
            verify(reverse.walk!!.visits.last().node.id == "B", "Reverse second node is B")
            Thread.sleep(1100)
            click(R.id.btnNodeReached)
            click(R.id.btnStop)
            verify(JSONObject(File(reverse.dir, "meta.json").readText()).getString("status") == "complete",
                "Reverse completes")

            val incomplete = begin(false)
            click(R.id.btnStop)
            dialogButton("停止")
            verify(JSONObject(File(incomplete.dir, "meta.json").readText()).getString("status") == "incomplete",
                "Early stop is incomplete")

            // 修改路线管理后，旧 Session 的路线副本必须保持不变。
            data.routes.last().nodes[1].id = "B2"
            RouteStore.save(targetContext, data)
            verify(JSONObject(File(forward.dir, "route_snapshot.json").readText()).getJSONArray("nodes")
                .getJSONObject(1).getString("id") == "B", "Snapshot survives route edits")
            val zip = SessionStore.exportZip(targetContext)
            ZipFile(zip).use { z ->
                verify(z.getEntry("sessions/${forward.dir.name}/node_visits.csv") != null, "Zip contains visits")
                verify(z.getEntry("sessions/${forward.dir.name}/node_marks.csv") != null, "Zip contains audit")
                verify(z.getEntry("sessions/${forward.dir.name}/route_snapshot.json") != null, "Zip contains snapshot")
            }
            result.putString("stream", "Walk UI and export: $checks checks passed\n")
            result.putString("export_path", zip.absolutePath)
            resultCode = Activity.RESULT_OK
        } catch (e: Throwable) {
            result.putString("stream", e.stackTraceToString())
        } finally {
            if (::activity.isInitialized) runOnMainSync { activity.finish() }
            if (original == null) routeFile.delete() else routeFile.writeText(original)
            // 保留 QA 导出包供主机检查；只删除本测试创建的会话。
            sessions.forEach { it.deleteRecursively() }
        }
        finish(resultCode, result)
    }
}
