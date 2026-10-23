package com.arthvex.leadgen.ui.utils

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.widget.Toast
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.InteractionRequest
import com.arthvex.leadgen.model.Lead
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * Every outbound contact action lives here so calls, WhatsApp and email behave
 * the same from the lead list, the follow-up queue and the lead detail screen.
 */

fun digitsOnly(raw: String?): String = (raw ?: "").replace(Regex("[^0-9]"), "")

/**
 * `leads.whatsapp` is already a wa.me-ready international number. Only the
 * phone fallback needs the Indian 10-digit heuristic.
 */
fun waNumber(raw: String?, applyIndiaHeuristic: Boolean = false): String? {
    val digits = digitsOnly(raw)
    if (digits.isEmpty()) return null
    return if (applyIndiaHeuristic && digits.length == 10) "91$digits" else digits
}

fun openDialer(context: Context, phone: String?): Boolean {
    val number = (phone ?: "").trim()
    if (number.isEmpty()) {
        Toast.makeText(context, "No phone number on this lead", Toast.LENGTH_SHORT).show()
        return false
    }
    return try {
        context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${Uri.encode(number)}")))
        true
    } catch (e: Exception) {
        Toast.makeText(context, "No dialer app available", Toast.LENGTH_SHORT).show()
        false
    }
}

fun openWhatsApp(context: Context, rawNumber: String?, message: String? = null): Boolean {
    val number = waNumber(rawNumber)
    if (number == null) {
        Toast.makeText(context, "This lead has no WhatsApp-capable number", Toast.LENGTH_SHORT).show()
        return false
    }
    val uri = buildString {
        append("https://wa.me/")
        append(number)
        if (!message.isNullOrBlank()) append("?text=").append(Uri.encode(message))
    }
    return try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(uri)))
        true
    } catch (e: Exception) {
        Toast.makeText(context, "Could not open WhatsApp", Toast.LENGTH_SHORT).show()
        false
    }
}

fun openEmail(context: Context, address: String?) {
    val target = address?.trim().orEmpty()
    if (target.isEmpty()) {
        Toast.makeText(context, "No email address on this lead", Toast.LENGTH_SHORT).show()
        return
    }
    try {
        context.startActivity(Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:${Uri.encode(target)}")))
    } catch (e: Exception) {
        Toast.makeText(context, "No email app available", Toast.LENGTH_SHORT).show()
    }
}

fun openUrl(context: Context, url: String?) {
    val target = url?.trim().orEmpty()
    if (target.isEmpty()) {
        Toast.makeText(context, "No link on this lead", Toast.LENGTH_SHORT).show()
        return
    }
    val full = if (target.startsWith("http")) target else "https://$target"
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(full)))
    } catch (e: Exception) {
        Toast.makeText(context, "Could not open link", Toast.LENGTH_SHORT).show()
    }
}

fun openAppSettings(context: Context) {
    val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
    intent.data = Uri.fromParts("package", context.packageName, null)
    context.startActivity(intent)
}

/**
 * Opens the dialer and, when auto-logging is enabled, records a CALL
 * touchpoint against the lead. The API moves a NEW lead to CONTACTED by
 * itself when an interaction is logged.
 */
fun callLead(
    context: Context,
    repo: ApiRepo,
    lead: Lead,
    autoLog: Boolean,
    scope: CoroutineScope,
    onStatus: (String) -> Unit = {}
) {
    val opened = openDialer(context, lead.phone)
    if (!opened) return
    if (autoLog) {
        scope.launch {
            repo.logInteraction(
                lead.id,
                InteractionRequest(
                    type = "call",
                    content = "Call started from the Android app.",
                    outcome = "Dialer opened"
                )
            ).onSuccess { onStatus("Dialer opened · call logged") }
                .onFailure { onStatus("Dialer opened (could not log)") }
        }
    } else {
        onStatus("Dialer opened")
    }
}

/**
 * Opens a pre-filled WhatsApp chat and records the message as a WHATSAPP
 * touchpoint. WhatsApp does not allow apps to send silently, so the user still
 * taps send inside WhatsApp - this is deliberate and matches the web app.
 */
fun sendWhatsApp(
    context: Context,
    repo: ApiRepo,
    lead: Lead,
    message: String,
    autoLog: Boolean,
    scope: CoroutineScope,
    onStatus: (String) -> Unit = {}
) {
    val number = if (!lead.whatsapp.isNullOrBlank()) {
        waNumber(lead.whatsapp)
    } else {
        waNumber(lead.phone, applyIndiaHeuristic = true)
    }
    val opened = openWhatsApp(context, number, message)
    if (!opened) return
    if (autoLog) {
        scope.launch {
            repo.logInteraction(
                lead.id,
                InteractionRequest(
                    type = "whatsapp",
                    content = message,
                    outcome = "WhatsApp opened",
                    subject = "WhatsApp message"
                )
            ).onSuccess { onStatus("WhatsApp opened · message logged") }
                .onFailure { onStatus("WhatsApp opened (could not log)") }
        }
    } else {
        onStatus("WhatsApp opened")
    }
}
