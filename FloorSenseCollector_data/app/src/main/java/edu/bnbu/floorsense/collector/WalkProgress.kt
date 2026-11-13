package edu.bnbu.floorsense.collector

/** 本次行走使用的不可变节点副本。order 是路线管理中的正向顺序（从 1 开始），反向行走时也不变。 */
data class WalkNode(val id: String, val order: Int, val xM: Double?, val yM: Double?)

data class NodeVisit(
    val markId: Int,
    val nodeIndex: Int,
    val node: WalkNode,
    val wallMs: Long,
    val elapsedMs: Long,
    val automatic: Boolean,
)

/** 与 Android 无关的顺序标记状态；撤销保留在 Session 的审计记录中。 */
class WalkProgress(nodes: List<WalkNode>, val reverse: Boolean) {
    val nodes: List<WalkNode>
    private val reached = mutableListOf<NodeVisit>()
    private var nextMarkId = 1
    private var lastActionMs: Long? = null

    init {
        require(nodes.size >= 2) { "路线至少需要起点和终点两个节点" }
        require(nodes.all { it.id.isNotBlank() } && nodes.map { it.id }.distinct().size == nodes.size) {
            "节点编号不能为空或重复"
        }
        require(nodes.all { n -> (n.xM == null || n.xM.isFinite()) && (n.yM == null || n.yM.isFinite()) }) {
            "节点坐标无效，请在路线与节点中修正"
        }
        val sorted = nodes.sortedBy { it.order }
        this.nodes = if (reverse) sorted.reversed() else sorted
    }

    val visits: List<NodeVisit> get() = reached.toList()
    val reachedCount: Int get() = reached.size
    val complete: Boolean get() = reached.size == nodes.size
    val canUndo: Boolean get() = reached.size > 1 // 起点与开始采集绑定，不能单独撤销。
    val nextNode: WalkNode? get() = nodes.getOrNull(reached.size)

    fun recordNext(wallMs: Long, elapsedMs: Long): NodeVisit? {
        val node = nextNode ?: return null
        // 防止连点把下一个节点也误记；起点记录后同样留出一秒。
        if (lastActionMs?.let { elapsedMs - it < 1000 } == true) return null
        val visit = NodeVisit(nextMarkId++, reached.size + 1, node, wallMs, elapsedMs, reached.isEmpty())
        reached.add(visit)
        lastActionMs = elapsedMs
        return visit
    }

    fun undo(elapsedMs: Long): NodeVisit? {
        if (!canUndo) return null
        lastActionMs = elapsedMs
        return reached.removeAt(reached.lastIndex)
    }
}
