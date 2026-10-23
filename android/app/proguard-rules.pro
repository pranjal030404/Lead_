
# kotlinx.serialization models
-keepattributes *Annotation*, InnerClasses, Signature
-keep,includedescriptorclasses class com.arthvex.leadgen.model.**$$serializer { *; }
-keepclassmembers class com.arthvex.leadgen.model.** { *** Companion; }
-keepclasseswithmembers class com.arthvex.leadgen.model.** { kotlinx.serialization.KSerializer serializer(...); }
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
