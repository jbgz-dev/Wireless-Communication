package edu.bnbu.floorsense.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
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
import edu.bnbu.floorsense.app.core.ForestModel
import edu.bnbu.floorsense.app.core.FusionEngine
import edu.bnbu.floorsense.app.core.FusionReplay
import edu.bnbu.floorsense.app.core.MotionDetector
import edu.bnbu.floorsense.app.core.FloorTracker.Status
import edu.bnbu.floorsense.app.core.WifiScan
import java.io.File
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity(), SensorEventListener {

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
    private var engine: FusionEngine? = null
    private lateinit var sensors: SensorManager
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
        sensors = getSystemService(SENSOR_SERVICE) as SensorManager
        tone = try { ToneGenerator(AudioManager.STREAM_NOTIFICATION, 100) } catch (_: Exception) { null }

        ActivityCompat.requestPermissions(
            this, arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION), 1,
        )
        btnRun.isEnabled = false
        btnReplay.isEnabled = false
        tvStatus.text = "正在加载模型…"
        io.execute {
            val m = try {
                val name = assets.list("").orEmpty().firstOrNull { it == MODEL_ASSET || it == "$MODEL_ASSET.gz" }
                    ?: error("APK 里没有 $MODEL_ASSET")
                ForestModel.load(assets.open(name))
            } catch (e: Exception) {
                ui.post { tvStatus.text = "模型加载失败：${e.message}" }
                return@execute
            }
            ui.post {
                model = m
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
        sensors.unregisterListener(this)
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
            sensors.unregisterListener(this)
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
        // 没有陀螺仪的手机退回 D2 的加速度计规则
        engine = FusionEngine(m, useGyro = sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE) != null)
        scanner = (s ?: WifiScanner(this) { ui.post { onScan(it) } }).also { it.start() }
        for (type in intArrayOf(Sensor.TYPE_ACCELEROMETER, Sensor.TYPE_GRAVITY, Sensor.TYPE_GYROSCOPE)) {
            // 和采集 App 一样用 SENSOR_DELAY_GAME（约 50 Hz）；检测器内部重采样到 25 Hz
            sensors.getDefaultSensor(type)?.let { sensors.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME, ui) }
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        btnRun.text = "停止"
        render(engine!!.state)
    }

    private fun onScan(scan: WifiScan) {
        val t = engine ?: return
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

    override fun onSensorChanged(e: SensorEvent) {
        val eng = engine ?: return
        val t = e.timestamp / 1e6  // elapsedRealtimeNanos → 毫秒，和 Wi-Fi 的 elapsedMs 同一时钟
        val v = e.values
        val found = when (e.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> eng.onAccelerometer(t, v[0].toDouble(), v[1].toDouble(), v[2].toDouble())
            Sensor.TYPE_GRAVITY -> eng.onGravity(t, v[0].toDouble(), v[1].toDouble(), v[2].toDouble())
            Sensor.TYPE_GYROSCOPE -> eng.onGyroscope(t, v[0].toDouble(), v[1].toDouble(), v[2].toDouble())
            else -> emptyList()
        }
        if (found.isNotEmpty()) {
            beep()
            render(eng.state)
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    private val ticker = object : Runnable {
        override fun run() {
            engine?.let { e -> if (scanner?.isRunning == true) render(e.tick(System.currentTimeMillis())) }
            ui.postDelayed(this, 1000)
        }
    }

    private fun render(s: FloorTracker.State) {
        val dim = s.status != Status.OK
        tvFloor.text = s.floor?.let { "${it}F" } ?: "—"
        tvFloor.alpha = if (dim) 0.4f else 1f
        tvFloorScore.text = if (s.floor == null) {
            if (s.status == Status.WAITING) "" else "还不确定在哪一层（把握 %.2f）".format(s.floorScore)
        } else "楼层把握 %.2f".format(s.floorScore)
        tvZone.text = s.zone?.let { "区域：${ZONE_NAMES[it] ?: it}（%.2f）".format(s.zoneScore) } ?: ""
        val (text, color) = when (s.status) {
            Status.WAITING -> "等待新的 Wi-Fi 扫描…" to Color.parseColor("#455A64")
            Status.OK -> "正常" to Color.parseColor("#2E7D32")
            Status.LOW_SIGNAL -> "信号不足：只认出 ${s.knownAps} 个 AP，保持上一次结果" to Color.parseColor("#EF6C00")
            Status.LOW_VOTE -> "信号弱，待更新：这次扫描把握不够，保持上一次结果" to Color.parseColor("#EF6C00")
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
            engine?.lastTransition?.let { tr ->
                val ago = (android.os.SystemClock.elapsedRealtime() - tr.t1Ms) / 1000
                val kind = if (tr.kind == MotionDetector.Kind.ELEVATOR) "电梯" else "楼梯"
                append("\n上次跨层：$kind %+d 层（%d 秒前）".format(tr.delta, ago.toLong()))
            }
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
        val dirs = root.listFiles().orEmpty().filter { File(it, "wifi.csv").exists() && File(it, "sensors.csv").exists() }.sortedBy { it.name }
        if (dirs.isEmpty()) {
            tvReplay.text = "没有找到 session。用 adb 推送到：\n${root.absolutePath}/<session文件夹>/（wifi.csv + sensors.csv）"
            return
        }
        btnReplay.isEnabled = false
        tvReplay.text = "回放 ${dirs.size} 个 session…"
        io.execute {
            val text = buildString {
                appendLine("session                     检测      纯Wi-Fi   融合  到达后Wi-Fi 到达后融合")
                for (d in dirs) {
                    try {
                        val r = FusionReplay.run(d, m)
                        fun pc(k: Int, n: Int) = if (n == 0) "—" else "%.0f%%".format(100.0 * k / n)
                        appendLine(
                            "%-26s %-9s %6s %6s %8s %8s".format(
                                d.name.take(26), r.summary(), pc(r.wifiOnlyCorrect, r.n), pc(r.fusedCorrect, r.n),
                                pc(r.afterWifiOnlyCorrect, r.afterN), pc(r.afterFusedCorrect, r.afterN),
                            ),
                        )
                    } catch (e: Exception) {
                        appendLine("${d.name}: 读取失败 ${e.message}")
                    }
                }
                appendLine()
                appendLine("检测：传感器检测到的跨层；纯Wi-Fi：只用 Wi-Fi 平滑的界面楼层准确率；融合：Wi-Fi + 传感器。")
                appendLine("到达后：move_end 之后的扫描。移动途中不计分；界面显示“—”算错。需要 session 里有 sensors.csv。")
            }
            ui.post {
                tvReplay.text = text
                btnReplay.isEnabled = true
            }
        }
    }

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
        /**
         * C 交付的冻结版 V1：源码里是 wifi_model.json.gz（SHA-256 e018a4f7…，和 D3 静态测试用的是同一个文件），
         * 打包后 aapt 解压成 wifi_model.json。
         */
        private const val MODEL_ASSET = "wifi_model.json"
        private const val ZONE_UNKNOWN = "Zone 未知"
        private val ZONES = listOf(ZONE_UNKNOWN, "West", "Middle", "East")
        private val ZONE_NAMES = mapOf("West" to "西区", "Middle" to "中区", "East" to "东区")
    }
}
