package edu.bnbu.floorsense.collector

import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.Build
import android.os.SystemClock
import java.io.File

/**
 * 持续 BLE 扫描，每个广播包写一行到 ble.csv。
 * 不记录设备名（可能含个人信息）。iBeacon 会解析出 uuid/major/minor，
 * 这几个字段比 MAC 地址稳定（很多设备的 MAC 会随机变化）。
 */
class BleCollector(
    context: Context,
    dir: File,
    private val mark: (String, String) -> Unit,
) {
    private val adapter = (context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager?)?.adapter
    private val out = CsvWriter(
        File(dir, "ble.csv"),
        listOf(
            "t_wall_ms", "t_elapsed_ms", "address", "rssi", "tx_power",
            "connectable", "ibeacon_uuid", "ibeacon_major", "ibeacon_minor", "ibeacon_tx",
        ),
    )

    @Volatile var packets = 0; private set
    private val seen = HashSet<String>()
    val uniqueDevices: Int get() = synchronized(seen) { seen.size }

    private val callback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) = write(result)
        override fun onBatchScanResults(results: MutableList<ScanResult>) = results.forEach(::write)
        override fun onScanFailed(errorCode: Int) = mark("ble_scan_failed", errorCode.toString())
    }

    @SuppressLint("MissingPermission")
    fun start() {
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .setReportDelay(0)
            .build()
        try {
            val scanner = adapter?.takeIf { it.isEnabled }?.bluetoothLeScanner
            if (scanner == null) {
                mark("ble_unavailable", "bluetooth off or missing")
                return
            }
            scanner.startScan(null, settings, callback)
        } catch (e: SecurityException) {
            mark("ble_unavailable", "permission denied")
        }
    }

    @SuppressLint("MissingPermission")
    fun stop() {
        try { adapter?.bluetoothLeScanner?.stopScan(callback) } catch (_: Exception) {}
        out.close()
    }

    private fun write(r: ScanResult) {
        val wall = System.currentTimeMillis()
        val elapsedMs = SystemClock.elapsedRealtime()
        val addr = r.device.address
        synchronized(seen) { seen.add(addr) }
        packets++
        val beacon = parseIBeacon(r.scanRecord?.getManufacturerSpecificData(0x004C))
        out.row(
            wall, elapsedMs, addr, r.rssi,
            if (r.txPower == ScanResult.TX_POWER_NOT_PRESENT) null else r.txPower,
            if (r.isConnectable) 1 else 0,
            beacon?.uuid, beacon?.major, beacon?.minor, beacon?.tx,
        )
    }

    private data class IBeacon(val uuid: String, val major: Int, val minor: Int, val tx: Int)

    /** Apple 厂商数据：0x02 0x15 + 16 字节 UUID + 2 字节 major + 2 字节 minor + 1 字节 tx。 */
    private fun parseIBeacon(d: ByteArray?): IBeacon? {
        if (d == null || d.size < 23 || d[0] != 0x02.toByte() || d[1] != 0x15.toByte()) return null
        val hex = d.copyOfRange(2, 18).joinToString("") { "%02x".format(it) }
        val uuid = "${hex.substring(0, 8)}-${hex.substring(8, 12)}-${hex.substring(12, 16)}-" +
            "${hex.substring(16, 20)}-${hex.substring(20)}"
        val major = ((d[18].toInt() and 0xFF) shl 8) or (d[19].toInt() and 0xFF)
        val minor = ((d[20].toInt() and 0xFF) shl 8) or (d[21].toInt() and 0xFF)
        return IBeacon(uuid, major, minor, d[22].toInt())
    }
}
