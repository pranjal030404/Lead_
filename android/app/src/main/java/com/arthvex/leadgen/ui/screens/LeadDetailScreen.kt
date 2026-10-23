package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Message
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.AutoFixHigh
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.MessageResponse
import com.arthvex.leadgen.model.MessageTemplate
import kotlinx.serialization.json.JsonElement
import com.arthvex.leadgen.model.InteractionRequest
import com.arthvex.leadgen.model.Lead
import com.arthvex.leadgen.ui.*
import com.arthvex.leadgen.ui.utils.*
import kotlinx.coroutines.launch

private val STATUS_OPTIONS = listOf(
    "NEW", "CONTACTED", "REPLIED", "MEETING_SET", "PROPOSAL_SENT", "NEGOTIATION", "WON", "LOST"
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LeadDetailScreen(
    repo: ApiRepo,
    leadId: Int,
    autoLog: Boolean,
    onBack: () -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var lead by remember { mutableStateOf<Lead?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }
    var showComposer by remember { mutableStateOf(false) }
    var notes by remember { mutableStateOf("") }
    var logType by remember { mutableStateOf("call") }
    var logOutcome by remember { mutableStateOf("") }
    var logNote by remember { mutableStateOf("") }
    var logStatus by remember { mutableStateOf<String?>(null) }
    var logFollow by remember { mutableStateOf<String?>(null) }
    var confirmDelete by remember { mutableStateOf(false) }
    var enriching by remember { mutableStateOf(false) }
    var templates by remember { mutableStateOf<List<MessageTemplate>>(emptyList()) }
    var tplName by remember { mutableStateOf("") }
    var draft by remember { mutableStateOf<MessageResponse?>(null) }
    var draftSubject by remember { mutableStateOf("") }
    var draftBody by remember { mutableStateOf("") }
    var intel by remember { mutableStateOf<Pair<String, JsonElement?>?>(null) }

    fun load() {
        loading = true
        scope.launch {
            repo.lead(leadId).onSuccess {
                lead = it
                notes = it.notes.orEmpty()
                error = null
            }.onFailure { error = it.message ?: "Could not load lead" }
            loading = false
        }
    }

    fun patch(fields: Map<String, kotlinx.serialization.json.JsonElement?>) {
        saving = true
        scope.launch {
            repo.updateLead(leadId, fields).onSuccess {
                load()
            }.onFailure { snackbar.showSnackbar(it.message ?: "Could not save") }
            saving = false
        }
    }

    fun logInteraction() {
        val current = lead ?: return
        saving = true
        scope.launch {
            repo.logInteraction(
                current.id,
                InteractionRequest(
                    type = logType,
                    content = logNote.ifBlank { null },
                    outcome = logOutcome.ifBlank { null },
                    follow_up_date = logFollow,
                    new_status = logStatus
                )
            ).onSuccess {
                logNote = ""
                logOutcome = ""
                logStatus = null
                logFollow = null
                snackbar.showSnackbar("Interaction logged")
                load()
            }.onFailure {
                snackbar.showSnackbar(it.message ?: "Could not log interaction")
            }
            saving = false
        }
    }

    LaunchedEffect(leadId) {
        load()
        repo.templates().onSuccess { list ->
            templates = list
            if (tplName.isBlank()) tplName = list.firstOrNull { it.type == "email" }?.name ?: list.firstOrNull()?.name.orEmpty()
        }
    }

    fun enrich() {
        enriching = true
        scope.launch {
            snackbar.showSnackbar("Enriching — checking the website, this takes a few seconds…")
        }
        scope.launch {
            repo.enrichLead(leadId).onSuccess { snackbar.showSnackbar("Enrichment complete"); load() }
                .onFailure { snackbar.showSnackbar(it.message ?: "Enrichment failed") }
            enriching = false
        }
    }

    fun intelligence(label: String, call: suspend () -> Result<JsonElement>) {
        scope.launch {
            call().onSuccess { intel = label to it }
                .onFailure { snackbar.showSnackbar(it.message ?: "Could not load $label") }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        lead?.businessName ?: "Lead",
                        maxLines = 1,
                        fontWeight = FontWeight.SemiBold
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
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
        if (loading && lead == null) {
            Box(Modifier.padding(padding).fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }

        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 14.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            if (saving || loading) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }
            error?.let {
                Card(
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text("Could not load this lead", fontWeight = FontWeight.SemiBold)
                        Text(it, style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = { load() }) { Text("Retry") }
                    }
                }
            }

            lead?.let { l ->
                LeadHeader(l)

                Card(shape = RoundedCornerShape(16.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(10.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Button(
                            onClick = {
                                callLead(context, repo, l, autoLog, scope) { msg ->
                                    scope.launch { snackbar.showSnackbar(msg) }
                                }
                            },
                            modifier = Modifier.weight(1f),
                            enabled = !l.phone.isNullOrBlank()
                        ) {
                            Icon(Icons.Default.Call, null, Modifier.size(17.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Call")
                        }
                        Button(
                            onClick = { showComposer = true },
                            modifier = Modifier.weight(1f),
                            enabled = !(l.whatsapp ?: l.phone).isNullOrBlank()
                        ) {
                            Icon(Icons.Default.Message, null, Modifier.size(17.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("WhatsApp")
                        }
                        FilledTonalButton(
                            onClick = { openEmail(context, l.email) },
                            enabled = !l.email.isNullOrBlank()
                        ) {
                            Icon(Icons.Default.Email, null, Modifier.size(17.dp))
                        }
                        FilledTonalButton(
                            onClick = { openUrl(context, l.website_url ?: l.google_maps_url) },
                            enabled = !(l.website_url ?: l.google_maps_url).isNullOrBlank()
                        ) {
                            Icon(
                                if (!l.website_url.isNullOrBlank()) Icons.Default.Language else Icons.Default.Place,
                                null,
                                Modifier.size(17.dp)
                            )
                        }
                    }
                }

                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (!l.google_maps_url.isNullOrBlank()) AssistChip(onClick = { openUrl(context, l.google_maps_url) }, label = { Text("Maps") })
                    if (!l.instagram_url.isNullOrBlank()) AssistChip(onClick = { openUrl(context, l.instagram_url) }, label = { Text("Instagram") })
                    if (!l.facebook_url.isNullOrBlank()) AssistChip(onClick = { openUrl(context, l.facebook_url) }, label = { Text("Facebook") })
                    if (!l.linkedin_url.isNullOrBlank()) AssistChip(onClick = { openUrl(context, l.linkedin_url) }, label = { Text("LinkedIn") })
                    AssistChip(
                        onClick = { enrich() }, enabled = !enriching,
                        leadingIcon = { Icon(Icons.Default.AutoFixHigh, null, Modifier.size(16.dp)) },
                        label = { Text(if (enriching) "Enriching…" else "Re-enrich") }
                    )
                    AssistChip(
                        onClick = { confirmDelete = true },
                        leadingIcon = { Icon(Icons.Default.Delete, null, Modifier.size(16.dp)) },
                        label = { Text("Delete") }
                    )
                }

                // ------------------------------------------------------ status --
                SectionCard(title = "Pipeline status") {
                    Row(
                        modifier = Modifier.horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        STATUS_OPTIONS.forEach { option ->
                            FilterChip(
                                selected = l.status == option,
                                onClick = {
                                    if (l.status != option) {
                                        patch(mapOf("status" to kotlinx.serialization.json.JsonPrimitive(option)))
                                    }
                                },
                                label = { Text(statusLabel(option)) }
                            )
                        }
                    }
                }

                // --------------------------------------------------- follow-up --
                SectionCard(title = "Follow-up") {
                    Text(
                        relativeFollowUp(l.follow_up_date) +
                            if (!l.follow_up_date.isNullOrBlank()) " · ${prettyDate(l.follow_up_date)}" else "",
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (l.follow_up_date != null) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        AssistChip(
                            onClick = {
                                patch(mapOf("follow_up_date" to kotlinx.serialization.json.JsonPrimitive(isoDaysFromNow(1))))
                            },
                            label = { Text("Tomorrow") }
                        )
                        AssistChip(
                            onClick = {
                                patch(mapOf("follow_up_date" to kotlinx.serialization.json.JsonPrimitive(isoDaysFromNow(3))))
                            },
                            label = { Text("+3 days") }
                        )
                        AssistChip(
                            onClick = {
                                patch(mapOf("follow_up_date" to kotlinx.serialization.json.JsonPrimitive(isoDaysFromNow(7))))
                            },
                            label = { Text("+1 week") }
                        )
                        AssistChip(
                            onClick = {
                                patch(mapOf("follow_up_date" to null))
                            },
                            label = { Text("Clear") }
                        )
                    }
                }

                // ------------------------------------------------------ details --
                SectionCard(title = "Contact details") {
                    DetailRow("Owner", l.owner_name)
                    DetailRow("Phone", l.phone)
                    DetailRow("WhatsApp", l.whatsapp)
                    DetailRow("Email", l.email)
                    DetailRow("Website", l.website_url)
                    DetailRow("Address", listOfNotNull(l.address, l.area, l.city, l.state, l.country)
                        .filter { it.isNotBlank() }.joinToString(", "))
                    DetailRow("Niche", l.niche)
                    DetailRow("Touchpoints", l.total_touchpoints?.toString())
                    DetailRow("Last contacted", prettyDate(l.last_contacted_date))
                    DetailRow("Phone check", if (!l.phone.isNullOrBlank() && l.phone_valid == 0) "unvalidated" else null)
                    DetailRow("Data quality", l.data_quality_score?.let { "$it/10" })
                    DetailRow("Source", l.source)
                    DetailRow("Found", prettyDate(l.found_date))
                    DetailRow("Enrichment", listOfNotNull(l.enrichment_status, l.enrichment_error).joinToString(" — "))
                    DetailRow("Google rating", l.google_rating?.let {
                        "$it" + (l.google_reviews?.let { r -> " ($r reviews)" } ?: "")
                    })
                }

                // -------------------------------------------------------- notes --
                SectionCard(title = "Notes") {
                    OutlinedTextField(
                        value = notes,
                        onValueChange = { notes = it },
                        modifier = Modifier.fillMaxWidth(),
                        minLines = 2,
                        maxLines = 5,
                        placeholder = { Text("Anything useful about this lead…") }
                    )
                    Spacer(Modifier.height(6.dp))
                    Button(
                        onClick = { patch(mapOf("notes" to kotlinx.serialization.json.JsonPrimitive(notes))) },
                        enabled = notes != l.notes.orEmpty()
                    ) { Text("Save notes") }
                }

                // ------------------------------------------------- log contact --
                SectionCard(title = "Log an interaction") {
                    Row(
                        modifier = Modifier.horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        listOf("call", "whatsapp", "email", "meeting", "note").forEach { type ->
                            FilterChip(
                                selected = logType == type,
                                onClick = { logType = type },
                                label = { Text(type.replaceFirstChar { it.uppercase() }) }
                            )
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(
                        value = logOutcome,
                        onValueChange = { logOutcome = it },
                        label = { Text("Outcome") },
                        placeholder = { Text("no answer / interested / asked for pricing") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true
                    )
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(
                        value = logNote,
                        onValueChange = { logNote = it },
                        label = { Text("Notes") },
                        modifier = Modifier.fillMaxWidth(),
                        minLines = 2
                    )
                    Spacer(Modifier.height(8.dp))
                    Text("Set status (optional)", style = MaterialTheme.typography.labelSmall)
                    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf("CONTACTED", "REPLIED", "MEETING_SET", "PROPOSAL_SENT", "NEGOTIATION", "WON", "LOST").forEach { st ->
                            FilterChip(selected = logStatus == st, onClick = { logStatus = if (logStatus == st) null else st },
                                label = { Text(statusLabel(st)) })
                        }
                    }
                    Spacer(Modifier.height(6.dp))
                    Text("Follow up (optional)", style = MaterialTheme.typography.labelSmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf(1 to "Tomorrow", 3 to "+3d", 7 to "+1w").forEach { (d, label) ->
                            FilterChip(selected = logFollow == isoDaysFromNow(d), onClick = {
                                logFollow = if (logFollow == isoDaysFromNow(d)) null else isoDaysFromNow(d)
                            }, label = { Text(label) })
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    Button(onClick = { logInteraction() }) { Text("Log it") }
                }

                // ----------------------------------------------------- timeline --
                SectionCard(title = "Timeline (${l.interactions.size})") {
                    if (l.interactions.isEmpty()) {
                        Text(
                            "No interactions yet. Calls and WhatsApp messages sent from this app are logged automatically.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    } else {
                        l.interactions.forEach { interaction ->
                            Row(
                                modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
                                verticalAlignment = Alignment.Top
                            ) {
                                Box(
                                    modifier = Modifier
                                        .padding(top = 5.dp)
                                        .size(8.dp)
                                        .background(statusColor(interaction.type), RoundedCornerShape(50))
                                )
                                Spacer(Modifier.width(10.dp))
                                Column {
                                    Text(
                                        interaction.type.replaceFirstChar { it.uppercase() } +
                                            (interaction.outcome?.let { " · $it" } ?: ""),
                                        style = MaterialTheme.typography.bodyMedium,
                                        fontWeight = FontWeight.Medium
                                    )
                                    Text(
                                        prettyDateTime(interaction.date) +
                                            if (interaction.automated == 1) " · automated" else "",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                    interaction.content?.takeIf { it.isNotBlank() }?.let {
                                        Text(
                                            it,
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                                            maxLines = 4
                                        )
                                    }
                                }
                            }
                            HorizontalDivider()
                        }
                    }
                }

                // ------------------------------------------------------ outreach --
                SectionCard(title = "Generate a message") {
                    if (templates.isEmpty()) Text("No templates yet — add one under More → Templates.", style = MaterialTheme.typography.bodySmall)
                    else {
                        Picker("Template", templates.map { it.name to "${it.name} (${it.type})" }, tplName, { tplName = it })
                        Spacer(Modifier.height(8.dp))
                        Button(onClick = {
                            scope.launch {
                                repo.renderMessage(l.id, tplName).onSuccess {
                                    draft = it; draftSubject = it.subject.orEmpty(); draftBody = it.body
                                }.onFailure { snackbar.showSnackbar(it.message ?: "Could not generate") }
                            }
                        }) { Text("Generate") }
                    }
                    draft?.let { d ->
                        Spacer(Modifier.height(8.dp))
                        if (!d.subject.isNullOrBlank()) FormField(draftSubject, { draftSubject = it }, "Subject")
                        Spacer(Modifier.height(8.dp))
                        FormField(draftBody, { draftBody = it }, "Body", singleLine = false)
                        Spacer(Modifier.height(8.dp))
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (!d.to.isNullOrBlank()) Button(onClick = {
                                scope.launch {
                                    repo.queueApproval(l.id, tplName).onSuccess {
                                        snackbar.showSnackbar("Queued — approve it under More → Approve sends"); load()
                                    }.onFailure { snackbar.showSnackbar(it.message ?: "Could not queue") }
                                }
                            }) { Text("Queue for approval") }
                            if (!d.whatsapp_link.isNullOrBlank()) OutlinedButton(onClick = { openUrl(context, d.whatsapp_link) }) { Text("Open in WhatsApp") }
                            OutlinedButton(onClick = {
                                val cm = context.getSystemService(android.content.Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
                                cm.setPrimaryClip(android.content.ClipData.newPlainText("message", draftBody))
                                scope.launch { snackbar.showSnackbar("Copied") }
                            }) { Text("Copy") }
                        }
                        if (d.to.isNullOrBlank()) Text("No email on this lead — use WhatsApp or add an address.",
                            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("WhatsApp opens a pre-filled chat for you to send by hand — nothing is auto-sent.",
                            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                SectionCard(title = "Queued for approval") {
                    if (l.queued_messages.isEmpty()) Text("Nothing queued for this lead.", style = MaterialTheme.typography.bodySmall)
                    l.queued_messages.forEach {
                        Text("${it.status} · ${it.step ?: ""} · ${it.template_name ?: ""} · ${prettyDate(it.created_at)}",
                            style = MaterialTheme.typography.bodySmall)
                    }
                }

                // ------------------------------------------------------ analysis --
                SectionCard(title = "Website analysis") {
                    if (l.website_url.isNullOrBlank()) Text("No website at all — this is the easiest pitch you can make.", style = MaterialTheme.typography.bodySmall)
                    else {
                        DetailRow("Score", "${l.website_score ?: "?"}/10")
                        DetailRow("Platform", l.website_platform ?: "unknown / custom")
                        DetailRow("HTTPS", if (l.website_ssl == 1) "yes" else "no")
                        DetailRow("Mobile-friendly", if (l.website_mobile_friendly == 1) "yes" else "no")
                        DetailRow("Load time", l.website_speed_ms?.let { "$it ms" })
                        DetailRow("Copyright year", l.website_last_updated)
                        DetailRow("Checked", prettyDate(l.website_checked_at))
                    }
                }
                SectionCard(title = "Intelligence") {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        AssistChip(onClick = { intelligence("Scores") { repo.leadScores(l.id) } }, label = { Text("Scores") })
                        AssistChip(onClick = { intelligence("Signals") { repo.leadSignals(l.id) } }, label = { Text("Signals") })
                        AssistChip(onClick = { intelligence("Competitors") { repo.leadCompetitors(l.id) } }, label = { Text("Competitors") })
                        AssistChip(onClick = { intelligence("Opportunities") { repo.leadOpportunities(l.id) } }, label = { Text("Opportunities") })
                        AssistChip(onClick = { intelligence("Contacts") { repo.leadContacts(l.id) } }, label = { Text("Contacts") })
                        AssistChip(onClick = { intelligence("Emails") { repo.leadEmails(l.id) } }, label = { Text("Emails") })
                        AssistChip(onClick = { intelligence("Socials") { repo.leadSocials(l.id) } }, label = { Text("Socials") })
                        AssistChip(onClick = { intelligence("Audit") { repo.leadAudit(l.id) } }, label = { Text("Audit") })
                    }
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { intelligence("Rescored") { repo.recomputeScore(l.id) } }) { Text("Recompute score") }
                        TextButton(onClick = { intelligence("Signals refreshed") { repo.refreshSignals(l.id) } }) { Text("Refresh signals") }
                        TextButton(onClick = { intelligence("Detected") { repo.detectOpportunities(l.id) } }) { Text("Detect opportunities") }
                        TextButton(onClick = { intelligence("Audit run") { repo.runAudit(l.id) } }) { Text("Run audit") }
                    }
                    intel?.let { (label, data) ->
                        Spacer(Modifier.height(8.dp))
                        Text(label, fontWeight = FontWeight.SemiBold)
                        JsonBlock(data)
                    }
                }

                if (l.score_reasons.isNotEmpty() || l.website_issues.isNotEmpty()) {
                    SectionCard(title = "Why this lead scored ${l.lead_score ?: "–"}/10") {
                        l.score_reasons.forEach { reason ->
                            Text("• $reason", style = MaterialTheme.typography.bodySmall)
                        }
                        if (l.website_issues.isNotEmpty()) {
                            Spacer(Modifier.height(8.dp))
                            Text("Website issues", fontWeight = FontWeight.SemiBold)
                            l.website_issues.forEach { issue ->
                                Text("• $issue", style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }

                Spacer(Modifier.height(24.dp))
            }
        }
    }

    if (confirmDelete) AlertDialog(
        onDismissRequest = { confirmDelete = false },
        title = { Text("Delete this lead?") },
        text = { Text("Permanently deletes the lead and its entire history. This cannot be undone.") },
        confirmButton = {
            TextButton(onClick = {
                confirmDelete = false
                scope.launch {
                    repo.deleteLead(leadId).onSuccess { onBack() }
                        .onFailure { snackbar.showSnackbar(it.message ?: "Could not delete") }
                }
            }) { Text("Delete") }
        },
        dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Cancel") } }
    )

    if (showComposer) {
        lead?.let { l ->
            WhatsAppComposerSheet(
                lead = l,
                repo = repo,
                autoLog = autoLog,
                onDismiss = { showComposer = false },
                onStatus = { msg -> scope.launch { snackbar.showSnackbar(msg) } }
            )
        }
    }
}

@Composable
private fun LeadHeader(lead: Lead) {
    Card(shape = RoundedCornerShape(18.dp)) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        lead.businessName ?: "Unnamed business",
                        style = MaterialTheme.typography.headlineSmall,
                        fontWeight = FontWeight.Bold
                    )
                    val place = listOfNotNull(lead.area, lead.city, lead.state)
                        .filter { it.isNotBlank() }.distinct().joinToString(", ")
                    if (place.isNotBlank()) {
                        Text(
                            place,
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                ScoreBadge(lead.lead_score)
            }
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Pill(lead.lead_priority ?: "COLD", priorityColor(lead.lead_priority))
                Pill(statusLabel(lead.status), statusColor(lead.status))
                if (!lead.niche.isNullOrBlank()) Pill(lead.niche ?: "", statusColor("NEW"))
            }
        }
    }
}

@Composable
private fun SectionCard(title: String, content: @Composable ColumnScope.() -> Unit) {
    Card(shape = RoundedCornerShape(16.dp)) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(
                title,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Spacer(Modifier.height(10.dp))
            content()
        }
    }
}

@Composable
private fun DetailRow(label: String, value: String?) {
    if (value.isNullOrBlank()) return
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Spacer(Modifier.width(12.dp))
        Text(
            value,
            style = MaterialTheme.typography.bodySmall,
            fontWeight = FontWeight.Medium,
            modifier = Modifier.weight(1f, fill = false),
            textAlign = androidx.compose.ui.text.style.TextAlign.End,
            maxLines = 2
        )
    }
}
