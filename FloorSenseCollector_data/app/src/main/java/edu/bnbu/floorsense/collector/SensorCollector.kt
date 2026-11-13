package edu.bnbu.floorsense.collector

import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.content.Context
import android.os.Handler
import android.os.HandlerThread
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin

/**
 * 以约 50 Hz 记录手机传感器到 sensors.csv（长表：一行一个事件）。
 * 按课程要求不记录气压计（TYPE_PRESSURE）。
 * 时间戳 t_ns 是 SensorEvent.timestamp，与 SystemClock.elapsedRealtimeNanos 同一时钟，
 * 可以和 wifi/ble/cell 的 t_elapsed_ms 对齐。
 */
class SensorCollector(context: Context, private val dir: File) : SensorEventListener {
    private val sm = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val thread = HandlerThread("sensors").apply { start() }
    private val out = CsvWriter(
        File(dir, "sensors.csv"),
        listOf("t_ns", "sensor", "v0", "v1", "v2", "v3", "v4", "v5", "accuracy"),
    )

    @Volatile var events = 0L; private set
    /** 最近一次由旋转矢量算出的方位角（度，0=北），用于界面提示和元数据。 */
    @Volatile var azimuthDeg: Double = Double.NaN; private set
    private var sumSin = 0.0
    private var sumCos = 0.0
    private val rot = FloatArray(9)
    private val ori = FloatArray(3)

    fun start() {
        val handler = Handler(thread.looper)
        val registered = JSONArray()
        for ((type, name) in TYPES) {
            val s = sm.getDefaultSensor(type) ?: continue
            if (sm.registerListener(this, s, SensorManager.SENSOR_DELAY_GAME, handler)) {
                registered.put(
                    JSONObject()
                        .put("name", name)
                        .put("hw_name", s.name)
                        .put("vendor", s.vendor)
                        .put("resolution", s.resolution.toDouble())
                        .put("max_range", s.maximumRange.toDouble())
                        .put("min_delay_us", s.minDelay),
                )
            }
        }
        File(dir, "sensors.json").writeText(registered.toString(2))
    }

    fun stop() {
        sm.unregisterListener(this)
        thread.quitSafely()
        out.close()
    }

    /** 整段会话的平均方位角（圆周平均），没有旋转矢量传感器时为 NaN。 */
    fun meanAzimuthDeg(): Double {
        if (sumSin == 0.0 && sumCos == 0.0) return Double.NaN
        return (Math.toDegrees(atan2(sumSin, sumCos)) + 360) % 360
    }

    override fun onSensorChanged(e: SensorEvent) {
        val name = NAMES[e.sensor.type] ?: return
        val v = e.values
        out.row(
            e.timestamp, name,
            v.getOrNull(0), v.getOrNull(1), v.getOrNull(2),
            v.getOrNull(3), v.getOrNull(4), v.getOrNull(5),
            e.accuracy,
        )
        events++
        if (e.sensor.type == Sensor.TYPE_ROTATION_VECTOR) {
            SensorManager.getRotationMatrixFromVector(rot, v)
            SensorManager.getOrientation(rot, ori)
            val a = ori[0].toDouble()
            sumSin += sin(a); sumCos += cos(a)
            azimuthDeg = (Math.toDegrees(a) + 360) % 360
        }
    }

    override fun onAccuracyChanged(sensor: Sensor, accuracy: Int) {}

    companion object {
        private val TYPES = listOf(
            Sensor.TYPE_ACCELEROMETER to "acc",
            Sensor.TYPE_LINEAR_ACCELERATION to "lin_acc",
            Sensor.TYPE_GRAVITY to "gravity",
            Sensor.TYPE_GYROSCOPE to "gyro",
            Sensor.TYPE_MAGNETIC_FIELD to "mag",
            Sensor.TYPE_MAGNETIC_FIELD_UNCALIBRATED to "mag_uncal",
            Sensor.TYPE_ROTATION_VECTOR to "rot_vec",
            Sensor.TYPE_GAME_ROTATION_VECTOR to "game_rot_vec",
            Sensor.TYPE_LIGHT to "light",
            Sensor.TYPE_PROXIMITY to "proximity",
            Sensor.TYPE_STEP_DETECTOR to "step_detector",
            Sensor.TYPE_STEP_COUNTER to "step_counter",
        )
        private val NAMES = TYPES.toMap()
    }
}
