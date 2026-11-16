package com.arthvex.leadgen.ui.screens

import android.content.Intent
import android.provider.CalendarContract
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.Meeting
import com.arthvex.leadgen.model.Proposal
import com.arthvex.leadgen.model.Revenue
import com.arthvex.leadgen.model.TaskItem
import com.arthvex.leadgen.ui.*
import com.arthvex.leadgen.ui.utils.openUrl
import com.arthvex.leadgen.ui.utils.prettyDate
import com.arthvex.leadgen.ui.utils.prettyDateTime
import kotlinx.coroutines.launch
import java.text.NumberFormat
import java.text.SimpleDateFormat
import java.util.Currency
import java.util.Date
import java.util.Locale
import java.util.TimeZone

// ------------------------------------------------------------------ helpers --

/** "2026-12-01" + "15:30" in the phone's time zone -> "2026-12-01T10:00:00+00:00" (UTC), or null if invalid. */
private fun localToUtcIso(date: String, time: String = "09:00"): String? {
    return try {
        val parse = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply { isLenient = false }
        val moment: Date = parse.parse("${date.trim()} ${time.trim()}") ?: return null
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'+00:00'", Locale.US)
            .apply { timeZone = TimeZone.getTimeZone("UTC") }.format(moment)
    } catch (_: Exception) {
        null
    }
}

private fun money(amount: Double, currency: String): String = try {
    NumberFormat.getCurrencyInstance().apply { this.currency = Currency.getInstance(currency.uppercase()) }.format(amount)
} catch (_: Exception) {
    "$currency ${"%,.2f".format(amount)}"
}

private fun isPast(iso: String?): Boolean = try {
    iso != null && SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
        .parse(iso.take(19))!!.before(Date())
} catch (_: Exception) {
    false
}

// -------------------------------------------------------------------- tasks --

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(repo: ApiRepo, onBack: () -> Unit, onOpenLead: (Int) -> Unit) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var tasks by remember { mutableStateOf<List<TaskItem>>(emptyList()) }
    var status by remember { mutableStateOf("OPEN") }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var showAdd by remember { mutableStateOf(false) }

    fun load() {
        loading = true
        scope.launch {
            repo.tasks(status).onSuccess { tasks = it; error = null }.onFailure { error = it.message }
            loading = false
        }
    }
    LaunchedEffect(status) { load() }

    fun run(block: suspend () -> Result<Unit>) {
        scope.launch {
            block().onFailure { snackbar.showSnackbar(it.message ?: "Could not do that") }
            load()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { BackBar("Tasks", "Follow-ups and to-dos", onBack) { load() } },
        floatingActionButton = {
            FloatingActionButton(onClick = { showAdd = true }) { Icon(Icons.Default.Add, contentDescription = "Add task") }
        }
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            LoadingBar(loading)
            error?.let { ErrorCard(it) { load() } }
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("OPEN" to "Open", "DONE" to "Done").forEach { (v, label) ->
                    FilterChip(selected = status == v, onClick = { status = v }, label = { Text(label) })
                }
            }
            if (tasks.isEmpty() && !loading && error == null) {
                EmptyState(if (status == "OPEN") "No open tasks" else "Nothing completed yet",
                    "Logging a positive reply adds a follow-up task automatically.")
            }
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(tasks, key = { it.id }) { t ->
                    Card(Modifier.fillMaxWidth()) {
                        Row(Modifier.padding(horizontal = 8.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                            Checkbox(checked = t.status == "DONE", onCheckedChange = { run { repo.setTaskDone(t.id, it) } })
                            Column(
                                Modifier.weight(1f).then(if (t.lead_id != null) Modifier.clickable { onOpenLead(t.lead_id) } else Modifier)
                            ) {
                                Text(t.title, fontWeight = FontWeight.SemiBold)
                                val due = t.due_at
                                if (due != null) {
                                    val late = t.status == "OPEN" && isPast(due)
                                    Text(
                                        (if (late) "Overdue · " else "Due ") + prettyDate(due),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = if (late) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                }
                                if (t.lead_id != null) Text("Tap to open lead", style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.primary)
                            }
                            IconButton(onClick = { run { repo.deleteTask(t.id) } }) {
                                Icon(Icons.Default.Delete, contentDescription = "Delete task")
                            }
                        }
                    }
                }
            }
        }
    }

    if (showAdd) {
        var title by remember { mutableStateOf("") }
        var due by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { showAdd = false },
            title = { Text("New task") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    FormField(title, { title = it }, "Task")
                    FormField(due, { due = it }, "Due date (optional)", placeholder = "2026-12-01")
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    val iso = if (due.isBlank()) null else localToUtcIso(due)
                    if (title.isBlank()) return@TextButton
                    if (due.isNotBlank() && iso == null) {
                        scope.launch { snackbar.showSnackbar("Use the date format YYYY-MM-DD") }
                        return@TextButton
                    }
                    showAdd = false
                    run { repo.addTask(title.trim(), iso) }
                }) { Text("Add") }
            },
            dismissButton = { TextButton(onClick = { showAdd = false }) { Text("Cancel") } }
        )
    }
}

// ----------------------------------------------------------------- meetings --

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MeetingsScreen(repo: ApiRepo, onBack: () -> Unit, onOpenLead: (Int) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var meetings by remember { mutableStateOf<List<Meeting>>(emptyList()) }
    var which by remember { mutableStateOf("upcoming") }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var showAdd by remember { mutableStateOf(false) }

    fun load() {
        loading = true
        scope.launch {
            repo.meetings(which).onSuccess { meetings = it; error = null }.onFailure { error = it.message }
            loading = false
        }
    }
    LaunchedEffect(which) { load() }

    fun setStatus(id: Int, status: String) {
        scope.launch {
            repo.setMeetingStatus(id, status).onFailure { snackbar.showSnackbar(it.message ?: "Could not update") }
            load()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { BackBar("Meetings", "Booking one moves the lead to Meeting set", onBack) { load() } },
        floatingActionButton = {
            FloatingActionButton(onClick = { showAdd = true }) { Icon(Icons.Default.Add, contentDescription = "Schedule meeting") }
        }
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            LoadingBar(loading)
            error?.let { ErrorCard(it) { load() } }
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("upcoming" to "Upcoming", "past" to "Past").forEach { (v, label) ->
                    FilterChip(selected = which == v, onClick = { which = v }, label = { Text(label) })
                }
            }
            if (meetings.isEmpty() && !loading && error == null) {
                EmptyState(if (which == "upcoming") "Nothing scheduled" else "No past meetings", "Tap + to book a call with a lead.")
            }
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(meetings, key = { it.id }) { m ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text(m.title, fontWeight = FontWeight.SemiBold)
                                    Text(prettyDateTime(m.starts_at) + " · ${m.duration_min} min", style = MaterialTheme.typography.bodySmall)
                                }
                                Pill(m.status.replace('_', ' ').lowercase().replaceFirstChar { it.uppercase() },
                                    when (m.status) { "COMPLETED" -> GoodColor; "SCHEDULED" -> InfoColor; "NO_SHOW" -> HotColor; else -> ColdColor })
                            }
                            Text(m.business_name.orEmpty(), style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.clickable { onOpenLead(m.lead_id) }.padding(top = 2.dp))
                            if (!m.location.isNullOrBlank()) {
                                Text(m.location, style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.then(if (m.location.startsWith("http")) Modifier.clickable { openUrl(context, m.location) } else Modifier))
                            }
                            if (m.status == "SCHEDULED") {
                                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                    TextButton(onClick = { setStatus(m.id, "COMPLETED") }) { Text("Done") }
                                    TextButton(onClick = { setStatus(m.id, "NO_SHOW") }) { Text("No-show") }
                                    TextButton(onClick = { setStatus(m.id, "CANCELLED") }) { Text("Cancel") }
                                    TextButton(onClick = {
                                        val start = try {
                                            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US)
                                                .apply { timeZone = TimeZone.getTimeZone("UTC") }.parse(m.starts_at.take(19))!!.time
                                        } catch (_: Exception) { null }
                                        if (start != null) {
                                            context.startActivity(Intent(Intent.ACTION_INSERT).apply {
                                                data = CalendarContract.Events.CONTENT_URI
                                                putExtra(CalendarContract.Events.TITLE, m.title)
                                                putExtra(CalendarContract.Events.EVENT_LOCATION, m.location.orEmpty())
                                                putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, start)
                                                putExtra(CalendarContract.EXTRA_EVENT_END_TIME, start + m.duration_min * 60_000L)
                                            })
                                        }
                                    }) { Text("Calendar") }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    if (showAdd) {
        var leads by remember { mutableStateOf<List<Pair<Int, String>>>(emptyList()) }
        var leadId by remember { mutableStateOf(0) }
        var title by remember { mutableStateOf("") }
        var date by remember { mutableStateOf("") }
        var time by remember { mutableStateOf("15:00") }
        var minutes by remember { mutableStateOf("30") }
        var location by remember { mutableStateOf("") }
        LaunchedEffect(Unit) {
            repo.leads(limit = 100).onSuccess { r ->
                leads = r.leads.map { it.id to (it.businessName ?: "Lead ${it.id}") }
                if (leads.isNotEmpty()) leadId = leads.first().first
            }
        }
        AlertDialog(
            onDismissRequest = { showAdd = false },
            title = { Text("Schedule meeting") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (leads.isNotEmpty()) Picker("Lead", leads, leadId, { leadId = it })
                    FormField(title, { title = it }, "Title", placeholder = "Intro call")
                    FormField(date, { date = it }, "Date", placeholder = "2026-12-01")
                    FormField(time, { time = it }, "Time (24h)", placeholder = "15:00")
                    FormField(minutes, { minutes = it.filter(Char::isDigit) }, "Minutes", numeric = true)
                    FormField(location, { location = it }, "Location or link (optional)")
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    val iso = localToUtcIso(date, time)
                    when {
                        leadId == 0 || title.isBlank() -> scope.launch { snackbar.showSnackbar("Pick a lead and add a title") }
                        iso == null -> scope.launch { snackbar.showSnackbar("Use date YYYY-MM-DD and time HH:MM") }
                        else -> {
                            showAdd = false
                            scope.launch {
                                repo.addMeeting(leadId, title.trim(), iso, minutes.toIntOrNull()?.coerceIn(5, 480) ?: 30, location.trim())
                                    .onSuccess { snackbar.showSnackbar("Meeting scheduled") }
                                    .onFailure { snackbar.showSnackbar(it.message ?: "Could not schedule") }
                                load()
                            }
                        }
                    }
                }) { Text("Schedule") }
            },
            dismissButton = { TextButton(onClick = { showAdd = false }) { Text("Cancel") } }
        )
    }
}

// ---------------------------------------------------------------- proposals --

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProposalsScreen(repo: ApiRepo, onBack: () -> Unit, onOpenLead: (Int) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var proposals by remember { mutableStateOf<List<Proposal>>(emptyList()) }
    var revenue by remember { mutableStateOf<Revenue?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var confirm by remember { mutableStateOf<Triple<Proposal, Boolean, String>?>(null) }

    fun load() {
        loading = true
        scope.launch {
            repo.proposals().onSuccess { proposals = it; error = null }.onFailure { error = it.message }
            repo.revenue().onSuccess { revenue = it }
            loading = false
        }
    }
    LaunchedEffect(Unit) { load() }

    fun act(block: suspend () -> Result<Unit>, done: String) {
        scope.launch {
            block().onSuccess { snackbar.showSnackbar(done) }.onFailure { snackbar.showSnackbar(it.message ?: "Could not do that") }
            load()
        }
    }
    fun link(p: Proposal) = repo.serverUrl.trimEnd('/') + "/p/" + p.share_token

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { BackBar("Proposals", "Quotes, client links and won revenue", onBack) { load() } }
    ) { padding ->
        LazyColumn(Modifier.padding(padding).fillMaxSize(), contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item { LoadingBar(loading) }
            error?.let { msg -> item { ErrorCard(msg) { load() } } }
            revenue?.let { r ->
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        StatTile("Won", money(r.won_total, "INR"), GoodColor, Modifier.weight(1f))
                        StatTile("Open", money(r.open_value, "INR"), InfoColor, Modifier.weight(1f))
                        StatTile("Win rate", r.win_rate?.let { "${it.toInt()}%" } ?: "—", WarmColor, Modifier.weight(1f))
                    }
                }
            }
            if (proposals.isEmpty() && !loading && error == null) {
                item { EmptyState("No proposals yet", "Create proposals in the web app, then send and track them here.") }
            }
            items(proposals, key = { it.id }) { p ->
                val st = p.effective_status
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(p.title, fontWeight = FontWeight.SemiBold)
                                Text("${p.number} · ${p.business_name.orEmpty()}", style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.primary, modifier = Modifier.clickable { onOpenLead(p.lead_id) })
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(money(p.total, p.currency), fontWeight = FontWeight.Bold)
                                Pill(st.lowercase().replaceFirstChar { it.uppercase() },
                                    when (st) { "ACCEPTED" -> GoodColor; "SENT" -> InfoColor; "DECLINED", "EXPIRED" -> HotColor; else -> ColdColor })
                            }
                        }
                        if (p.valid_until != null && st == "SENT") {
                            Text("Valid until ${prettyDate(p.valid_until)}", style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                            if (st == "DRAFT") {
                                TextButton(onClick = { act({ repo.sendProposal(p.id) }, "Marked as sent") }) { Text("Mark sent") }
                            } else {
                                TextButton(onClick = { openUrl(context, link(p) + "/pdf") }) { Text("PDF") }
                                TextButton(onClick = {
                                    context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
                                        type = "text/plain"
                                        putExtra(Intent.EXTRA_SUBJECT, "Proposal ${p.number}: ${p.title}")
                                        putExtra(Intent.EXTRA_TEXT, "Here is your proposal — you can review, accept or decline it here:\n${link(p)}")
                                    }, "Share proposal link"))
                                }) { Text("Share link") }
                            }
                            if (st == "SENT") {
                                TextButton(onClick = { confirm = Triple(p, true, "Record that the client ACCEPTED? The lead becomes Won.") }) { Text("Accepted") }
                                TextButton(onClick = { confirm = Triple(p, false, "Record that the client declined?") }) { Text("Declined") }
                            }
                        }
                    }
                }
            }
        }
    }

    confirm?.let { (p, accepted, text) ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text(p.number) },
            text = { Text(text) },
            confirmButton = {
                TextButton(onClick = {
                    confirm = null
                    act({ repo.decideProposal(p.id, accepted) }, if (accepted) "Marked accepted" else "Marked declined")
                }) { Text("Confirm") }
            },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Cancel") } }
        )
    }
}
