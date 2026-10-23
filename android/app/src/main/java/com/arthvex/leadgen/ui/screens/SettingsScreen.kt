package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.OpenInNew
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.data.Prefs
import com.arthvex.leadgen.model.User
import com.arthvex.leadgen.ui.utils.openUrl
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    repo: ApiRepo,
    prefs: Prefs,
    user: User?,
    onAutoLogChanged: (Boolean) -> Unit,
    onLoggedOut: () -> Unit,
    onBack: () -> Unit = {}
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var autoLog by remember { mutableStateOf(prefs.autoLog) }
    var serverUrl by remember { mutableStateOf(prefs.baseUrl) }
    var testing by remember { mutableStateOf(false) }
    var testResult by remember { mutableStateOf<String?>(null) }
    var testOk by remember { mutableStateOf(false) }
    var confirmLogout by remember { mutableStateOf(false) }
    val isAdmin = user?.role == "admin" || user?.role == "superadmin"
    var company by remember { mutableStateOf("") }
    var portfolio by remember { mutableStateOf("") }
    var suppressed by remember { mutableStateOf<List<com.arthvex.leadgen.model.SuppressionEntry>>(emptyList()) }
    var newSuppress by remember { mutableStateOf("") }
    var brand by remember { mutableStateOf<com.arthvex.leadgen.model.Brand?>(null) }
    var quota by remember { mutableStateOf<com.arthvex.leadgen.model.Quota?>(null) }
    var backups by remember { mutableStateOf<com.arthvex.leadgen.model.BackupsResponse?>(null) }

    fun loadExtras() {
        scope.launch {
            repo.identity().onSuccess { company = it.company_name; portfolio = it.portfolio_url }
            repo.suppression().onSuccess { suppressed = it }
            if (isAdmin) {
                repo.branding().onSuccess { brand = it.brand }
                repo.quota().onSuccess { quota = it }
                repo.backups().onSuccess { backups = it }
            }
        }
    }
    LaunchedEffect(Unit) { loadExtras() }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { com.arthvex.leadgen.ui.BackBar("Settings", "Identity, logging and account", onBack) }
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Card(shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.padding(16.dp)) {
                    Text("Signed in", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        user?.username ?: "Unknown user",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold
                    )
                    Text(
                        (user?.role ?: "user").replaceFirstChar { it.uppercase() } +
                            (user?.email?.let { " · $it" } ?: ""),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }

            Card(shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.padding(16.dp)) {
                    Text("Outreach logging", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Log calls & WhatsApp automatically")
                            Text(
                                "Writes a touchpoint to the CRM when you open the dialer or a " +
                                    "pre-filled WhatsApp chat.",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                        Switch(
                            checked = autoLog,
                            onCheckedChange = {
                                autoLog = it
                                prefs.autoLog = it
                                onAutoLogChanged(it)
                            }
                        )
                    }
                }
            }

            com.arthvex.leadgen.ui.SectionCard("Sender identity", "Used in outreach signatures") {
                com.arthvex.leadgen.ui.FormField(company, { company = it }, "Company name")
                Spacer(Modifier.height(8.dp))
                com.arthvex.leadgen.ui.FormField(portfolio, { portfolio = it }, "Portfolio URL")
                Spacer(Modifier.height(8.dp))
                Button(onClick = {
                    scope.launch {
                        repo.saveIdentity(company, portfolio)
                            .onSuccess { snackbar.showSnackbar("Identity saved") }
                            .onFailure { snackbar.showSnackbar(it.message ?: "Failed") }
                    }
                }) { Text("Save identity") }
            }

            com.arthvex.leadgen.ui.SectionCard("Do-not-contact list", "${suppressed.size} entries") {
                com.arthvex.leadgen.ui.FormField(newSuppress, { newSuppress = it }, "Email or domain")
                Spacer(Modifier.height(6.dp))
                OutlinedButton(enabled = newSuppress.isNotBlank(), onClick = {
                    val v = newSuppress.trim()
                    scope.launch {
                        repo.addSuppression(v).onSuccess { newSuppress = ""; loadExtras() }
                            .onFailure { snackbar.showSnackbar(it.message ?: "Failed") }
                    }
                }) { Text("Add") }
                suppressed.take(30).forEach { e ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        Text(e.value, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = {
                            scope.launch { repo.removeSuppression(e.value, e.kind).onSuccess { loadExtras() } }
                        }) { Text("Remove") }
                    }
                }
            }

            if (isAdmin) {
                quota?.let { q ->
                    com.arthvex.leadgen.ui.SectionCard("Provider & quota") {
                        com.arthvex.leadgen.ui.KeyValue("Active provider", q.provider)
                        com.arthvex.leadgen.ui.KeyValue("Google calls today", "${q.google.used_today} / ${q.google.cap}")
                        com.arthvex.leadgen.ui.KeyValue("By stage", q.google.by_tier.entries.joinToString(", ") { "${it.key}: ${it.value}" })
                        com.arthvex.leadgen.ui.KeyValue("Hunter.io", "${q.hunter.used_this_month} / ${q.hunter.cap} this month")
                        Text(
                            "This cap is an app-side second layer. Also set a Requests-per-day quota limit on the API in Google Cloud Console — a billing alert only emails you.",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                brand?.let { b -> BrandingSection(repo, b, snackbar) { loadExtras() } }
                backups?.let { bk ->
                    com.arthvex.leadgen.ui.SectionCard("Backups", "Keep ${bk.keep_days} days") {
                        bk.backups.take(10).forEach { com.arthvex.leadgen.ui.KeyValue(it.name, "${it.size_kb} KB · ${com.arthvex.leadgen.ui.utils.prettyDate(it.created)}") }
                        OutlinedButton(onClick = {
                            scope.launch {
                                repo.makeBackup().onSuccess { snackbar.showSnackbar("Backup created"); loadExtras() }
                                    .onFailure { snackbar.showSnackbar(it.message ?: "Backup failed") }
                            }
                        }) { Text("Back up now") }
                    }
                }
            }

            Card(shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.padding(16.dp)) {
                    Text("Web dashboard", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "Search, enrichment, approvals and admin tools live in the full web app.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(Modifier.height(10.dp))
                    OutlinedButton(onClick = { openUrl(context, prefs.baseUrl) }) {
                        Icon(Icons.Default.OpenInNew, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Open ${prefs.baseUrl}")
                    }
                }
            }

            Card(shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.padding(16.dp)) {
                    Text("Account", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(10.dp))
                    OutlinedButton(
                        onClick = { confirmLogout = true },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Sign out") }
                }
            }

            Text(
                "Arthvex LeadGen Android 1.0.0\nMobile companion for the self-hosted lead CRM.",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(vertical = 8.dp)
            )
        }
    }

    if (confirmLogout) {
        AlertDialog(
            onDismissRequest = { confirmLogout = false },
            title = { Text("Sign out?") },
            text = { Text("You will need your username and password to sign in again.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmLogout = false
                    scope.launch {
                        repo.logout()
                        onLoggedOut()
                    }
                }) { Text("Sign out") }
            },
            dismissButton = {
                TextButton(onClick = { confirmLogout = false }) { Text("Cancel") }
            }
        )
    }
}
