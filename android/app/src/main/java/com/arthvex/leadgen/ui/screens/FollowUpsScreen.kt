package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Message
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.data.FollowUpKind
import com.arthvex.leadgen.model.FollowUp
import com.arthvex.leadgen.model.Lead
import com.arthvex.leadgen.ui.*
import com.arthvex.leadgen.ui.utils.*
import kotlinx.coroutines.launch

private fun FollowUp.toLead(): Lead = Lead(
    id = id,
    businessName = businessName,
    phone = phone,
    email = email,
    whatsapp = whatsapp,
    city = city,
    status = status,
    lead_priority = lead_priority,
    lead_score = lead_score,
    follow_up_date = follow_up_date,
    last_contacted_date = last_contacted_date,
    total_touchpoints = total_touchpoints
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FollowUpsScreen(
    repo: ApiRepo,
    autoLog: Boolean,
    onOpenLead: (Int) -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var tab by remember { mutableStateOf(FollowUpKind.TODAY) }
    var items by remember { mutableStateOf<List<FollowUp>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var composerLead by remember { mutableStateOf<Lead?>(null) }
    var counts by remember { mutableStateOf<Map<FollowUpKind, Int>>(emptyMap()) }

    fun load(kind: FollowUpKind, refreshCounts: Boolean = true) {
        loading = true
        error = null
        scope.launch {
            repo.followUps(kind).onSuccess { list ->
                if (tab == kind) items = list
                counts = counts + (kind to list.size)
            }.onFailure { error = it.message ?: "Could not load follow-ups" }
            loading = false
            if (refreshCounts) {
                FollowUpKind.entries.filter { it != kind }.forEach { other ->
                    repo.followUps(other).onSuccess { counts = counts + (other to it.size) }
                }
            }
        }
    }

    LaunchedEffect(Unit) { load(FollowUpKind.TODAY) }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Follow-ups", fontWeight = FontWeight.Bold)
                        Text(
                            "Work the queue, then log the outcome",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { load(tab) }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                    }
                }
            )
        }
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            ScrollableTabRow(
                selectedTabIndex = FollowUpKind.entries.indexOf(tab),
                edgePadding = 8.dp
            ) {
                FollowUpKind.entries.forEach { kind ->
                    val count = counts[kind]
                    Tab(
                        selected = tab == kind,
                        onClick = {
                            tab = kind
                            items = emptyList()
                            load(kind)
                        },
                        text = {
                            Text(
                                if (count != null) "${kind.label} ($count)" else kind.label,
                                fontWeight = if (tab == kind) FontWeight.SemiBold else FontWeight.Normal
                            )
                        }
                    )
                }
            }
            if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
            error?.let {
                Card(
                    Modifier.fillMaxWidth().padding(12.dp),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text("Could not load follow-ups", fontWeight = FontWeight.SemiBold)
                        Text(it, style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = { load(tab) }) { Text("Retry") }
                    }
                }
            }

            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(items, key = { it.id }) { followUp ->
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(16.dp),
                        onClick = { onOpenLead(followUp.id) }
                    ) {
                        Column(Modifier.padding(14.dp)) {
                            Row(verticalAlignment = Alignment.Top) {
                                ScoreBadge(followUp.lead_score)
                                Spacer(Modifier.width(10.dp))
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        followUp.businessName ?: "Unnamed business",
                                        style = MaterialTheme.typography.titleMedium,
                                        fontWeight = FontWeight.SemiBold,
                                        maxLines = 1
                                    )
                                    Text(
                                        listOfNotNull(followUp.city, followUp.status?.let(::statusLabel))
                                            .joinToString(" · "),
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                }
                                Pill(
                                    followUp.lead_priority ?: "COLD",
                                    priorityColor(followUp.lead_priority)
                                )
                            }
                            Spacer(Modifier.height(8.dp))
                            Text(
                                followUp.action ?: followUp.step ?: "Follow up",
                                style = MaterialTheme.typography.bodyMedium,
                                fontWeight = FontWeight.Medium,
                                color = MaterialTheme.colorScheme.primary
                            )
                            val dueText = when (tab) {
                                FollowUpKind.SUGGESTED ->
                                    followUp.days_since_contact?.let { "$it days since last contact" } ?: ""
                                else -> followUp.follow_up_date?.let {
                                    "${prettyDate(it)} · ${relativeFollowUp(it)}"
                                } ?: ""
                            }
                            if (dueText.isNotBlank()) {
                                Text(
                                    dueText,
                                    style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                            Spacer(Modifier.height(10.dp))
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                OutlinedButton(
                                    onClick = {
                                        callLead(context, repo, followUp.toLead(), autoLog, scope) { msg ->
                                            scope.launch { snackbar.showSnackbar(msg) }
                                        }
                                    },
                                    modifier = Modifier.weight(1f),
                                    enabled = !followUp.phone.isNullOrBlank()
                                ) {
                                    Icon(Icons.Default.Call, null, Modifier.size(16.dp))
                                    Spacer(Modifier.width(4.dp))
                                    Text("Call")
                                }
                                Button(
                                    onClick = { composerLead = followUp.toLead() },
                                    modifier = Modifier.weight(1f),
                                    enabled = !(followUp.whatsapp ?: followUp.phone).isNullOrBlank()
                                ) {
                                    Icon(Icons.Default.Message, null, Modifier.size(16.dp))
                                    Spacer(Modifier.width(4.dp))
                                    Text("WhatsApp")
                                }
                                if (tab != FollowUpKind.SUGGESTED) {
                                    FilledTonalButton(
                                        onClick = {
                                            scope.launch {
                                                repo.updateLead(followUp.id, mapOf("follow_up_date" to null))
                                                    .onSuccess {
                                                        snackbar.showSnackbar("Follow-up cleared")
                                                        load(tab)
                                                    }
                                                    .onFailure {
                                                        snackbar.showSnackbar("Could not clear follow-up")
                                                    }
                                            }
                                        }
                                    ) {
                                        Icon(Icons.Default.Check, contentDescription = "Done")
                                    }
                                    TextButton(
                                        onClick = {
                                            scope.launch {
                                                repo.updateLead(followUp.id, mapOf("follow_up_date" to kotlinx.serialization.json.JsonPrimitive(isoDaysFromNow(3))))
                                                    .onSuccess { snackbar.showSnackbar("Snoozed 3 days"); load(tab) }
                                                    .onFailure { snackbar.showSnackbar("Could not snooze") }
                                            }
                                        }
                                    ) { Text("+3d") }
                                }
                            }
                        }
                    }
                }
                if (items.isEmpty() && !loading) {
                    item {
                        EmptyState(
                            title = "Nothing in ${tab.label.lowercase()}",
                            subtitle = when (tab) {
                                FollowUpKind.SUGGESTED -> "Leads following the Day 3 / 7 / 14 cadence will appear here."
                                FollowUpKind.UPCOMING -> "Follow-ups scheduled in the next 7 days show up here."
                                else -> "You're all caught up. New follow-ups appear as leads are worked."
                            }
                        )
                    }
                }
                item { Spacer(Modifier.height(12.dp)) }
            }
        }
    }

    composerLead?.let { lead ->
        WhatsAppComposerSheet(
            lead = lead,
            repo = repo,
            autoLog = autoLog,
            onDismiss = { composerLead = null },
            onStatus = { msg -> scope.launch { snackbar.showSnackbar(msg) } }
        )
    }
}
