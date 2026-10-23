package com.arthvex.leadgen.ui.screens

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.Brand
import com.arthvex.leadgen.ui.FormField
import com.arthvex.leadgen.ui.SectionCard
import kotlinx.coroutines.launch

private val ALLOWED_IMAGES = setOf("image/png", "image/jpeg", "image/webp", "image/svg+xml")
private const val MAX_IMAGE_BYTES = 1_048_576

/** Admin-only white-label controls; mirrors the web Settings → branding cards. */
@Composable
fun BrandingSection(repo: ApiRepo, brand: Brand, snackbar: SnackbarHostState, onChanged: () -> Unit) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var name by remember(brand) { mutableStateOf(brand.brand_name) }
    var shortName by remember(brand) { mutableStateOf(brand.brand_short_name) }
    var tagline by remember(brand) { mutableStateOf(brand.brand_tagline) }
    var accent by remember(brand) { mutableStateOf(brand.brand_accent) }
    var accent2 by remember(brand) { mutableStateOf(brand.brand_accent_2) }
    var background by remember(brand) { mutableStateOf(brand.brand_background) }
    var title by remember(brand) { mutableStateOf(brand.brand_landing_title) }
    var subtitle by remember(brand) { mutableStateOf(brand.brand_landing_subtitle) }
    var company by remember(brand) { mutableStateOf(brand.brand_company_name) }
    var support by remember(brand) { mutableStateOf(brand.brand_support_email) }
    var footer by remember(brand) { mutableStateOf(brand.brand_footer) }
    var confirmReset by remember { mutableStateOf(false) }
    var pendingFavicon by remember { mutableStateOf(false) }

    fun upload(uri: Uri?, favicon: Boolean) {
        if (uri == null) return
        scope.launch {
            val resolver = context.contentResolver
            val mime = resolver.getType(uri).orEmpty()
            if (mime !in ALLOWED_IMAGES) {
                snackbar.showSnackbar("Choose a png, jpg, webp or svg image"); return@launch
            }
            val bytes = resolver.openInputStream(uri)?.use { it.readBytes() }
            if (bytes == null || bytes.isEmpty()) { snackbar.showSnackbar("Could not read that file"); return@launch }
            if (bytes.size > MAX_IMAGE_BYTES) { snackbar.showSnackbar("Image must be 1 MB or smaller"); return@launch }
            val ext = mime.substringAfter('/').substringBefore('+')
            repo.uploadBrandImage(bytes, "${if (favicon) "favicon" else "logo"}.$ext", mime, favicon)
                .onSuccess { snackbar.showSnackbar(if (favicon) "Favicon uploaded" else "Logo uploaded"); onChanged() }
                .onFailure { snackbar.showSnackbar(it.message ?: "Upload failed") }
        }
    }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { upload(it, pendingFavicon) }

    SectionCard("White-label branding", "Admin only · applies to the web app and emails") {
        FormField(name, { name = it }, "Product name")
        Spacer(Modifier.height(8.dp)); FormField(shortName, { shortName = it }, "Wordmark suffix")
        Spacer(Modifier.height(8.dp)); FormField(tagline, { tagline = it }, "Tagline")
        Spacer(Modifier.height(8.dp)); FormField(accent, { accent = it }, "Accent colour (#hex)")
        Spacer(Modifier.height(8.dp)); FormField(accent2, { accent2 = it }, "Accent highlight (#hex)")
        Spacer(Modifier.height(8.dp)); FormField(background, { background = it }, "Background (#hex)")
        Spacer(Modifier.height(8.dp))
        Button(onClick = {
            val hex = Regex("^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")
            if (listOf(accent, accent2, background).any { it.isNotBlank() && !hex.matches(it.trim()) }) {
                scope.launch { snackbar.showSnackbar("Colours must be hex like #22cc88") }
                return@Button
            }
            scope.launch {
                repo.saveBranding(mapOf(
                    "brand_name" to name, "brand_short_name" to shortName, "brand_tagline" to tagline,
                    "brand_accent" to accent, "brand_accent_2" to accent2, "brand_background" to background
                )).onSuccess { snackbar.showSnackbar("Branding saved"); onChanged() }
                    .onFailure { snackbar.showSnackbar(it.message ?: "Failed") }
            }
        }) { Text("Save branding") }
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { pendingFavicon = false; picker.launch("image/*") }) { Text("Upload logo") }
            OutlinedButton(onClick = { pendingFavicon = true; picker.launch("image/*") }) { Text("Upload favicon") }
        }
        Text(
            "png, jpg, webp or svg · max 1 MB",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }

    SectionCard("Landing page & contact copy") {
        FormField(title, { title = it }, "Landing headline")
        Spacer(Modifier.height(8.dp)); FormField(subtitle, { subtitle = it }, "Landing sub-headline", singleLine = false)
        Spacer(Modifier.height(8.dp)); FormField(company, { company = it }, "Company name (email footer)")
        Spacer(Modifier.height(8.dp)); FormField(support, { support = it }, "Support email")
        Spacer(Modifier.height(8.dp)); FormField(footer, { footer = it }, "Footer line")
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                scope.launch {
                    repo.saveBranding(mapOf(
                        "brand_landing_title" to title, "brand_landing_subtitle" to subtitle,
                        "brand_company_name" to company, "brand_support_email" to support, "brand_footer" to footer
                    )).onSuccess { snackbar.showSnackbar("Copy saved"); onChanged() }
                        .onFailure { snackbar.showSnackbar(it.message ?: "Failed") }
                }
            }) { Text("Save copy") }
            OutlinedButton(onClick = { confirmReset = true }) { Text("Reset all to default") }
        }
    }

    if (confirmReset) AlertDialog(
        onDismissRequest = { confirmReset = false },
        title = { Text("Reset branding?") },
        text = { Text("Removes every branding override, including the logo and favicon.") },
        confirmButton = {
            TextButton(onClick = {
                confirmReset = false
                scope.launch {
                    repo.resetBranding().onSuccess { snackbar.showSnackbar("Reset to default"); onChanged() }
                        .onFailure { snackbar.showSnackbar(it.message ?: "Failed") }
                }
            }) { Text("Reset") }
        },
        dismissButton = { TextButton(onClick = { confirmReset = false }) { Text("Cancel") } }
    )
}
