package com.arthvex.leadgen.ui.utils

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

private val isoDate = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply {
    timeZone = TimeZone.getDefault()
}
private val isoDateTime = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply {
    timeZone = TimeZone.getDefault()
}
private val pretty = SimpleDateFormat("d MMM yyyy", Locale.US)

fun prettyDate(raw: String?): String {
    if (raw.isNullOrBlank()) return ""
    val datePart = raw.take(10)
    return try {
        val parsed = isoDate.parse(datePart)
        if (parsed != null) pretty.format(parsed) else datePart
    } catch (_: Exception) {
        datePart
    }
}

fun prettyDateTime(raw: String?): String {
    if (raw.isNullOrBlank()) return ""
    return try {
        val cleaned = raw.substringBefore('.').substringBefore('+').trim()
        val parsed = isoDateTime.parse(cleaned.take(19))
        if (parsed != null) SimpleDateFormat("d MMM · h:mm a", Locale.US).format(parsed) else raw.take(16)
    } catch (_: Exception) {
        raw.take(16).replace('T', ' ')
    }
}

fun todayIso(): String = isoDate.format(Calendar.getInstance().time)

fun isoDaysFromNow(days: Int): String {
    val calendar = Calendar.getInstance()
    calendar.add(Calendar.DAY_OF_YEAR, days)
    return isoDate.format(calendar.time)
}

fun relativeFollowUp(raw: String?): String {
    if (raw.isNullOrBlank()) return "No follow-up set"
    val due = try {
        isoDate.parse(raw.take(10))
    } catch (_: Exception) {
        null
    } ?: return raw.take(10)
    val today = Calendar.getInstance()
    today.set(Calendar.HOUR_OF_DAY, 0)
    today.set(Calendar.MINUTE, 0)
    today.set(Calendar.SECOND, 0)
    today.set(Calendar.MILLISECOND, 0)
    val diff = ((due.time - today.timeInMillis) / (1000 * 60 * 60 * 24)).toInt()
    return when {
        diff == 0 -> "Due today"
        diff == 1 -> "Due tomorrow"
        diff > 1 -> "Due in $diff days"
        diff == -1 -> "Overdue by 1 day"
        else -> "Overdue by ${-diff} days"
    }
}
