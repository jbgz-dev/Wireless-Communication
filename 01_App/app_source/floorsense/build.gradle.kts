plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "edu.bnbu.floorsense.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "edu.bnbu.floorsense.app"
        minSdk = 28
        targetSdk = 34
        versionCode = 4
        versionName = "0.4"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    // rf_model.json 是文本（约 1 MB），不压缩可以直接按流读取
    androidResources {
        noCompress += "json"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")

    testImplementation("junit:junit:4.13.2")
    // Android 的 org.json 在本地单元测试里只是空壳，测试时换成真实实现
    testImplementation("org.json:json:20231013")
}
