package edu.bnbu.floorsense.collector

import android.annotation.SuppressLint
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.telephony.CellIdentityNr
import android.telephony.CellInfo
import android.telephony.CellInfoGsm
import android.telephony.CellInfoLte
import android.telephony.CellInfoNr
import android.telephony.CellInfoWcdma
import android.telephony.CellSignalStrengthNr
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import java.io.File

/** 每 2 秒读取一次可见基站信息，写入 cell.csv（每个基站一行）。 */
class CellCollector(
    private val context: Context,
    dir: File,
    private val mark: (String, String) -> Unit,
) {
    private val tm = context.getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager?
    private val handler = Handler(Looper.getMainLooper())
    private val out = CsvWriter(
        File(dir, "cell.csv"),
        listOf(
            "sample_id", "t_wall_ms", "t_elapsed_ms", "type", "registered", "mcc", "mnc",
            "area", "cid", "pci", "arfcn", "dbm", "rsrp", "rsrq", "sinr", "level",
        ),
    )

    @Volatile var samples = 0; private set
    @Volatile var lastCellCount = 0; private set
    private var running = false

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            sample()
            handler.postDelayed(this, 2000)
        }
    }

    fun start() {
        if (tm == null) {
            mark("cell_unavailable", "no telephony")
            return
        }
        running = true
        handler.post(tick)
    }

    fun stop() {
        running = false
        handler.removeCallbacksAndMessages(null)
        out.close()
    }

    @SuppressLint("MissingPermission")
    private fun sample() {
        val t = tm ?: return
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                t.requestCellInfoUpdate(
                    ContextCompat.getMainExecutor(context),
                    object : TelephonyManager.CellInfoCallback() {
                        override fun onCellInfo(cells: MutableList<CellInfo>) = write(cells)
                    },
                )
            } else {
                write(t.allCellInfo ?: emptyList())
            }
        } catch (e: SecurityException) {
            if (samples == 0) mark("cell_unavailable", "permission denied")
            running = false
        }
    }

    private fun write(cells: List<CellInfo>) {
        if (!running) return
        val id = samples++
        val wall = System.currentTimeMillis()
        val elapsedMs = SystemClock.elapsedRealtime()
        lastCellCount = cells.size
        for (c in cells) {
            val reg = if (c.isRegistered) 1 else 0
            when {
                c is CellInfoLte -> {
                    val i = c.cellIdentity; val s = c.cellSignalStrength
                    out.row(
                        id, wall, elapsedMs, "lte", reg, i.mccString, i.mncString,
                        v(i.tac), v(i.ci), v(i.pci), v(i.earfcn),
                        v(s.dbm), v(s.rsrp), v(s.rsrq), v(s.rssnr), s.level,
                    )
                }
                Build.VERSION.SDK_INT >= 29 && c is CellInfoNr -> {
                    val i = c.cellIdentity as CellIdentityNr
                    val s = c.cellSignalStrength as CellSignalStrengthNr
                    out.row(
                        id, wall, elapsedMs, "nr", reg, i.mccString, i.mncString,
                        v(i.tac), if (i.nci == Long.MAX_VALUE) null else i.nci, v(i.pci), v(i.nrarfcn),
                        v(s.dbm), v(s.ssRsrp), v(s.ssRsrq), v(s.ssSinr), s.level,
                    )
                }
                c is CellInfoGsm -> {
                    val i = c.cellIdentity; val s = c.cellSignalStrength
                    out.row(
                        id, wall, elapsedMs, "gsm", reg, i.mccString, i.mncString,
                        v(i.lac), v(i.cid), null, v(i.arfcn),
                        v(s.dbm), null, null, null, s.level,
                    )
                }
                c is CellInfoWcdma -> {
                    val i = c.cellIdentity; val s = c.cellSignalStrength
                    out.row(
                        id, wall, elapsedMs, "wcdma", reg, i.mccString, i.mncString,
                        v(i.lac), v(i.cid), v(i.psc), v(i.uarfcn),
                        v(s.dbm), null, null, null, s.level,
                    )
                }
                else -> out.row(
                    id, wall, elapsedMs, c.javaClass.simpleName, reg, null, null,
                    null, null, null, null, null, null, null, null, null,
                )
            }
        }
        out.flush()
    }

    /** CellInfo.UNAVAILABLE (Int.MAX_VALUE) 写成空值。 */
    private fun v(x: Int): Int? = if (x == Int.MAX_VALUE) null else x
}
