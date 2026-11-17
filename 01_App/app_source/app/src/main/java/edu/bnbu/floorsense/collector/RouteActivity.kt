package edu.bnbu.floorsense.collector

import android.content.ClipData
import android.content.ClipboardManager
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
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
import com.google.android.material.snackbar.Snackbar
import edu.bnbu.floorsense.collector.RouteStore.FloorKey
import edu.bnbu.floorsense.collector.RouteStore.Node
import edu.bnbu.floorsense.collector.RouteStore.Route

/** 路线与节点管理：每层若干条路线，每条路线记节点编号和沿路线的步测距离。 */
class RouteActivity : AppCompatActivity() {

    private lateinit var spFloor: Spinner
    private lateinit var spRoute: Spinner
    private lateinit var tvRouteInfo: TextView
    private lateinit var listNodes: LinearLayout
    private lateinit var cbAll: CheckBox
    private lateinit var etNodeId: EditText
    private lateinit var btnDeleteRoute: Button

    private lateinit var data: RouteStore.Data
    /** 主界面当前楼层：还没有路线时也显示出来，但不写进文件。 */
    private var extraFloor: FloorKey? = null
    private var floors: List<FloorKey> = emptyList()
    private var routes: List<Route> = emptyList()
    private var curFloor: FloorKey? = null
    private var curRoute: Route? = null
    /** 主界面带过来的路线编号，这层还没有这条路线时，用来预填“新建路线”。 */
    private var pendingRouteId: String? = null
    private val selected = mutableSetOf<String>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_routes)
        title = "路线与节点管理"
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        spFloor = findViewById(R.id.spFloor)
        spRoute = findViewById(R.id.spRoute)
        tvRouteInfo = findViewById(R.id.tvRouteInfo)
        listNodes = findViewById(R.id.listNodes)
        cbAll = findViewById(R.id.cbAll)
        etNodeId = findViewById(R.id.etNodeId)
        btnDeleteRoute = findViewById(R.id.btnDeleteRoute)

        data = RouteStore.load(this)
        data.warning?.let { AlertDialog.Builder(this).setMessage(it).setPositiveButton("知道了", null).show() }
        val building = intent.getStringExtra(EXTRA_BUILDING).orEmpty()
        val floor = intent.getStringExtra(EXTRA_FLOOR)?.toIntOrNull()
        if (building.isNotEmpty() && floor != null) {
            extraFloor = FloorKey(building, floor)
            curFloor = extraFloor
            val rid = intent.getStringExtra(EXTRA_ROUTE).orEmpty()
            curRoute = data.routesOn(extraFloor!!).firstOrNull { it.routeId == rid }
            if (curRoute == null && rid.isNotEmpty()) pendingRouteId = rid
        }

        spFloor.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, id: Long) {
                val f = floors.getOrNull(pos) ?: return
                if (f == curFloor) return
                curFloor = f
                curRoute = null
                renderRoutes()
            }
            override fun onNothingSelected(p: AdapterView<*>?) {}
        }
        spRoute.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, id: Long) {
                val r = routes.getOrNull(pos) ?: return
                if (r === curRoute) return
                curRoute = r
                selected.clear()
                renderNodes()
            }
            override fun onNothingSelected(p: AdapterView<*>?) {}
        }
        cbAll.setOnClickListener {
            val r = curRoute
            if (cbAll.isChecked && r != null) selected.addAll(r.nodes.map { it.id }) else selected.clear()
            renderNodes()
        }
        findViewById<Button>(R.id.btnAddFloor).setOnClickListener { addFloorDialog() }
        findViewById<Button>(R.id.btnAddRoute).setOnClickListener { addRouteDialog() }
        findViewById<Button>(R.id.btnAddNode).setOnClickListener { onAddNode() }
        findViewById<Button>(R.id.btnDeleteSelected).setOnClickListener { onDeleteSelected() }
        findViewById<Button>(R.id.btnCopy).setOnClickListener { onCopy() }
        btnDeleteRoute.setOnClickListener { onDeleteRouteOrFloor() }
        renderFloors()
    }

    override fun onSupportNavigateUp(): Boolean { finish(); return true }

    // ---------- 渲染 ----------

    private fun renderFloors() {
        floors = data.floorKeys(extraFloor)
        if (curFloor !in floors) curFloor = floors.firstOrNull()
        spFloor.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item,
            floors.map { it.toString() }.ifEmpty { listOf("（还没有楼层，点 + 楼层）") })
        curFloor?.let { spFloor.setSelection(floors.indexOf(it)) }
        renderRoutes()
    }

    private fun renderRoutes() {
        routes = curFloor?.let { data.routesOn(it) }.orEmpty()
        if (curRoute !in routes) curRoute = routes.firstOrNull()
        spRoute.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item,
            routes.map { it.routeId }.ifEmpty { listOf("（这层还没有路线，点 + 路线）") })
        curRoute?.let { spRoute.setSelection(routes.indexOf(it)) }
        selected.clear()
        renderNodes()
    }

    private fun renderNodes() {
        listNodes.removeAllViews()
        val r = curRoute
        selected.retainAll(r?.nodes?.map { it.id }.orEmpty().toSet())
        cbAll.isChecked = r != null && r.nodes.isNotEmpty() && selected.size == r.nodes.size
        btnDeleteRoute.text = if (r != null) "删除路线 ${r.routeId}" else "删除这个楼层"
        btnDeleteRoute.isEnabled = r != null || curFloor != null
        findViewById<Button>(R.id.btnDeleteSelected).text =
            if (selected.isEmpty()) "删除选中节点" else "删除选中节点（${selected.size}）"
        tvRouteInfo.text = when {
            curFloor == null -> "先点“+ 楼层”添加楼层，再添加路线和节点。"
            r == null -> "${curFloor} 还没有路线，点“+ 路线”新建。"
            r.nodes.isEmpty() -> "${r.routeId}：还没有节点。按经过顺序添加，第一个是起点。"
            else -> "${r.routeId}：${r.nodes.joinToString(" → ") { it.id }}，" +
                "已填坐标 ${r.nodes.count { it.xM != null && it.yM != null }}/${r.nodes.size} 个"
        }
        r ?: return
        r.nodes.forEachIndexed { i, n -> listNodes.addView(nodeRow(r, i, n)) }
    }

    private fun nodeRow(r: Route, index: Int, n: Node): View = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        minimumHeight = dp(44)
        setOnClickListener { editNodeDialog(n) }
        addView(CheckBox(context).apply {
            isChecked = n.id in selected
            setOnClickListener {
                if (isChecked) selected.add(n.id) else selected.remove(n.id)
                renderNodes()
            }
        }, LinearLayout.LayoutParams(dp(40), LinearLayout.LayoutParams.WRAP_CONTENT))
        addView(TextView(context).apply { text = (index + 1).toString(); textSize = 14f },
            LinearLayout.LayoutParams(dp(40), LinearLayout.LayoutParams.WRAP_CONTENT))
        addView(TextView(context).apply { text = n.id; textSize = 16f },
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        addView(TextView(context).apply {
            text = if (n.xM == null && n.yM == null) "—" else "${n.xM ?: "?"}, ${n.yM ?: "?"}"
            textSize = 14f
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        addView(TextView(context).apply {
            text = if (index > 0) "↑" else ""
            textSize = 18f
            gravity = Gravity.CENTER
            if (index > 0) setOnClickListener {
                r.nodes.add(index - 1, r.nodes.removeAt(index))
                save()
                renderNodes()
            }
        }, LinearLayout.LayoutParams(dp(44), dp(44)))
        addView(TextView(context).apply {
            text = "✕"
            textSize = 18f
            gravity = Gravity.CENTER
            setOnClickListener { deleteNodes(listOf(n)) }
        }, LinearLayout.LayoutParams(dp(44), dp(44)))
    }

    // ---------- 节点 ----------

    private fun onAddNode() {
        val r = curRoute ?: run { toast("先新建一条路线"); return }
        val id = checkId(etNodeId.text.toString(), r, except = null) ?: return
        r.nodes.add(Node(id))
        save()
        renderNodes()
        etNodeId.setText(nextNodeId(r))
        etNodeId.setSelection(etNodeId.text.length)
    }

    /** 编号：字母数字，同一路线内不能重复。返回规范化后的编号，不合法时提示并返回 null。 */
    private fun checkId(raw: String, r: Route, except: Node?): String? {
        val id = raw.trim().uppercase()
        return when {
            id.isEmpty() -> { toast("请填写节点编号"); null }
            !id.matches(Regex("[A-Z0-9_-]+")) -> { toast("节点编号只能用字母、数字、- 和 _"); null }
            r.nodes.any { it !== except && it.id == id } -> { toast("${r.routeId} 里已经有节点 $id"); null }
            else -> id
        }
    }

    /** 最后一个节点是字母就取下一个字母，是 W01 这类就加 1。 */
    private fun nextNodeId(r: Route): String {
        val last = r.nodes.lastOrNull()?.id ?: return "A"
        val next = if (last.length == 1 && last[0] in 'A'..'Y') (last[0] + 1).toString() else MainActivity.nextId(last)
        return if (r.nodes.any { it.id == next }) "" else next
    }

    private fun editNodeDialog(n: Node) {
        val r = curRoute ?: return
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(8), dp(24), 0) }
        val decimal = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
        fun field(label: String, value: String, type: Int, hint: String = ""): EditText {
            box.addView(TextView(this).apply { text = label })
            return EditText(this).apply { setText(value); inputType = type; this.hint = hint; box.addView(this) }
        }
        val eId = field("节点编号", n.id, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS)
        // 坐标可以是负数（原点不一定在角落）
        val signed = decimal or InputType.TYPE_NUMBER_FLAG_SIGNED
        val eX = field("x（m，可留空）", n.xM?.toString().orEmpty(), signed, "以后再补")
        val eY = field("y（m，可留空）", n.yM?.toString().orEmpty(), signed, "以后再补")

        showValidated("修改节点 ${r.routeId} / ${n.id}", box, "保存", neutral = "删除" to { deleteNodes(listOf(n)) }) {
            val id = checkId(eId.text.toString(), r, except = n) ?: return@showValidated false
            val x = optCoord(eX) ?: return@showValidated false
            val y = optCoord(eY) ?: return@showValidated false
            if (n.id != id && selected.remove(n.id)) selected.add(id)
            n.id = id; n.xM = x.value; n.yM = y.value
            save()
            renderNodes()
            true
        }
    }

    /** 留空返回 Coord(null)；不是数字时提示并返回 null。 */
    private class Coord(val value: Double?)
    private fun optCoord(e: EditText): Coord? {
        val s = e.text.toString().trim()
        if (s.isEmpty()) return Coord(null)
        val v = s.toDoubleOrNull()
        if (v == null || v.isNaN() || v.isInfinite()) { toast("坐标要填数字，或者留空"); return null }
        return Coord(v)
    }

    /** 单个删除不确认，用“撤销”恢复；批量删除先确认。 */
    private fun deleteNodes(nodes: List<Node>) {
        val r = curRoute ?: return
        val before = r.nodes.toList()
        r.nodes.removeAll(nodes.toSet())
        nodes.forEach { selected.remove(it.id) }
        save()
        renderNodes()
        Snackbar.make(listNodes, "已删除 ${nodes.joinToString { it.id }}", Snackbar.LENGTH_LONG)
            .setAction("撤销") {
                // 按删除前的顺序放回；删除后新加的同名节点优先保留
                val now = r.nodes.toList()
                val ids = now.map { it.id }.toSet()
                r.nodes.clear()
                r.nodes.addAll(before.filter { it in now || it.id !in ids })
                r.nodes.addAll(now.filter { it !in before })
                save()
                renderNodes()
            }.show()
    }

    private fun onDeleteSelected() {
        val r = curRoute ?: return
        val nodes = r.nodes.filter { it.id in selected }
        if (nodes.isEmpty()) { toast("先勾选要删除的节点"); return }
        AlertDialog.Builder(this).setTitle("删除 ${nodes.size} 个节点？")
            .setMessage("${r.routeId}：${nodes.joinToString { it.id }}")
            .setPositiveButton("删除") { _, _ -> deleteNodes(nodes) }
            .setNegativeButton("取消", null).show()
    }

    // ---------- 楼层 / 路线 ----------

    private fun addFloorDialog() {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(8), dp(24), 0) }
        box.addView(TextView(this).apply { text = "楼名" })
        val eB = EditText(this).apply {
            setText(curFloor?.building.orEmpty()); hint = "如 T5"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS; box.addView(this)
        }
        box.addView(TextView(this).apply { text = "楼层" })
        val eF = EditText(this).apply {
            hint = "如 3"; inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_SIGNED; box.addView(this)
        }
        showValidated("添加楼层", box, "添加") {
            val b = eB.text.toString().trim().uppercase()
            val f = eF.text.toString().trim().toIntOrNull()
            if (b.isEmpty() || f == null) { toast("请填写楼名和楼层（整数）"); return@showValidated false }
            val key = FloorKey(b, f)
            data.floors.add(key)
            curFloor = key
            curRoute = null
            save()
            renderFloors()
            true
        }
    }

    private fun addRouteDialog() {
        val floor = curFloor ?: run { toast("先添加楼层"); return }
        val e = EditText(this).apply {
            setText(pendingRouteId.orEmpty()); hint = "如 TW01（和行走模式的路线编号一致）"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS
        }
        val box = LinearLayout(this).apply { setPadding(dp(24), dp(8), dp(24), 0); addView(e, LinearLayout.LayoutParams(-1, -2)) }
        showValidated("在 $floor 新建路线", box, "新建") {
            val id = e.text.toString().trim().uppercase()
            when {
                !id.matches(Regex("[A-Z0-9_-]+")) -> { toast("路线编号只能用字母、数字、- 和 _"); return@showValidated false }
                data.routesOn(floor).any { it.routeId == id } -> { toast("$floor 已经有路线 $id"); return@showValidated false }
            }
            val r = Route(floor.building, floor.floor, id)
            data.routes.add(r)
            curRoute = r
            pendingRouteId = null
            save()
            renderRoutes()
            true
        }
    }

    /** 有路线时删除当前路线；这层没有路线时删除楼层本身。 */
    private fun onDeleteRouteOrFloor() {
        val r = curRoute
        val floor = curFloor ?: return
        if (r != null) {
            AlertDialog.Builder(this).setTitle("删除路线 ${r.routeId}？")
                .setMessage("$floor 的路线 ${r.routeId} 和它的 ${r.nodes.size} 个节点都会删除，不能撤销。\n已采集的行走记录不受影响。")
                .setPositiveButton("删除") { _, _ ->
                    data.routes.remove(r)
                    data.floors.add(floor)
                    curRoute = null
                    save()
                    renderRoutes()
                }
                .setNegativeButton("取消", null).show()
        } else {
            data.floors.remove(floor)
            if (extraFloor == floor) extraFloor = null
            curFloor = null
            save()
            renderFloors()
        }
    }

    private fun onCopy() {
        val list = curRoute?.let { listOf(it) } ?: curFloor?.let { data.routesOn(it) }.orEmpty()
        val count = list.sumOf { it.nodes.size }
        if (count == 0) { toast("没有可复制的节点"); return }
        val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("nodes", RouteStore.csv(list)))
        toast("已复制 $count 个节点（CSV，可以直接粘贴进表格）")
    }

    // ---------- 工具 ----------

    private fun save() {
        try { RouteStore.save(this, data) } catch (e: Exception) { toast("保存失败：${e.message}") }
    }

    /** 点确定时先校验，onOk 返回 false 时对话框不关闭。 */
    private fun showValidated(
        title: String, view: View, ok: String,
        neutral: Pair<String, () -> Unit>? = null, onOk: () -> Boolean,
    ) {
        val b = AlertDialog.Builder(this).setTitle(title).setView(view)
            .setPositiveButton(ok, null).setNegativeButton("取消", null)
        neutral?.let { (label, action) -> b.setNeutralButton(label) { _, _ -> action() } }
        val dialog = b.create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener { if (onOk()) dialog.dismiss() }
        }
        dialog.show()
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        const val EXTRA_BUILDING = "building"
        const val EXTRA_FLOOR = "floor"
        const val EXTRA_ROUTE = "route"
    }
}
