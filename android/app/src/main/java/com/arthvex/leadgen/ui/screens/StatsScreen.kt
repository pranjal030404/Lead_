package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.Conversions
import com.arthvex.leadgen.model.Stats
import com.arthvex.leadgen.ui.*
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StatsScreen(repo: ApiRepo, onOpenLead: (Int) -> Unit = {}, onOpen: (String) -> Unit = {}) {
    val scope = rememberCoroutineScope()
    var stats by remember { mutableStateOf<Stats?>(null) }
    var conv by remember { mutableStateOf<Conversions?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    fun load() {
        loading = true
        error = null
        scope.launch {
            repo.stats().onSuccess { stats = it }.onFailure { error = it.message }
            repo.conversions().onSuccess { conv = it }
            loading = false
        }
    }

    LaunchedEffect(Unit) { load() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Dashboard", fontWeight = FontWeight.Bold)
                        Text(
                            "Your pipeline at a glance",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { load() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
            error?.let {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                    Column(Modifier.padding(12.dp)) {
                        Text("Could not load stats", fontWeight = FontWeight.SemiBold)
                        Text(it, style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = { load() }) { Text("Retry") }
                    }
                }
            }

            stats?.let { s ->
                if (s.quota.google.warning) Banner(
                    "API usage is at ${s.quota.google.pct}% of today's cap (${s.quota.google.used_today}/${s.quota.google.cap}). Searches stop automatically at 100%.",
                    WarmColor
                )
                if (s.unresolved_failures > 0) Banner(
                    "${s.unresolved_failures} unresolved job failure(s). Review them under More → Control.", HotColor,
                    onClick = { onOpen("automation") }
                )
                if (s.pending_approval > 0) Banner(
                    "${s.pending_approval} message(s) waiting for your approval.", InfoColor,
                    onClick = { onOpen("approvals") }
                )
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Button(onClick = { onOpen("followups") }, modifier = Modifier.weight(1f)) { Text("Today's follow-ups") }
                    OutlinedButton(onClick = { onOpen("search") }, modifier = Modifier.weight(1f)) { Text("New search") }
                }
            }
            stats?.let { s ->
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    StatTile("Total leads", "${s.total_leads}", InfoColor, Modifier.weight(1f))
                    StatTile("Hot", "${s.hot_leads}", HotColor, Modifier.weight(1f))
                }
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    StatTile("Warm", "${s.warm_leads}", WarmColor, Modifier.weight(1f))
                    StatTile("No website", "${s.no_website}", PurpleColor, Modifier.weight(1f))
                }
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    StatTile("Follow-ups due", "${s.follow_ups_due}", HotColor, Modifier.weight(1f))
                    StatTile("Won", "${s.won}", GoodColor, Modifier.weight(1f))
                }
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    StatTile("Contacted", "${s.contacted}", infoColorForContacted(), Modifier.weight(1f))
                    StatTile("New this week", "${s.new_this_week}", InfoColor, Modifier.weight(1f))
                }

                Card(shape = RoundedCornerShape(16.dp)) {
                    Column(Modifier.padding(14.dp)) {
                        Text("Pipeline", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(10.dp))
                        val max = (s.by_status.maxOfOrNull { it.n } ?: 1).coerceAtLeast(1)
                        s.by_status.sortedByDescending { it.n }.forEach { row ->
                            val label = statusLabel(row.status)
                            Row(
                                Modifier.fillMaxWidth().padding(vertical = 5.dp),
                                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically
                            ) {
                                Text(
                                    label,
                                    modifier = Modifier.width(105.dp),
                                    style = MaterialTheme.typography.bodySmall
                                )
                                Box(
                                    Modifier
                                        .weight(1f)
                                        .height(8.dp)
                                        .background(
                                            MaterialTheme.colorScheme.surfaceVariant,
                                            RoundedCornerShape(50)
                                        )
                                ) {
                                    Box(
                                        Modifier
                                            .fillMaxHeight()
                                            .fillMaxWidth(row.n.toFloat() / max)
                                            .background(statusColor(row.status), RoundedCornerShape(50))
                                    )
                                }
                                Spacer(Modifier.width(8.dp))
                                Text("${row.n}", style = MaterialTheme.typography.labelMedium)
                            }
                        }
                        if (s.by_status.isEmpty()) {
                            Text(
                                "No leads yet.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }

                conv?.let { c ->
                    SectionCard(
                        "Conversion funnel",
                        "${c.results_seen} search results seen → ${c.reached["NEW"] ?: 0} leads kept (${"%.0f".format(c.search_to_lead)}%)"
                    ) {
                        if (c.steps.isEmpty()) Text("No pipeline movement yet.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        c.steps.forEach {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                Text("${it.from.replace('_', ' ')} → ${it.to.replace('_', ' ')}", style = MaterialTheme.typography.bodySmall)
                                Text("${it.to_count}/${it.from_count} · ${"%.0f".format(it.rate)}%", style = MaterialTheme.typography.labelSmall)
                            }
                            HorizontalBar((it.rate / 100).toFloat())
                            Spacer(Modifier.height(8.dp))
                        }
                    }
                }
                val g = s.quota.google
                SectionCard("API budget today", "Provider: ${s.quota.provider}") {
                    Text("${g.used_today} / ${g.cap}", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                    HorizontalBar(g.pct / 100f, if (g.pct > 90) HotColor else if (g.pct > 70) WarmColor else GoodColor)
                    Spacer(Modifier.height(8.dp))
                    KeyValue("Hunter.io", "${s.quota.hunter.used_this_month} / ${s.quota.hunter.cap} this month")
                    KeyValue("Pending enrichment", s.pending_enrichment.toString())
                }
                SectionCard("By niche") {
                    if (s.by_niche.isEmpty()) Text("Run a search to see this.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    s.by_niche.forEach { KeyValue(it.niche ?: "—", "${it.n} leads · ${it.hot} hot") }
                }
                SectionCard("By city") {
                    if (s.by_city.isEmpty()) Text("Run a search to see this.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    s.by_city.forEach { KeyValue(it.city ?: "—", "${it.n} leads · ${it.hot} hot") }
                }

                Card(shape = RoundedCornerShape(16.dp)) {
                    Column(Modifier.padding(14.dp)) {
                        Text("Automation", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(8.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Pill("${s.pending_approval} awaiting approval", WarmColor)
                            Pill("${s.won_this_month} won this month", GoodColor)
                        }
                    }
                }
            }
        }
    }
}

private fun infoColorForContacted() = androidx.compose.ui.graphics.Color(0xFF0891B2)

@Composable
private fun Banner(text: String, color: androidx.compose.ui.graphics.Color, onClick: (() -> Unit)? = null) {
    Card(
        onClick = onClick ?: {},
        enabled = onClick != null,
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = color.copy(alpha = 0.15f))
    ) { Text(text, Modifier.padding(12.dp), style = MaterialTheme.typography.bodySmall) }
}
