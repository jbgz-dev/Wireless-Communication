package edu.bnbu.floorsense.collector

import android.Manifest
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.pm.PackageManager
import android.location.LocationManager
import android.net.wifi.WifiManager
import android.os.Build
import androidx.core.content.ContextCompat
import org.json.JSONObject

/** 手机环境检查：权限、定位开关、Wi-Fi/蓝牙开关、扫描节流。 */
object Env {
    fun requiredPermissions(): Array<String> {
        val p = mutableListOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION,
            Manifest.permission.READ_PHONE_STATE,
        )
        if (Build.VERSION.SDK_INT >= 31) p += Manifest.permission.BLUETOOTH_SCAN
        if (Build.VERSION.SDK_INT >= 29) p += Manifest.permission.ACTIVITY_RECOGNITION
        return p.toTypedArray()
    }

    data class Snapshot(
        val locationOn: Boolean,
        val wifiOn: Boolean,
        val bluetoothOn: Boolean,
        /** null 表示系统没有提供这个信息（Android 9 一定节流）。 */
        val throttled: Boolean?,
        val missing: List<String>,
    ) {
        /** 会让 Wi-Fi 数据无效的问题，必须先解决才能开始采集。 */
        fun blockers(): List<String> = buildList {
            if (Manifest.permission.ACCESS_FINE_LOCATION in missing) add("未授予精确定位权限")
            if (!locationOn) add("系统定位未开启（不开定位扫不到 Wi-Fi）")
            if (!wifiOn) add("Wi-Fi 未开启")
        }

        fun warnings(): List<String> = buildList {
            when (throttled) {
                true -> add(
                    "系统报告 Wi-Fi 扫描节流仍开启（每 2 分钟最多 4 次新扫描）。" +
                        "请在开发者选项里关闭“WLAN 扫描调节/节流”；" +
                        "Android 11 起 adb 的 settings global 命令对它无效",
                )
                null -> add("无法自动检测扫描节流，请确认开发者选项里已关闭")
                false -> {}
            }
            if (!bluetoothOn) add("蓝牙未开启，不会记录 BLE")
            val others = missing.filter { it != Manifest.permission.ACCESS_FINE_LOCATION }
            if (others.isNotEmpty()) add("缺少权限：" + others.joinToString { it.substringAfterLast('.') })
        }

        fun toJson(): JSONObject = JSONObject()
            .put("location_on", locationOn)
            .put("wifi_on", wifiOn)
            .put("bluetooth_on", bluetoothOn)
            .put("wifi_throttled", throttled ?: JSONObject.NULL)
            .put("missing_permissions", missing.joinToString(";"))
    }

    fun snapshot(ctx: Context): Snapshot {
        val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val wm = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val bt = (ctx.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager?)?.adapter
        val missing = requiredPermissions().filter {
            ContextCompat.checkSelfPermission(ctx, it) != PackageManager.PERMISSION_GRANTED
        }
        val btOn = try { bt?.isEnabled == true } catch (_: SecurityException) { false }
        val throttled = when {
            Build.VERSION.SDK_INT >= 30 -> wm.isScanThrottleEnabled
            Build.VERSION.SDK_INT == 28 -> true
            else -> null
        }
        return Snapshot(lm.isLocationEnabled, wm.isWifiEnabled, btOn, throttled, missing)
    }

    fun device(): JSONObject = JSONObject()
        .put("manufacturer", Build.MANUFACTURER)
        .put("model", Build.MODEL)
        .put("device", Build.DEVICE)
        .put("android_release", Build.VERSION.RELEASE)
        .put("sdk_int", Build.VERSION.SDK_INT)
}
