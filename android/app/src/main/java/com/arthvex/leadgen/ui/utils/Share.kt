package com.arthvex.leadgen.ui.utils

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import java.io.File

/** Writes [content] to the app cache and hands it to the system share sheet. */
fun shareCsv(context: Context, name: String, content: String) {
    val dir = File(context.cacheDir, "exports").apply { mkdirs() }
    val file = File(dir, name).apply { writeText(content) }
    val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)
    val send = Intent(Intent.ACTION_SEND).apply {
        type = "text/csv"
        putExtra(Intent.EXTRA_STREAM, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    context.startActivity(Intent.createChooser(send, "Export leads").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}
