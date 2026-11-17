package edu.bnbu.floorsense.collector

import android.content.Intent
import android.graphics.Color
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.FileProvider
import java.io.File
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity(), SensorEventListener {

    private lateinit var tvEnv: TextView
    private lateinit var tvCompass: TextView
    private lateinit var tvLive: TextView
    private lateinit var tvSummary: TextView
    private lateinit var form: LinearLayout
    private lateinit var etCollector: EditText
    private lateinit var etBuilding: EditText
    private lateinit var etFloor: EditText
    private lateinit var etZone: EditText
    private lateinit var etPoint: EditText
    private lateinit var etTarget: EditText
    private lateinit var etToFloor: EditText
    private lateinit var etNotes: EditText
    private lateinit var spMode: Spinner
    private lateinit var spSplit: Spinner
    private lateinit var spOrientation: Spinner
    private lateinit var spPosture: Spinner
    private lateinit var spMethod: Spinner
    private lateinit var cbCycle: CheckBox
    private lateinit var cbAutoInc: CheckBox
    private lateinit var cbReverseWalk: CheckBox
    private lateinit var tvWalkProgress: TextView
    private lateinit var btnNodeReached: Button
    private lateinit var btnUndoNode: Button
    private lateinit var tvNext: TextView
    private lateinit var btnStart: Button
    private lateinit var btnStop: Button
    private lateinit var btnMoveStart: Button
    private lateinit var btnFloorPassed: Button
    private lateinit var btnArrived: Button

    private val ui = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()
    private val prefs by lazy { getSharedPreferences("form", MODE_PRIVATE) }
    private val sm by lazy { getSystemService(SENSOR_SERVICE) as SensorManager }
    private var tone: ToneGenerator? = null

    private var session: Session? = null
    private var lastDir: File? = null
    private var moveStarted = false
    private var moveEnded = false
    private var floorsPassed = 0
    private var compassDeg = Double.NaN
    private var tickCount = 0
    private val rot = FloatArray(9)
    private val ori = FloatArray(3)

    // ---------- 生命周期 ----------

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        bindViews()
        setupSpinners()
        loadPrefs()
        tone = try { ToneGenerator(AudioManager.STREAM_NOTIFICATION, 100) } catch (_: Exception) { null }

        btnStart.setOnClickListener { onStartClicked() }
        btnStop.setOnClickListener { onStopClicked() }
        btnMoveStart.setOnClickListener { onMoveStart() }
        btnFloorPassed.setOnClickListener { onFloorPassed() }
        btnArrived.setOnClickListener { onArrived() }
        btnNodeReached.setOnClickListener { onNodeReached() }
        btnUndoNode.setOnClickListener { onUndoNode() }
        findViewById<Button>(R.id.btnDiscard).setOnClickListener { onDiscard() }
        findViewById<Button>(R.id.btnExport).setOnClickListener { onExport() }
        findViewById<Button>(R.id.btnEdit).setOnClickListener { onEditRecords() }
        findViewById<Button>(R.id.btnRoutes).setOnClickListener { onRoutes() }
        findViewById<Button>(R.id.btnBatchDelete).setOnClickListener { onBatchDelete() }

        ActivityCompat.requestPermissions(this, Env.requiredPermissions(), 1)
        updateUiState()
        refreshEnv()
        refreshSummary()
        ui.post(ticker)
    }

    override fun onResume() {
        super.onResume()
        sm.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)?.let {
            sm.registerListener(this, it, SensorManager.SENSOR_DELAY_UI)
        }
        refreshEnv()
    }

    override fun onPause() {
        super.onPause()
        sm.unregisterListener(this)
    }

    override fun onDestroy() {
        session?.stop(complete = false)
        session = null
        ui.removeCallbacksAndMessages(null)
        tone?.release()
        io.shutdown()
        super.onDestroy()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        refreshEnv()
    }

    // ---------- 界面初始化 ----------

    private fun bindViews() {
        tvEnv = findViewById(R.id.tvEnv)
        tvCompass = findViewById(R.id.tvCompass)
        tvLive = findViewById(R.id.tvLive)
        tvSummary = findViewById(R.id.tvSummary)
        form = findViewById(R.id.form)
        etCollector = findViewById(R.id.etCollector)
        etBuilding = findViewById(R.id.etBuilding)
        etFloor = findViewById(R.id.etFloor)
        etZone = findViewById(R.id.etZone)
        etPoint = findViewById(R.id.etPoint)
        etTarget = findViewById(R.id.etTarget)
        etToFloor = findViewById(R.id.etToFloor)
        etNotes = findViewById(R.id.etNotes)
        spMode = findViewById(R.id.spMode)
        spSplit = findViewById(R.id.spSplit)
        spOrientation = findViewById(R.id.spOrientation)
        spPosture = findViewById(R.id.spPosture)
        spMethod = findViewById(R.id.spMethod)
        cbCycle = findViewById(R.id.cbCycle)
        cbAutoInc = findViewById(R.id.cbAutoInc)
        cbReverseWalk = findViewById(R.id.cbReverseWalk)
        tvWalkProgress = findViewById(R.id.tvWalkProgress)
        btnNodeReached = findViewById(R.id.btnNodeReached)
        btnUndoNode = findViewById(R.id.btnUndoNode)
        tvNext = findViewById(R.id.tvNext)
        btnStart = findViewById(R.id.btnStart)
        btnStop = findViewById(R.id.btnStop)
        btnMoveStart = findViewById(R.id.btnMoveStart)
        btnFloorPassed = findViewById(R.id.btnFloorPassed)
        btnArrived = findViewById(R.id.btnArrived)
    }

    private fun setupSpinners() {
        spMode.setOptions(MODES)
        spSplit.setOptions(SPLITS)
        spOrientation.setOptions(ORIENTATIONS)
        spPosture.setOptions(POSTURES)
        spMethod.setOptions(METHODS)
        spMode.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, id: Long) = updateModeUi()
            override fun onNothingSelected(p: AdapterView<*>?) {}
        }
    }

    private fun updateModeUi() {
        val mode = spMode.code()
        findViewById<View>(R.id.groupPoint).visibility = if (mode == "transition") View.GONE else View.VISIBLE
        findViewById<View>(R.id.groupTransition).visibility = if (mode == "transition") View.VISIBLE else View.GONE
        findViewById<View>(R.id.groupMarks).visibility = if (mode == "transition") View.VISIBLE else View.GONE
        findViewById<View>(R.id.groupWalkOptions).visibility = if (mode == "walk") View.VISIBLE else View.GONE
        findViewById<View>(R.id.rowOrientation).visibility = if (mode == "static") View.VISIBLE else View.GONE
        findViewById<View>(R.id.rowTarget).visibility = if (mode == "static") View.VISIBLE else View.GONE
        cbCycle.visibility = if (mode == "static") View.VISIBLE else View.GONE
        cbAutoInc.text = if (mode == "static") "自动递增点位编号（默认关闭；开着朝向轮换时，W 采完才加 1）"
        else "自动递增路线编号（默认关闭）"
        findViewById<TextView>(R.id.tvPointLabel).text = if (mode == "walk") "路线编号" else "点位编号"
        etPoint.hint = if (mode == "walk") "训练 TW01… / 测试 XW01…" else "训练 T01… / 测试 X01…"
    }

    private fun updateUiState() {
        val rec = session != null
        setEnabledRecursive(form, !rec)
        btnStart.isEnabled = !rec
        btnStop.isEnabled = rec
        val inTransition = rec && session?.form?.mode == "transition"
        btnMoveStart.isEnabled = inTransition && !moveStarted
        btnFloorPassed.isEnabled = inTransition && moveStarted && !moveEnded
        btnArrived.isEnabled = inTransition && moveStarted && !moveEnded
        updateWalkUi()
        if (rec) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    private fun setEnabledRecursive(v: View, enabled: Boolean) {
        v.isEnabled = enabled
        if (v is ViewGroup) for (i in 0 until v.childCount) setEnabledRecursive(v.getChildAt(i), enabled)
    }

    // ---------- 表单 ----------

    private fun readForm(): Form? {
        val floor = etFloor.text.toString().trim().toIntOrNull()
        if (floor == null) { toast("请填写楼层（整数）"); return null }
        val mode = spMode.code()
        val f = Form(
            collector = etCollector.text.toString().trim(),
            building = etBuilding.text.toString().trim().uppercase(),
            floor = floor,
            zone = etZone.text.toString().trim(),
            pointId = if (mode == "transition") "" else etPoint.text.toString().trim().uppercase(),
            split = spSplit.code(),
            mode = mode,
            orientation = if (mode == "static") spOrientation.code() else "free",
            posture = spPosture.code(),
            targetScans = if (mode == "static") etTarget.text.toString().toIntOrNull() ?: 0 else 0,
            toFloor = if (mode == "transition") etToFloor.text.toString().trim().toIntOrNull() else null,
            method = if (mode == "transition") spMethod.code() else "",
            notes = etNotes.text.toString().trim(),
            reverseWalk = mode == "walk" && cbReverseWalk.isChecked,
        )
        f.validate()?.let { toast(it); return null }
        return f
    }

    private fun loadPrefs() {
        etCollector.setText(prefs.getString("collector", ""))
        etBuilding.setText(prefs.getString("building", ""))
        etFloor.setText(prefs.getString("floor", ""))
        etZone.setText(prefs.getString("zone", ""))
        etPoint.setText(prefs.getString("point", "T01"))
        etTarget.setText(prefs.getString("target", "5"))
        etToFloor.setText(prefs.getString("to_floor", ""))
        spMode.selectCode(prefs.getString("mode", "static")!!)
        spSplit.selectCode(prefs.getString("split", "train")!!)
        spOrientation.selectCode(prefs.getString("orientation", "N")!!)
        spPosture.selectCode(prefs.getString("posture", "hand_flat")!!)
        spMethod.selectCode(prefs.getString("method", "stairs")!!)
        cbCycle.isChecked = prefs.getBoolean("cycle", true)
        cbAutoInc.isChecked = prefs.getBoolean("auto_inc", false)
        cbReverseWalk.isChecked = prefs.getBoolean("reverse_walk", false)
        updateModeUi()
    }

    private fun savePrefs() {
        prefs.edit()
            .putString("collector", etCollector.text.toString())
            .putString("building", etBuilding.text.toString())
            .putString("floor", etFloor.text.toString())
            .putString("zone", etZone.text.toString())
            .putString("point", etPoint.text.toString())
            .putString("target", etTarget.text.toString())
            .putString("to_floor", etToFloor.text.toString())
            .putString("mode", spMode.code())
            .putString("split", spSplit.code())
            .putString("orientation", spOrientation.code())
            .putString("posture", spPosture.code())
            .putString("method", spMethod.code())
            .putBoolean("cycle", cbCycle.isChecked)
            .putBoolean("auto_inc", cbAutoInc.isChecked)
            .putBoolean("reverse_walk", cbReverseWalk.isChecked)
            .apply()
    }

    // ---------- 开始 / 停止 ----------

    private fun onStartClicked() {
        if (session != null) return
        val f = readForm() ?: return
        val walk = if (f.mode == "walk") {
            val data = RouteStore.load(this)
            val route = data.routes.firstOrNull {
                it.building == f.building && it.floor == f.floor && it.routeId == f.pointId
            }
            val progress = try {
                require(data.warning == null) { data.warning ?: "路线读取失败" }
                require(route != null) { "找不到 ${f.building} ${f.floor}F 的路线 ${f.pointId}，请先添加路线与节点" }
                WalkProgress(route.nodes.mapIndexed { i, n -> WalkNode(n.id, i + 1, n.xM, n.yM) }, f.reverseWalk)
            } catch (e: IllegalArgumentException) {
                AlertDialog.Builder(this).setTitle("请先设置行走路线")
                    .setMessage(e.message).setPositiveButton("路线与节点") { _, _ -> onRoutes() }
                    .setNegativeButton("取消", null).show()
                return
            }
            progress
        } else null
        val env = Env.snapshot(this)
        val blockers = env.blockers()
        if (blockers.isNotEmpty()) {
            AlertDialog.Builder(this).setTitle("无法开始")
                .setMessage(blockers.joinToString("\n"))
                .setPositiveButton("授予权限") { _, _ ->
                    ActivityCompat.requestPermissions(this, Env.requiredPermissions(), 1)
                }
                .setNegativeButton("取消", null).show()
            return
        }
        SessionStore.conflictingSplit(this, f)?.let { other ->
            AlertDialog.Builder(this).setTitle("点位冲突（会造成数据泄漏）")
                .setMessage("${f.building} ${f.floor}F 的点位 ${f.pointId} 已经作为 [$other] 采集过，" +
                    "不能再作为 [${f.split}] 采集。\n\n测试点必须是训练集里没有的位置，请换一个编号和位置。")
                .setPositiveButton("知道了", null).show()
            return
        }
        val warnings = env.warnings()
        if (warnings.isEmpty()) begin(f, walk)
        else AlertDialog.Builder(this).setTitle("注意")
            .setMessage(warnings.joinToString("\n") + "\n\n仍然开始？")
            .setPositiveButton("开始") { _, _ -> begin(f, walk) }
            .setNegativeButton("取消", null).show()
    }

    private fun begin(f: Form, walk: WalkProgress?) {
        if (session != null) return
        savePrefs()
        moveStarted = false
        moveEnded = false
        floorsPassed = 0
        val s = Session(this, f, Session.newDir(this, f), walk)
        s.start()
        session = s
        updateUiState()
        if (f.mode == "transition") {
            toast("先原地站 10 秒，再按“开始移动”")
        }
        if (walk != null) {
            toast("已记录起点 ${walk.nodes.first().id}，经过下一个节点时点击到达")
            findViewById<android.widget.ScrollView>(R.id.mainScroll).post {
                findViewById<android.widget.ScrollView>(R.id.mainScroll).smoothScrollTo(0, tvNext.top)
            }
        }
    }

    private fun onStopClicked() {
        val s = session ?: return
        val f = s.form
        val complete = when (f.mode) {
            "static" -> s.wifi.scansFresh >= f.targetScans
            "transition" -> moveEnded
            "walk" -> s.walk?.complete == true
            else -> s.wifi.scansFresh >= MIN_WALK_SCANS
        }
        if (complete) { endSession(complete = true); return }
        val reason = when (f.mode) {
            "static" -> "新扫描只有 ${s.wifi.scansFresh}/${f.targetScans} 次"
            "transition" -> "还没有按“到达目标层”"
            "walk" -> "路线节点只记录了 ${s.walk?.reachedCount}/${s.walk?.nodes?.size} 个，尚未记录终点"
            else -> "新扫描少于 $MIN_WALK_SCANS 次"
        }
        AlertDialog.Builder(this).setTitle("提前停止？")
            .setMessage("$reason，这条记录会被标记为 incomplete，处理时默认跳过。")
            .setPositiveButton("停止") { _, _ -> endSession(complete = false) }
            .setNegativeButton("继续采集", null).show()
    }

    private fun endSession(complete: Boolean) {
        val s = session ?: return
        s.stop(complete)
        session = null
        lastDir = s.dir
        updateUiState()
        if (complete) {
            tone?.startTone(ToneGenerator.TONE_PROP_ACK, 300)
            when (s.form.mode) {
                "transition" -> etFloor.setText(s.form.toFloor.toString())
                else -> advanceAfter(s.form)
            }
            savePrefs()
        }
        tvLive.text = "已保存：${s.dir.name}\n状态：${if (complete) "complete" else "incomplete"}"
        if (s.walk != null && s.wifi.scansFresh < MIN_WALK_SCANS) {
            tvLive.append("\n注意：新 Wi-Fi 扫描只有 ${s.wifi.scansFresh} 次，已记录为扫描不足，请检查或补采")
        }
        refreshSummary()
    }

    /**
     * 一条静态/行走记录完成后更新表单。
     * 朝向轮换和编号递增是两个独立开关；编号递增默认关闭，防止同一位置的记录被分到别的编号。
     * 静态模式开着朝向轮换时，只有 W 采完、回到 N 时才会递增编号。
     */
    private fun advanceAfter(f: Form) {
        var pointDone = true
        if (f.mode == "static" && cbCycle.isChecked) {
            val order = ORIENTATIONS.map { it.first }
            val next = order[(order.indexOf(f.orientation) + 1) % order.size]
            spOrientation.selectCode(next)
            pointDone = next == order.first()
        }
        when {
            !pointDone -> toast("${f.pointId} 下一个朝向：${spOrientation.code()}")
            cbAutoInc.isChecked -> {
                etPoint.setText(nextId(f.pointId))
                toast("${f.pointId} 已完成，编号自动改为 ${etPoint.text}")
            }
            else -> toast("${f.pointId} 已完成。换位置后请手动修改点位编号")
        }
    }

    /** “下一条”提示：开始前一眼能看到这条记录会被标成什么。 */
    private fun renderNext() {
        val f = session?.form
        val prefix = if (f != null) "正在采集" else "下一条"
        val mode = f?.mode ?: spMode.code()
        val building = f?.building ?: etBuilding.text.toString().trim().uppercase()
        val floor = f?.floor?.toString() ?: etFloor.text.toString().trim()
        val split = f?.split ?: spSplit.code()
        tvNext.text = when (mode) {
            "transition" -> {
                val to = f?.toFloor?.toString() ?: etToFloor.text.toString().trim()
                val method = f?.method ?: spMethod.code()
                "$prefix：$building ${floor}F → ${to}F  $method  [$split]"
            }
            "walk" -> {
                val point = f?.pointId ?: etPoint.text.toString().trim().uppercase()
                "$prefix：$building ${floor}F  路线 $point  行走  [$split]"
            }
            else -> {
                val point = f?.pointId ?: etPoint.text.toString().trim().uppercase()
                val ori = f?.orientation ?: spOrientation.code()
                val posture = f?.posture ?: spPosture.code()
                "$prefix：$building ${floor}F  点位 $point  朝向 $ori  $posture  [$split]"
            }
        }
    }

    // ---------- 跨层标记 ----------

    private fun updateWalkUi() {
        val w = session?.walk
        findViewById<View>(R.id.groupWalkMarks).visibility = if (w == null) View.GONE else View.VISIBLE
        btnNodeReached.isEnabled = w != null && !w.complete
        btnUndoNode.isEnabled = w?.canUndo == true
        if (w == null) return
        tvWalkProgress.text = "${if (w.reverse) "反向" else "正向"}：${w.nodes.joinToString(" → ") { it.id }}\n" +
            "已记录 ${w.reachedCount}/${w.nodes.size} 个节点；最近：${w.visits.lastOrNull()?.node?.id ?: "—"}"
        btnNodeReached.text = w.nextNode?.let { "到达 ${it.id}（第 ${w.reachedCount + 1}/${w.nodes.size} 个）" }
            ?: "终点已记录，请按停止"
    }

    private fun onNodeReached() {
        val s = session ?: return
        val visit = s.recordNextNode() ?: run { toast("请勿连续点击；实际到达下一个节点再记录"); return }
        tone?.startTone(ToneGenerator.TONE_PROP_BEEP, 100)
        updateWalkUi()
        toast("已记录 ${visit.node.id}" + if (s.walk?.complete == true) "，可按停止结束本条路线" else "")
    }

    private fun onUndoNode() {
        val s = session ?: return
        val visit = s.undoNode() ?: return
        updateWalkUi()
        toast("已撤销 ${visit.node.id}，到达时请重新记录")
    }

    private fun onMoveStart() {
        val s = session ?: return
        s.mark("move_start", "${s.form.floor}->${s.form.toFloor}:${s.form.method}")
        moveStarted = true
        updateUiState()
    }

    private fun onFloorPassed() {
        val s = session ?: return
        val dir = if ((s.form.toFloor ?: s.form.floor) > s.form.floor) 1 else -1
        floorsPassed++
        s.mark("floor_passed", (s.form.floor + dir * floorsPassed).toString())
    }

    private fun onArrived() {
        val s = session ?: return
        s.mark("move_end", s.form.toFloor.toString())
        moveEnded = true
        updateUiState()
        toast("在目标层原地站 10 秒，再按“停止”")
    }

    // ---------- 删除 / 导出 ----------

    private fun onDiscard() {
        val d = lastDir ?: run { toast("没有可删除的记录"); return }
        AlertDialog.Builder(this).setTitle("删除上一条记录？")
            .setMessage(d.name)
            .setPositiveButton("删除") { _, _ ->
                val labels = try {
                    org.json.JSONObject(File(d, "meta.json").readText()).getJSONObject("labels")
                } catch (_: Exception) { null }
                d.deleteRecursively()
                lastDir = null
                labels?.let { restoreForm(it) }
                tvLive.text = "已删除：${d.name}\n表单已恢复成被删除那条记录的标签，可以直接重采"
                refreshSummary()
            }
            .setNegativeButton("取消", null).show()
    }

    /** 把表单恢复成某条记录的标签（删除后重采用），避免沿用自动轮换/递增后的值。 */
    private fun restoreForm(l: org.json.JSONObject) {
        etFloor.setText(l.optInt("floor").toString())
        etZone.setText(l.optString("zone"))
        spPosture.selectCode(l.optString("posture"))
        if (l.optString("mode") == "transition") {
            etToFloor.setText(l.optInt("to_floor").toString())
        } else {
            etPoint.setText(l.optString("point_id"))
            if (l.optString("mode") == "static") spOrientation.selectCode(l.optString("orientation"))
        }
        savePrefs()
    }

    // ---------- 修改历史记录 ----------

    private fun onEditRecords() {
        if (session != null) { toast("请先停止当前采集"); return }
        io.execute {
            val items = SessionStore.list(this).reversed().take(50)
            ui.post {
                if (items.isEmpty()) { toast("还没有记录"); return@post }
                val names = items.map { describe(it) }.toTypedArray()
                AlertDialog.Builder(this).setTitle("选择要修改的记录（最新在上）")
                    .setItems(names) { _, i -> showEditDialog(items[i]) }
                    .setNegativeButton("取消", null).show()
            }
        }
    }

    private fun describe(i: SessionStore.Info): String {
        val l = i.labels
        val time = i.meta.optString("start_local").replace('T', ' ').drop(5).take(11)
        val what = if (i.mode == "transition") {
            "${l.optInt("floor")}F→${l.optInt("to_floor")}F ${l.optString("method")}"
        } else {
            "${l.optInt("floor")}F ${l.optString("point_id")} ${l.optString("orientation")} ${l.optString("split")}"
        }
        val edited = if (i.meta.has("labels_modified_ms")) " ✎" else ""
        return "$time  ${l.optString("building")} $what  ${i.status}$edited"
    }

    private fun showEditDialog(info: SessionStore.Info) {
        val l = info.labels
        val isTransition = info.mode == "transition"
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 16, 48, 0)
        }
        fun field(label: String, value: String, type: Int = InputType.TYPE_CLASS_TEXT): EditText {
            box.addView(TextView(this).apply { text = label })
            return EditText(this).apply { setText(value); inputType = type; box.addView(this) }
        }
        fun spinner(label: String, options: List<Pair<String, String>>, value: String): Spinner {
            box.addView(TextView(this).apply { text = label })
            return Spinner(this).apply { setOptions(options); selectCode(value); box.addView(this) }
        }
        val signed = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_SIGNED
        val eBuilding = field("楼名", l.optString("building"))
        val eFloor = field("楼层", l.optInt("floor").toString(), signed)
        val eZone = field("区域", l.optString("zone"))
        val eNotes = field("备注", l.optString("notes"))
        val sPosture = spinner("持机方式", POSTURES, l.optString("posture"))
        val ePoint = if (!isTransition) field("点位编号", l.optString("point_id")) else null
        val sSplit = if (!isTransition) spinner("数据集", SPLITS, l.optString("split")) else null
        val sOri = if (info.mode == "static") spinner("朝向", ORIENTATIONS, l.optString("orientation")) else null
        val eTo = if (isTransition) field("目标楼层", l.optInt("to_floor").toString(), signed) else null
        val sMethod = if (isTransition) spinner("方式", METHODS, l.optString("method")) else null

        val dialog = AlertDialog.Builder(this)
            .setTitle("修改 ${info.dir.name}")
            .setMessage("只改标签，原始数据和文件夹名不变。修改记录会保存在 meta.json 的 label_history 里。")
            .setView(android.widget.ScrollView(this).apply { addView(box) })
            .setPositiveButton("保存", null)
            .setNegativeButton("取消", null)
            .create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val floor = eFloor.text.toString().trim().toIntOrNull()
                if (floor == null) { toast("楼层必须是整数"); return@setOnClickListener }
                val building = eBuilding.text.toString().trim().uppercase()
                if (building.isEmpty()) { toast("楼名不能为空"); return@setOnClickListener }
                val changes = linkedMapOf<String, Any>(
                    "building" to building,
                    "floor" to floor,
                    "zone" to eZone.text.toString().trim(),
                    "notes" to eNotes.text.toString().trim(),
                    "posture" to sPosture.code(),
                )
                if (ePoint != null && sSplit != null) {
                    val point = ePoint.text.toString().trim().uppercase()
                    if (point.isEmpty()) { toast("点位编号不能为空"); return@setOnClickListener }
                    val split = sSplit.code()
                    val other = SessionStore.conflictingSplit(this, "$building|$floor|$point", split, info.dir)
                    if (other != null) {
                        toast("$building ${floor}F $point 已经作为 [$other] 采集过，不能改成 [$split]")
                        return@setOnClickListener
                    }
                    changes["point_id"] = point
                    changes["split"] = split
                }
                sOri?.let { changes["orientation"] = it.code() }
                if (eTo != null && sMethod != null) {
                    val to = eTo.text.toString().trim().toIntOrNull()
                    if (to == null || to == floor) {
                        toast("目标楼层必须是整数，并且和起始楼层不同"); return@setOnClickListener
                    }
                    changes["to_floor"] = to
                    changes["method"] = sMethod.code()
                }
                val real = changes.filter { (k, v) -> l.opt(k)?.toString() != v.toString() }
                if (real.isEmpty()) { toast("没有改动"); return@setOnClickListener }
                try {
                    SessionStore.updateLabels(info, real)
                } catch (e: Exception) {
                    toast("保存失败：${e.message}"); return@setOnClickListener
                }
                toast("已修改：" + real.keys.joinToString())
                dialog.dismiss()
                refreshSummary()
            }
        }
        dialog.show()
    }

    /** 一次勾选多条记录删除。删除前再确认一次，删掉的是整个会话文件夹，不能恢复。 */
    private fun onBatchDelete() {
        if (session != null) { toast("请先停止当前采集"); return }
        io.execute {
            val items = SessionStore.list(this).reversed()
            ui.post {
                if (items.isEmpty()) { toast("还没有记录"); return@post }
                val checked = BooleanArray(items.size)
                val dialog = AlertDialog.Builder(this).setTitle("勾选要删除的记录（最新在上）")
                    .setMultiChoiceItems(items.map { describe(it) }.toTypedArray(), checked) { _, i, on -> checked[i] = on }
                    .setPositiveButton("删除", null)
                    .setNeutralButton("勾选 incomplete", null)
                    .setNegativeButton("取消", null)
                    .create()
                dialog.setOnShowListener {
                    dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener {
                        items.forEachIndexed { i, info ->
                            if (info.status == "incomplete") { checked[i] = true; dialog.listView.setItemChecked(i, true) }
                        }
                    }
                    dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                        val chosen = items.filterIndexed { i, _ -> checked[i] }
                        if (chosen.isEmpty()) { toast("没有勾选记录"); return@setOnClickListener }
                        dialog.dismiss()
                        confirmBatchDelete(chosen)
                    }
                }
                dialog.show()
            }
        }
    }

    private fun confirmBatchDelete(chosen: List<SessionStore.Info>) {
        val preview = chosen.take(8).joinToString("\n") { describe(it) } +
            if (chosen.size > 8) "\n…另外 ${chosen.size - 8} 条" else ""
        AlertDialog.Builder(this).setTitle("删除 ${chosen.size} 条记录？")
            .setMessage("原始数据会一起删除，不能恢复。如果只是标签填错，请用“修改记录”。\n\n$preview")
            .setPositiveButton("删除 ${chosen.size} 条") { _, _ ->
                io.execute {
                    val failed = chosen.count { !it.dir.deleteRecursively() }
                    ui.post {
                        if (chosen.any { it.dir == lastDir }) lastDir = null
                        tvLive.text = "已删除 ${chosen.size - failed} 条记录" + if (failed > 0) "，$failed 条删除失败" else ""
                        refreshSummary()
                    }
                }
            }
            .setNegativeButton("取消", null).show()
    }

    private fun onRoutes() {
        if (session != null) { toast("请先停止当前采集"); return }
        startActivity(
            Intent(this, RouteActivity::class.java)
                .putExtra(RouteActivity.EXTRA_BUILDING, etBuilding.text.toString().trim().uppercase())
                .putExtra(RouteActivity.EXTRA_FLOOR, etFloor.text.toString().trim())
                .putExtra(RouteActivity.EXTRA_ROUTE, if (spMode.code() == "walk") etPoint.text.toString().trim().uppercase() else ""),
        )
    }

    private fun onExport() {
        if (session != null) { toast("请先停止当前采集"); return }
        toast("正在打包…")
        io.execute {
            val zip = try { SessionStore.exportZip(this) } catch (e: Exception) { null }
            ui.post {
                if (zip == null) { toast("导出失败"); return@post }
                val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", zip)
                val share = Intent(Intent.ACTION_SEND)
                    .setType("application/zip")
                    .putExtra(Intent.EXTRA_STREAM, uri)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                startActivity(Intent.createChooser(share, "导出数据"))
                tvLive.text = "已打包：${zip.absolutePath}"
            }
        }
    }

    // ---------- 定时刷新 ----------

    private val ticker = object : Runnable {
        override fun run() {
            tickCount++
            session?.let { renderLive(it) }
            renderNext()
            if (tickCount % 6 == 0 && session == null) refreshEnv()
            ui.postDelayed(this, 500)
        }
    }

    private fun renderLive(s: Session) {
        val f = s.form
        val w = s.wifi
        val sb = StringBuilder()
        sb.append("● 采集中 ${s.elapsedSec}s   ${f.building} ${f.floor}F ${f.mode} ${f.pointId}\n")
        sb.append("Wi-Fi 新扫描 ${w.scansFresh}")
        if (f.mode == "static") sb.append("/${f.targetScans}")
        sb.append("  (总 ${w.scansTotal}, 拒绝 ${w.rejected})  AP ${w.lastApCount}\n")
        sb.append("BLE 包 ${s.ble.packets}  设备 ${s.ble.uniqueDevices}\n")
        sb.append("基站 采样 ${s.cell.samples}  当前 ${s.cell.lastCellCount}\n")
        sb.append("传感器事件 ${s.sensors.events}\n")
        sb.append(w.diagnosis()).append('\n')
        if (f.mode == "transition") {
            sb.append(
                when {
                    !moveStarted -> "→ 原地站稳后按“开始移动”"
                    !moveEnded -> "→ 移动中，已经过 $floorsPassed 层；到达 ${f.toFloor}F 后按“到达目标层”"
                    else -> "→ 已到达，原地站 10 秒后按“停止”"
                },
            )
        }
        if (w.scansTotal > 3 && w.lastApCount == 0) sb.append("\n⚠ 最近一次扫描 0 个 AP，检查定位和 Wi-Fi")
        tvLive.text = sb.toString()

        if (f.mode == "static" && w.scansFresh >= f.targetScans) endSession(complete = true)
    }

    private fun refreshEnv() {
        val env = Env.snapshot(this)
        val problems = env.blockers() + env.warnings()
        if (problems.isEmpty()) {
            tvEnv.text = "环境正常：定位、Wi-Fi、蓝牙已开启，扫描节流已关闭"
            tvEnv.setBackgroundColor(Color.parseColor("#2E7D32"))
        } else {
            tvEnv.text = problems.joinToString("\n") { "• $it" }
            tvEnv.setBackgroundColor(Color.parseColor(if (env.blockers().isEmpty()) "#EF6C00" else "#C62828"))
        }
        tvEnv.setTextColor(Color.WHITE)
    }

    private fun refreshSummary() {
        io.execute {
            val text = try { SessionStore.summary(this) } catch (e: Exception) { "统计失败：${e.message}" }
            ui.post { tvSummary.text = text }
        }
    }

    // ---------- 指南针（只用于提示朝向） ----------

    override fun onSensorChanged(e: SensorEvent) {
        SensorManager.getRotationMatrixFromVector(rot, e.values)
        SensorManager.getOrientation(rot, ori)
        compassDeg = (Math.toDegrees(ori[0].toDouble()) + 360) % 360
        val dirs = arrayOf("N", "E", "S", "W")
        val cur = dirs[(((compassDeg + 45) % 360) / 90).toInt()]
        val want = spOrientation.code()
        val hint = if (spMode.code() == "static" && want != cur) "（选择的是 $want）" else ""
        tvCompass.text = "手机顶部朝向：${compassDeg.toInt()}° ≈ $cur $hint"
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    // ---------- 工具 ----------

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    private fun Spinner.setOptions(options: List<Pair<String, String>>) {
        tag = options.map { it.first }
        adapter = ArrayAdapter(context, android.R.layout.simple_spinner_dropdown_item, options.map { it.second })
    }

    @Suppress("UNCHECKED_CAST")
    private fun Spinner.code(): String = (tag as List<String>)[selectedItemPosition.coerceAtLeast(0)]

    @Suppress("UNCHECKED_CAST")
    private fun Spinner.selectCode(code: String) {
        val i = (tag as List<String>).indexOf(code)
        if (i >= 0) setSelection(i)
    }

    companion object {
        private const val MIN_WALK_SCANS = 5

        private val MODES = listOf(
            "static" to "静态定点",
            "walk" to "行走（同一层内）",
            "transition" to "跨层（楼梯/电梯）",
        )
        private val SPLITS = listOf("train" to "训练集 train", "test" to "测试集 test")
        private val ORIENTATIONS = listOf("N" to "北 N", "E" to "东 E", "S" to "南 S", "W" to "西 W")
        private val POSTURES = listOf(
            "hand_flat" to "手持平放",
            "hand_upright" to "手持竖直（看屏幕）",
            "call" to "贴耳打电话",
            "pocket" to "裤袋",
            "bag" to "背包",
        )
        private val METHODS = listOf("stairs" to "楼梯", "elevator" to "电梯", "escalator" to "扶梯")

        /** T05 -> T06，X09 -> X10；末尾没有数字时原样返回。 */
        fun nextId(id: String): String {
            val m = Regex("^(.*?)(\\d+)$").find(id) ?: return id
            val (prefix, num) = m.destructured
            return prefix + (num.toInt() + 1).toString().padStart(num.length, '0')
        }
    }
}
