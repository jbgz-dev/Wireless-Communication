import edu.bnbu.floorsense.collector.*;
import java.util.*;

/** 无需新增测试依赖：对编译后的纯 JVM 行走状态运行回归检查。 */
public class WalkProgressCheck {
    private static int checks;
    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }
    private static WalkNode node(String id, int order) {
        return new WalkNode(id, order, null, null);
    }
    private static void invalid(List<WalkNode> nodes) {
        try {
            new WalkProgress(nodes, false);
            throw new AssertionError("Invalid route was accepted");
        } catch (IllegalArgumentException expected) { checks++; }
    }
    public static void main(String[] args) {
        var input = new ArrayList<>(List.of(node("C", 3), node("A", 1), node("B", 2)));
        var w = new WalkProgress(input, false);
        input.clear();
        check(w.getNodes().size() == 3, "Route must be a snapshot");
        var a = w.recordNext(10000, 0);
        check(a.getAutomatic() && a.getNode().getId().equals("A"), "Start must be automatic at A");
        check(!w.getCanUndo() && w.undo(100) == null, "Start cannot be undone");
        check(w.recordNext(10100, 100) == null, "Double tap must not reach B");
        var b = w.recordNext(11000, 1000);
        check(b.getNode().getId().equals("B") && !b.getAutomatic(), "Next is B");
        check(b.getNodeIndex() == 2, "Forward order");
        check(!w.getComplete(), "Intermediate node is not completion");
        check(w.undo(1100).getMarkId() == b.getMarkId(), "Undo identifies original mark");
        check(w.getNextNode().getId().equals("B"), "Undo restores B prompt");
        var retry = w.recordNext(12100, 2100);
        check(retry.getMarkId() > b.getMarkId(), "Re-record must use a fresh mark ID");
        check(retry.getElapsedMs() == 2100, "Re-record uses new arrival time");
        var c = w.recordNext(13100, 3100);
        check(w.getComplete() && c.getNodeIndex() == 3 && w.getNextNode() == null, "Endpoint completes route");
        check(w.recordNext(15000, 5000) == null, "Cannot record beyond endpoint");
        w.undo(5100);
        check(!w.getComplete() && w.getNextNode().getId().equals("C"), "Endpoint undo clears completion");
        var reverse = new WalkProgress(List.of(node("A", 1), node("B", 2), node("C", 3)), true);
        check(reverse.recordNext(10000, 0).getNode().getId().equals("C"), "Reverse starts at C");
        var rb = reverse.recordNext(11000, 1000);
        check(rb.getNode().getOrder() == 2 && rb.getNodeIndex() == 2, "Reverse keeps route order");
        check(reverse.recordNext(12000, 2000).getNode().getId().equals("A") && reverse.getComplete(), "Reverse completes at A");
        invalid(List.of(node("A", 1)));
        invalid(List.of(node("A", 1), node("A", 2)));
        invalid(List.of(node("A", 1), new WalkNode("B", 2, Double.NaN, null)));
        System.out.println("WalkProgress: " + checks + " checks passed");
    }
}
