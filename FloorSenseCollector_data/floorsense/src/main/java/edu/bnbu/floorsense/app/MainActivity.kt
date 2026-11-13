package edu.bnbu.floorsense.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.location.LocationManager
import android.media.AudioManager
import android.media.ToneGenerator
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.WindowManager
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import edu.bnbu.floorsense.app.core.FloorModel
import edu.bnbu.floorsense.app.core.FloorTracker
import edu.bnbu.floorsense.app.core.FloorTracker.Status
import edu.bnbu.floorsense.app.core.KnnModel
import edu.bnbu.floorsense.app.core.ReplayRunner
import edu.bnbu.floorsense.app.core.WifiScan
import java.io.File
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private lateinit var tvEnv: TextView
    private lateinit var tvFloor: TextView
    private lateinit var tvFloorScore: TextView
    private lateinit var tvZone: TextView
    private lateinit var tvStatus: TextView
    private lateinit var tvDetail: TextView
    private lateinit var tvEval: TextView
    private lateinit var tvReplay: TextView
    private lateinit var btnRun: Button
    private lateinit var btnRecord: Button
    private lateinit var btnReplay: Button
    private lateinit var etPoint: EditText
    private lateinit var etTrueFloor: EditText
    private lateinit var spTrueZone: Spinner

    private val ui = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()

    private var model: FloorModel? = null
    private var tracker: FloorTracker? = null
    private var scanner: WifiScanner? = null
    private var recorder: EvalRecorder? = null
    private var tone: ToneGenerator? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        tvEnv = findViewById(R.id.tvEnv)
        tvFloor = findViewById(R.id.tvFloor)
        tvFloorScore = findViewById(R.id.tvFloorScore)
        tvZone = findViewById(R.id.tvZone)
        tvStatus = findViewById(R.id.tvStatus)
        tvDetail = findViewById(R.id.tvDetail)
        tvEval = findViewById(R.id.tvEval)
        tvReplay = findViewById(R.id.tvReplay)
        btnRun = findViewById(R.id.btnRun)
        btnRecord = findViewById(R.id.btnRecord)
        btnReplay = findViewById(R.id.btnReplay)
        etPoint = findViewById(R.id.etPoint)
        etTrueFloor = findViewById(R.id.etTrueFloor)
        spTrueZone = findViewById(R.id.spTrueZone)
        spTrueZone.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, ZONES)

        btnRun.setOnClickListener { toggleRun() }
        btnRecord.setOnClickListener { onRecord() }
        findViewById<Button>(R.id.btnExport).setOnClickListener { onExport() }
        btnReplay.setOnClickListener { onReplay() }
        tone = try { ToneGenerator(AudioManager.STREAM_NOTIFICATION, 100) } catch (_: Exception) { null }

        ActivityCompat.requestPermissions(
            this, arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION), 1,
        )
        btnRun.isEnabled = false
        btnReplay.isEnabled = false
        tvStatus.text = "正在加载模型…"
        io.execute {
            val m = try {
                KnnModel.fromJson(assets.open(MODEL_ASSET).bufferedReader().use { it.readText() })
            } catch (e: Exception) {
                ui.post { tvStatus.text = "模型加载失败：${e.message}" }
                return@execute
            }
            ui.post {
                model = m
                tracker = FloorTracker(m)
                btnRun.isEnabled = true
                btnReplay.isEnabled = true
                tvStatus.text = "模型：${m.name}\n按“开始识别”"
            }
        }
        refreshEnv()
        ui.post(ticker)
    }

    override fun onResume() {
        super.onResume()
        refreshEnv()
    }

    override fun onDestroy() {
        scanner?.stop()
        recorder?.close()
        tone?.release()
        ui.removeCallbacksAndMessages(null)
        io.shutdown()
        super.onDestroy()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        refreshEnv()
    }

    // ---------- 实时识别 ----------

    private fun toggleRun() {
        val s = scanner
        if (s != null && s.isRunning) {
            s.stop()
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            btnRun.text = "开始识别"
            return
        }
        val problems = envProblems()
        if (problems.isNotEmpty()) {
            toast(problems.first())
            refreshEnv()
            return
        }
        val m = model ?: return
        tracker = FloorTracker(m)
        scanner = (s ?: WifiScanner(this) { ui.post { onScan(it) } }).also { it.start() }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        btnRun.text = "停止"
        render(tracker!!.state)
    }

    private fun onScan(scan: WifiScan) {
        val t = tracker ?: return
        val u = t.onScan(scan)
        val r = recorder
        if (r != null && r.onScan(scan, u)) {
            beep()
            btnRecord.text = "记录这个点"
            tvEval.text = r.summary()
        } else if (r?.recording == true) {
            btnRecord.text = "记录中 ${r.progress}/${EvalRecorder.SCANS_PER_POINT}（点击取消）"
        }
        render(u.state)
    }

    private val ticker = object : Runnable {
        override fun run() {
            tracker?.let { t -> if (scanner?.isRunning == true) render(t.tick(System.currentTimeMillis())) }
            ui.postDelayed(this, 1000)
        }
    }

    private fun render(s: FloorTracker.State) {
        val dim = s.status != Status.OK
        tvFloor.text = s.floor?.let { "${it}F" } ?: "—"
        tvFloor.alpha = if (dim) 0.4f else 1f
        tvFloorScore.text = if (s.floor == null) "" else "楼层投票分 %.2f（平滑 %d 次）".format(s.floorScore, s.windowSize)
        tvZone.text = s.zone?.let { "区域：${ZONE_NAMES[it] ?: it}（%.2f）".format(s.zoneScore) } ?: ""
        val (text, color) = when (s.status) {
            Status.WAITING -> "等待新的 Wi-Fi 扫描…" to Color.parseColor("#455A64")
            Status.OK -> "正常" to Color.parseColor("#2E7D32")
            Status.LOW_SIGNAL -> "信号不足：只认出 ${s.knownAps} 个 AP，保持上一次结果" to Color.parseColor("#EF6C00")
            Status.STALE -> "扫描过期：超过 15 秒没有新扫描，保持上一次结果" to Color.parseColor("#C62828")
            Status.MOVING -> "正在跨层移动…" to Color.parseColor("#1565C0")
        }
        tvStatus.text = text
        tvStatus.setBackgroundColor(color)
        tvStatus.setTextColor(Color.WHITE)
        val age = s.lastFreshMs?.let { (System.currentTimeMillis() - it) / 1000 }
        val sc = scanner
        tvDetail.text = buildString {
            append("已知 AP ${s.knownAps} / 扫到 ${s.observedAps}")
            if (age != null) append("　上次新扫描 ${age} 秒前")
            if (sc != null) append("\n扫描请求 ${sc.requests} 次，被拒绝 ${sc.rejected} 次")
        }
    }

    // ---------- 评估模式 ----------

    private fun onRecord() {
        val r0 = recorder
        if (r0?.recording == true) {
            r0.cancel()
            btnRecord.text = "记录这个点"
            return
        }
        if (scanner?.isRunning != true) { toast("先按“开始识别”"); return }
        val point = etPoint.text.toString().trim()
        val floor = etTrueFloor.text.toString().trim().toIntOrNull()
        if (point.isEmpty() || floor == null) { toast("请填测试点编号和真实楼层"); return }
        val zone = ZONES[spTrueZone.selectedItemPosition].takeIf { it != ZONE_UNKNOWN }
        val r = recorder ?: EvalRecorder(File(getExternalFilesDir(null), "eval"), model!!.name).also { recorder = it }
        r.begin(EvalRecorder.Target(point, floor, zone), System.currentTimeMillis())
        btnRecord.text = "记录中 0/${EvalRecorder.SCANS_PER_POINT}（点击取消）"
        tvEval.text = r.summary() + "\n保存位置：${r.dir.absolutePath}"
    }

    private fun onExport() {
        val r = recorder ?: run { toast("还没有评估数据"); return }
        io.execute {
            val zip = r.zip()
            ui.post {
                val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", zip)
                val share = Intent(Intent.ACTION_SEND).setType("application/zip")
                    .putExtra(Intent.EXTRA_STREAM, uri)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                startActivity(Intent.createChooser(share, "导出评估数据"))
            }
        }
    }

    // ---------- 回放模式 ----------

    /**
     * 回放 getExternalFilesDir/replay/ 下的每个 session 文件夹。用 adb 把 Collector 的 session 放进去：
     * adb push <session文件夹> /sdcard/Android/data/edu.bnbu.floorsense.app/files/replay/
     */
    private fun onReplay() {
        val m = model ?: return
        val root = File(getExternalFilesDir(null), "replay").apply { mkdirs() }
        val dirs = root.listFiles().orEmpty().filter { File(it, "wifi.csv").exists() }.sortedBy { it.name }
        if (dirs.isEmpty()) {
            tvReplay.text = "没有找到 session。用 adb 推送到：\n${root.absolutePath}/<session文件夹>/wifi.csv"
            return
        }
        btnReplay.isEnabled = false
        tvReplay.text = "回放 ${dirs.size} 个 session…"
        io.execute {
            val text = buildString {
                appendLine("session                       纯Wi-Fi  +事件  单次  到达后")
                for (d in dirs) {
                    try {
                        val base = ReplayRunner.run(d, m, useEvents = false)
                        val ev = ReplayRunner.run(d, m, useEvents = true)
                        appendLine(
                            "%-28s %6s %6s %6s %6s".format(
                                d.name.take(28), pct(base.accuracy), pct(ev.accuracy), pct(base.rawAccuracy),
                                ev.settleMs?.let { "%.1fs".format(it / 1000.0) } ?: "—",
                            ),
                        )
                    } catch (e: Exception) {
                        appendLine("${d.name}: 读取失败 ${e.message}")
                    }
                }
                appendLine()
                appendLine("纯Wi-Fi：界面楼层准确率；+事件：用 events.csv 的移动标记模拟跨层检测；")
                appendLine("单次：不平滑的单次扫描准确率；到达后：move_end 后多久显示正确楼层。移动途中不计分。")
            }
            ui.post {
                tvReplay.text = text
                btnReplay.isEnabled = true
            }
        }
    }

    private fun pct(v: Double) = if (v.isNaN()) "—" else "%.1f%%".format(v * 100)

    // ---------- 环境检查 ----------

    private fun envProblems(): List<String> = buildList {
        if (ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.ACCESS_FINE_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) add("未授予精确定位权限")
        val lm = getSystemService(LOCATION_SERVICE) as LocationManager
        if (!lm.isLocationEnabled) add("系统定位未开启（不开定位扫不到 Wi-Fi）")
        val wm = applicationContext.getSystemService(WIFI_SERVICE) as WifiManager
        if (!wm.isWifiEnabled) add("Wi-Fi 未开启")
    }

    private fun refreshEnv() {
        val problems = envProblems()
        val wm = applicationContext.getSystemService(WIFI_SERVICE) as WifiManager
        val throttled = if (Build.VERSION.SDK_INT >= 30) wm.isScanThrottleEnabled else Build.VERSION.SDK_INT == 28
        val warn = if (throttled) listOf("Wi-Fi 扫描节流开启，每 2 分钟只有 4 次新扫描，请在开发者选项里关闭") else emptyList()
        val lines = problems + warn
        tvEnv.text = if (lines.isEmpty()) "环境正常" else lines.joinToString("\n")
        tvEnv.setBackgroundColor(
            Color.parseColor(if (problems.isNotEmpty()) "#C62828" else if (warn.isNotEmpty()) "#EF6C00" else "#2E7D32"),
        )
        tvEnv.setTextColor(Color.WHITE)
    }

    private fun beep() {
        tone?.startTone(ToneGenerator.TONE_PROP_BEEP, 200)
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        private const val MODEL_ASSET = "knn_reference.json"
        private const val ZONE_UNKNOWN = "Zone 未知"
        private val ZONES = listOf(ZONE_UNKNOWN, "West", "Middle", "East")
        private val ZONE_NAMES = mapOf("West" to "西区", "Middle" to "中区", "East" to "东区")
    }
}
