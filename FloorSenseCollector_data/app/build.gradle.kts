plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "edu.bnbu.floorsense.collector"
    compileSdk = 34

    defaultConfig {
        applicationId = "edu.bnbu.floorsense.collector"
        minSdk = 28
        targetSdk = 34
        versionCode = 2
        versionName = "1.1"
        testInstrumentationRunner = "edu.bnbu.floorsense.collector.WalkFlowInstrumentation"
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
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
}
