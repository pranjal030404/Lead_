package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Checklist
import androidx.compose.material.icons.filled.FileDownload
import androidx.compose.material.icons.filled.MergeType
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.Facets
import com.arthvex.leadgen.model.Lead
import com.arthvex.leadgen.ui.Picker
import com.arthvex.leadgen.ui.utils.shareCsv
import com.arthvex.leadgen.ui.LeadCard
import com.arthvex.leadgen.ui.WhatsAppComposerSheet
import com.arthvex.leadgen.ui.utils.callLead
import com.arthvex.leadgen.ui.utils.digitsOnly
import kotlinx.coroutines.launch

private val STATUS_FILTERS = listOf(
    null to "All statuses",
    "NEW" to "New",
    "CONTACTED" to "Contacted",
    "REPLIED" to "Replied",
    "MEETING_SET" to "Meeting set",
    "PROPOSAL_SENT" to "Proposal sent",
    "NEGOTIATION" to "Negotiation",
    "WON" to "Won",
    "LOST" to "Lost"
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LeadsScreen(
    repo: ApiRepo,
    autoLog: Boolean,
    onOpenLead: (Int) -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    val keyboard = LocalSoftwareKeyboardController.current

    var query by remember { mutableStateOf("") }
    var priority by remember { mutableStateOf<String?>(null) }
    var status by remember { mutableStateOf<String?>(null) }
    var leads by remember { mutableStateOf<List<Lead>>(emptyList()) }
    var total by remember { mutableStateOf(0) }
    var loading by remember { mutableStateOf(false) }
    var loadingMore by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var composerLead by remember { mutableStateOf<Lead?>(null) }
    var statusMenuOpen by remember { mutableStateOf(false) }
    var city by remember { mutableStateOf<String?>(null) }
    var niche by remember { mutableStateOf<String?>(null) }
    var website by remember { mutableStateOf<Boolean?>(null) }
    var sort by remember { mutableStateOf("created_at" to "desc") }
    var facets by remember { mutableStateOf(Facets()) }
    var showFilters by remember { mutableStateOf(false) }
    var selecting by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf(setOf<Int>()) }
    var bulkOpen by remember { mutableStateOf(false) }
    var confirmDedupe by remember { mutableStateOf(false) }

    fun load(reset: Boolean) {
        if (reset) {
            loading = true
            error = null
            leads = emptyList()
            total = 0
        } else {
            loadingMore = true
        }
        scope.launch {
            val offset = if (reset) 0 else leads.size
            repo.leads(
                query = query.ifBlank { null },
                status = status,
                priority = priority,
                city = city,
                niche = niche,
                hasWebsite = website,
                sort = sort.first,
                direction = sort.second,
                limit = 50,
                offset = offset
            ).onSuccess { page ->
                total = page.total
                leads = if (reset) page.leads else leads + page.leads
                error = null
            }.onFailure {
                error = it.message ?: "Could not load leads"
            }
            loading = false
            loadingMore = false
        }
    }

    LaunchedEffect(Unit) {
        load(true)
        repo.facets().onSuccess { facets = it }
    }

    fun exportCsv() {
        scope.launch {
            repo.exportLeads(
                mapOf(
                    "q" to query.ifBlank { null }, "status" to status, "priority" to priority,
                    "city" to city, "niche" to niche, "has_website" to website?.toString()
                )
            ).onSuccess { shareCsv(context, "leads.csv", it) }
                .onFailure { snackbar.showSnackbar(it.message ?: "Export failed") }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Leads", fontWeight = FontWeight.Bold)
                        Text(
                            if (total > 0) "$total matching" else "Your workspace",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { selecting = !selecting; selected = emptySet() }) {
                        Icon(Icons.Default.Checklist, contentDescription = "Select leads")
                    }
                    IconButton(onClick = { exportCsv() }) {
                        Icon(Icons.Default.FileDownload, contentDescription = "Export CSV")
                    }
                    IconButton(onClick = { confirmDedupe = true }) {
                        Icon(Icons.Default.MergeType, contentDescription = "Find duplicates")
                    }
                    IconButton(onClick = { load(true) }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                    }
                }
            )
        }
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                placeholder = { Text("Search business, phone, email…") },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 6.dp),
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = {
                    keyboard?.hide()
                    load(true)
                })
            )

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 12.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                listOf(null to "All", "HOT" to "Hot", "WARM" to "Warm", "COLD" to "Cold").forEach { (value, label) ->
                    FilterChip(
                        selected = priority == value,
                        onClick = {
                            priority = value
                            load(true)
                        },
                        label = { Text(label) }
                    )
                }
                Box {
                    AssistChip(
                        onClick = { statusMenuOpen = true },
                        label = {
                            Text(STATUS_FILTERS.first { it.first == status }.second)
                            Icon(Icons.Default.ArrowDropDown, contentDescription = null)
                        }
                    )
                    DropdownMenu(expanded = statusMenuOpen, onDismissRequest = { statusMenuOpen = false }) {
                        STATUS_FILTERS.forEach { (value, label) ->
                            DropdownMenuItem(
                                text = { Text(label) },
                                onClick = {
                                    status = value
                                    statusMenuOpen = false
                                    load(true)
                                }
                            )
                        }
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                    .padding(horizontal = 12.dp, vertical = 2.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                AssistChip(onClick = { showFilters = !showFilters }, label = { Text(if (showFilters) "Hide filters" else "More filters") })
                AssistChip(onClick = { priority = "HOT"; status = null; city = null; niche = null; website = null; sort = "lead_score" to "desc"; load(true) }, label = { Text("Hot leads") })
                AssistChip(onClick = { priority = null; status = null; website = false; sort = "lead_score" to "desc"; load(true) }, label = { Text("No website") })
                AssistChip(onClick = { priority = null; website = null; status = "NEW"; sort = "lead_score" to "desc"; load(true) }, label = { Text("Never contacted") })
                AssistChip(onClick = {
                    query = ""; priority = null; status = null; city = null; niche = null; website = null
                    sort = "created_at" to "desc"; load(true)
                }, label = { Text("Clear") })
            }
            if (showFilters) {
                Column(Modifier.padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Picker("City", listOf<Pair<String?, String>>(null to "Any") + facets.cities.map { it to it }, city,
                        { city = it; load(true) })
                    Picker("Niche", listOf<Pair<String?, String>>(null to "Any") + facets.niches.map { it to it }, niche,
                        { niche = it; load(true) })
                    Picker("Website", listOf<Pair<Boolean?, String>>(null to "Any", false to "No website", true to "Has website"),
                        website, { website = it; load(true) })
                    Picker("Sort", listOf(
                        ("created_at" to "desc") to "Newest first", ("lead_score" to "desc") to "Highest score",
                        ("business_name" to "asc") to "Name A–Z", ("last_contacted_date" to "asc") to "Longest since contact"
                    ), sort, { sort = it; load(true) })
                }
            }
            if (selecting) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    Text("${selected.size} selected", Modifier.weight(1f), fontWeight = FontWeight.SemiBold)
                    TextButton(onClick = { selected = leads.map { it.id }.toSet() }) { Text("All") }
                    Box {
                        Button(enabled = selected.isNotEmpty(), onClick = { bulkOpen = true }) { Text("Set status") }
                        DropdownMenu(expanded = bulkOpen, onDismissRequest = { bulkOpen = false }) {
                            listOf("CONTACTED", "REPLIED", "MEETING_SET", "PROPOSAL_SENT", "WON", "LOST").forEach { st ->
                                DropdownMenuItem(text = { Text(st.replace('_', ' ')) }, onClick = {
                                    bulkOpen = false
                                    val ids = selected.toList()
                                    scope.launch {
                                        repo.bulkStatus(ids, st)
                                            .onSuccess { snackbar.showSnackbar("${ids.size} lead(s) updated"); selected = emptySet(); load(true) }
                                            .onFailure { snackbar.showSnackbar(it.message ?: "Failed") }
                                    }
                                })
                            }
                        }
                    }
                }
            }

            if (loading) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }
            error?.let {
                Card(
                    modifier = Modifier.fillMaxWidth().padding(12.dp),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text("Could not load leads", fontWeight = FontWeight.SemiBold)
                        Text(it, style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = { load(true) }) { Text("Retry") }
                    }
                }
            }

            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(leads, key = { it.id }) { lead ->
                    Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    if (selecting) Checkbox(
                        checked = lead.id in selected,
                        onCheckedChange = { selected = if (it) selected + lead.id else selected - lead.id }
                    )
                    Box(Modifier.weight(1f)) {
                    LeadCard(
                        lead = lead,
                        onOpen = {
                            if (selecting) selected = if (lead.id in selected) selected - lead.id else selected + lead.id
                            else onOpenLead(lead.id)
                        },
                        onCall = {
                            callLead(context, repo, lead, autoLog, scope) { msg ->
                                scope.launch { snackbar.showSnackbar(msg) }
                            }
                        },
                        onWhatsApp = { composerLead = lead }
                    )
                    }
                    }
                }
                if (leads.isEmpty() && !loading) {
                    item {
                        com.arthvex.leadgen.ui.EmptyState(
                            title = "No leads found",
                            subtitle = if (query.isBlank()) {
                                "Run a search from More → Search, then come back here."
                            } else {
                                "Nothing matches \"$query\". Try a different search or filter."
                            }
                        )
                    }
                }
                if (leads.size < total) {
                    item {
                        OutlinedButton(
                            onClick = { load(false) },
                            modifier = Modifier.fillMaxWidth(),
                            enabled = !loadingMore
                        ) {
                            Text(if (loadingMore) "Loading…" else "Load more (${leads.size}/$total)")
                        }
                    }
                }
                item { Spacer(Modifier.height(12.dp)) }
            }
        }
    }

    if (confirmDedupe) AlertDialog(
        onDismissRequest = { confirmDedupe = false },
        title = { Text("Merge duplicates?") },
        text = { Text("Finds leads that are the same business and merges them. This cannot be undone.") },
        confirmButton = {
            TextButton(onClick = {
                confirmDedupe = false
                scope.launch {
                    repo.dedupe().onSuccess { snackbar.showSnackbar("$it duplicate(s) merged"); load(true) }
                        .onFailure { snackbar.showSnackbar(it.message ?: "Failed") }
                }
            }) { Text("Merge") }
        },
        dismissButton = { TextButton(onClick = { confirmDedupe = false }) { Text("Cancel") } }
    )

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

