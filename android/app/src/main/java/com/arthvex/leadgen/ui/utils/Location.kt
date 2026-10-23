package com.arthvex.leadgen.ui.utils

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * One-shot device location without Play Services. The app only needs a rough
 * point to centre a "search near me", so we take the best last-known fix and
 * otherwise ask each provider once.
 */
suspend fun currentLocation(context: Context): Pair<Double, Double> {
    val fine = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION)
    val coarse = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION)
    if (fine != PackageManager.PERMISSION_GRANTED && coarse != PackageManager.PERMISSION_GRANTED) {
        throw IllegalStateException("Location permission is needed for a near-me search")
    }
    val manager = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
        ?: throw IllegalStateException("This device has no location service")
    val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
        .filter { runCatching { manager.isProviderEnabled(it) }.getOrDefault(false) }
    if (providers.isEmpty()) throw IllegalStateException("Turn on location services, then retry")

    providers.forEach { provider ->
        val last = runCatching { manager.getLastKnownLocation(provider) }.getOrNull()
        if (last != null && System.currentTimeMillis() - last.time < 10 * 60 * 1000L) {
            return last.latitude to last.longitude
        }
    }

    return suspendCancellableCoroutine { cont ->
        val listener = object : android.location.LocationListener {
            override fun onLocationChanged(location: Location) {
                manager.removeUpdates(this)
                if (cont.isActive) cont.resume(location.latitude to location.longitude)
            }

            override fun onProviderEnabled(provider: String) {}
            override fun onProviderDisabled(provider: String) {}
            @Deprecated("Required by the platform interface")
            override fun onStatusChanged(provider: String?, status: Int, extras: android.os.Bundle?) {}
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                manager.getCurrentLocation(
                    providers.first(),
                    null,
                    ContextCompat.getMainExecutor(context)
                ) { location ->
                    manager.removeUpdates(listener)
                    if (location != null) {
                        if (cont.isActive) cont.resume(location.latitude to location.longitude)
                    } else if (cont.isActive) {
                        cont.resumeWithException(IllegalStateException("Could not get your location"))
                    }
                }
            } else {
                @Suppress("DEPRECATION")
                manager.requestSingleUpdate(providers.first(), listener, null)
            }
        } catch (e: SecurityException) {
            if (cont.isActive) cont.resumeWithException(e)
        }
    }
}
