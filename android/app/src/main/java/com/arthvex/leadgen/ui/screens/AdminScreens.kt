package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.*
import com.arthvex.leadgen.model.Target
import com.arthvex.leadgen.ui.*
import com.arthvex.leadgen.ui.utils.prettyDate
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/** Shared page frame: back bar, loading bar, error card, snackbar and a scrolling column. */
@Composable
private fun Page(
    title: String,
    subtitle: String? = null,
    onBack: () -> Unit,
    onRefresh: (() -> Unit)?,
    loading: Boolean,
    error: String?,
    snackbar: SnackbarHostState,
    content: @Composable ColumnScope.() -> Unit
) {
    Scaffold(
        topBar = { BackBar(title, subtitle, onBack, onRefresh) },
        snackbarHost = { SnackbarHost(snackbar) }
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            LoadingBar(loading)
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                if (error != null) ErrorCard(error, onRefresh)
                content()
                Spacer(Modifier.height(24.dp))
            }
        }
    }
}

/** Runs [call], reports the outcome on the snackbar and refreshes on success. */
private fun <T> CoroutineScope.act(
    snackbar: SnackbarHostState,
    ok: String,
    reload: () -> Unit,
    call: suspend () -> Result<T>
) {
    launch {
        call().onSuccess { snackbar.showSnackbar(ok); reload() }
            .onFailure { snackbar.showSnackbar(it.message ?: "Failed") }
    }
}

@Composable
private fun Confirm(text: String, onYes: () -> Unit, onNo: () -> Unit) {
    AlertDialog(
        onDismissRequest = onNo,
        title = { Text("Are you sure?") },
        text = { Text(text) },
        confirmButton = { TextButton(onClick = onYes) { Text("Yes") } },
        dismissButton = { TextButton(onClick = onNo) { Text("Cancel") } }
    )
}

// ------------------------------------------------------------- automation --

@Composable
fun AutomationScreen(repo: ApiRepo, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var status by remember { mutableStateOf<AutomationStatus?>(null) }
    var log by remember { mutableStateOf<List<AutomationLogEntry>>(emptyList()) }
    var failures by remember { mutableStateOf<List<Failure>>(emptyList()) }
    var backups by remember { mutableStateOf<BackupsResponse?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var confirmKill by remember { mutableStateOf(false) }

    fun load() {
        loading = true; error = null
        scope.launch {
            repo.automationStatus().onSuccess { status = it }.onFailure { error = it.message }
            repo.automationLog().onSuccess { log = it }
            repo.automationFailures().onSuccess { failures = it }
            repo.backups().onSuccess { backups = it }
            loading = false
        }
    }
    LaunchedEffect(Unit) { load() }

    Page("Automation", "Switches, jobs and activity", onBack, ::load, loading, error, snackbar) {
        status?.let { st ->
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                MiniStat("Searches", st.today.searches.toString(), modifier = Modifier.weight(1f))
                MiniStat("Leads found", st.today.leads_found.toString(), modifier = Modifier.weight(1f))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                MiniStat("Enriched", st.today.enriched.toString(), modifier = Modifier.weight(1f))
                MiniStat("Awaiting", st.today.awaiting_approval.toString(), modifier = Modifier.weight(1f))
            }
            SectionCard("Switches", "Provider: ${st.provider}${if (st.dry_run) " · dry run" else ""}") {
                st.switches.entries.sortedBy { it.key }.forEach { (name, on) ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(name.replace('_', ' '), Modifier.weight(1f))
                        Switch(checked = on, onCheckedChange = {
                            scope.act(snackbar, "Updated $name", ::load) { repo.setSwitch(name, it) }
                        })
                    }
                }
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = { confirmKill = true }, modifier = Modifier.fillMaxWidth()) {
                    Text("Kill switch — turn everything off")
                }
            }
            SectionCard("Jobs", "Run any job now") {
                st.jobs.forEach { job ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(job.id, fontWeight = FontWeight.Medium)
                            Text(
                                "Next: ${prettyDate(job.next_run)}",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                        TextButton(onClick = {
                            scope.act(snackbar, "Started ${job.id}", ::load) { repo.runJob(job.id) }
                        }) { Text("Run") }
                    }
                }
            }
        }
        SectionCard("Failures", "${failures.count { it.resolved == 0 }} unresolved") {
            if (failures.isEmpty()) Text("No failures", color = MaterialTheme.colorScheme.onSurfaceVariant)
            failures.take(10).forEach {
                Text("${it.job}: ${it.error}", style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(4.dp))
            }
            if (failures.any { it.resolved == 0 }) {
                TextButton(onClick = {
                    scope.act(snackbar, "Resolved", ::load) { repo.resolveAllFailures() }
                }) { Text("Resolve all") }
            }
        }
        SectionCard("Backups", "Kept ${backups?.keep_days ?: 0} days") {
            backups?.backups?.take(8)?.forEach {
                KeyValue(it.name, "${it.size_kb} KB · ${prettyDate(it.created)}")
            }
            TextButton(onClick = {
                scope.act(snackbar, "Backup created", ::load) { repo.makeBackup() }
            }) { Text("Back up now") }
        }
        SectionCard("Activity log") {
            if (log.isEmpty()) Text("Nothing yet", color = MaterialTheme.colorScheme.onSurfaceVariant)
            log.forEach {
                Text(
                    "${prettyDate(it.created_at)} · ${it.job} · ${it.message}",
                    style = MaterialTheme.typography.bodySmall
                )
                Spacer(Modifier.height(4.dp))
            }
        }
    }
    if (confirmKill) Confirm(
        "This switches off every automation job.",
        onYes = { confirmKill = false; scope.act(snackbar, "All switches off", ::load) { repo.killSwitch() } },
        onNo = { confirmKill = false }
    )
}

// ---------------------------------------------------------------- reports --

@Composable
fun ReportsScreen(repo: ApiRepo, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var weekly by remember { mutableStateOf<WeeklyReport?>(null) }
    var conv by remember { mutableStateOf<Conversions?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    fun load() {
        loading = true; error = null
        scope.launch {
            repo.weekly().onSuccess { weekly = it }.onFailure { error = it.message }
            repo.conversions().onSuccess { conv = it }
            loading = false
        }
    }
    LaunchedEffect(Unit) { load() }

    Page("Reports", weekly?.let { "${it.period_start} → ${it.period_end}" }, onBack, ::load, loading, error, snackbar) {
        weekly?.let { w ->
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                MiniStat("Leads found", w.leads_found.toString(), modifier = Modifier.weight(1f))
                MiniStat("Hot", w.hot_found.toString(), HotColor, Modifier.weight(1f))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                MiniStat("Touchpoints", w.total_touchpoints.toString(), modifier = Modifier.weight(1f))
                MiniStat("Meetings", w.meetings_set.toString(), modifier = Modifier.weight(1f))
                MiniStat("Won", w.deals_won.toString(), GoodColor, Modifier.weight(1f))
            }
            if (w.outreach.isNotEmpty()) SectionCard("Outreach") {
                w.outreach.forEach { (k, v) -> KeyValue(k.replace('_', ' '), v.toString()) }
            }
            if (w.by_niche.isNotEmpty()) SectionCard("By niche") {
                w.by_niche.forEach { KeyValue(it.niche ?: "—", "${it.leads} leads · ${it.contacted} contacted · ${it.won} won") }
            }
            if (w.by_city.isNotEmpty()) SectionCard("By city") {
                w.by_city.forEach { KeyValue(it.city ?: "—", "${it.leads} leads · ${it.contacted} contacted · ${it.won} won") }
            }
            SectionCard("Cost per hot lead", w.cost_per_hot_lead.note.ifBlank { null }) {
                KeyValue("API calls this month", w.cost_per_hot_lead.api_calls_this_month.toString())
                KeyValue("Hot leads this month", w.cost_per_hot_lead.hot_leads_this_month.toString())
                KeyValue("Calls per hot lead", w.cost_per_hot_lead.calls_per_hot_lead?.let { "%.1f".format(it) })
            }
        }
        conv?.let { c ->
            SectionCard("Funnel", "Search → lead ${"%.0f".format(c.search_to_lead)}% · → won ${"%.1f".format(c.search_to_won)}%") {
                c.steps.forEach {
                    Text("${it.from} → ${it.to}: ${it.to_count}/${it.from_count} (${"%.0f".format(it.rate)}%)", style = MaterialTheme.typography.bodySmall)
                    HorizontalBar((it.rate / 100).toFloat())
                    Spacer(Modifier.height(6.dp))
                }
            }
        }
    }
}

// ----------------------------------------------------------------- my plan --

@Composable
fun MyPlanScreen(repo: ApiRepo, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var current by remember { mutableStateOf<Subscription?>(null) }
    var history by remember { mutableStateOf<List<Subscription>>(emptyList()) }
    var plans by remember { mutableStateOf<List<Plan>>(emptyList()) }
    var paymentOn by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    fun load() {
        loading = true; error = null
        scope.launch {
            repo.mySubscription().onSuccess { current = it.subscription }.onFailure { error = it.message }
            repo.mySubscriptions().onSuccess { history = it }
            repo.plans().onSuccess { plans = it.rows }
            repo.paymentSettings().onSuccess { paymentOn = it.enabled }
            loading = false
        }
    }
    LaunchedEffect(Unit) { load() }

    Page("My plan", "Subscription and usage", onBack, ::load, loading, error, snackbar) {
        SectionCard("Current plan") {
            val s = current
            if (s == null) Text("No active plan. Order one below.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            else {
                Text(s.plan_name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                KeyValue("Valid until", prettyDate(s.period_end))
                KeyValue("Searches", "${s.searches_used} / ${s.search_limit}")
                HorizontalBar(if (s.search_limit > 0) s.searches_used.toFloat() / s.search_limit else 0f)
                KeyValue("Results per search", s.max_results_per_search.toString())
            }
        }
        SectionCard(if (paymentOn) "Buy a plan" else "Plans") {
            if (plans.none { it.is_active != 0 }) Text("No active plans right now.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (current == null && !paymentOn) Text("Self-serve ordering is switched off. Contact an admin to assign a plan.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            plans.filter { it.is_active != 0 }.forEach { p ->
                Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(p.name, fontWeight = FontWeight.Medium)
                        Text(
                            "${p.currency} ${"%.0f".format(p.price)} · ${p.period_days}d · ${p.search_limit} searches",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    if (current == null && paymentOn) Button(onClick = {
                        scope.act(snackbar, "Order placed — an admin will approve it", ::load) { repo.orderPlan(p.id) }
                    }) { Text("Buy") }
                }
            }
        }
        if (history.isNotEmpty()) SectionCard("History") {
            history.forEach {
                KeyValue(it.plan_name, "${it.status} · ${prettyDate(it.created_at)} · ${it.channel ?: ""} ${it.payment_status ?: ""}")
            }
        }
    }
}

// ----------------------------------------------------------------- targets --

@Composable
fun TargetsScreen(repo: ApiRepo, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var targets by remember { mutableStateOf<List<Target>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var niche by remember { mutableStateOf("") }
    var city by remember { mutableStateOf("") }
    var area by remember { mutableStateOf("") }
    var country by remember { mutableStateOf("") }
    var max by remember { mutableStateOf("20") }
    var cadence by remember { mutableStateOf("weekly") }
    var deleting by remember { mutableStateOf<Target?>(null) }

    fun load() {
        loading = true; error = null
        scope.launch {
            repo.targets().onSuccess { targets = it }.onFailure { error = it.message }
            loading = false
        }
    }
    LaunchedEffect(Unit) { load() }

    Page("Targets", "Saved niche + city searches", onBack, ::load, loading, error, snackbar) {
        SectionCard("Add target") {
            FormField(niche, { niche = it }, "Niche (e.g. dentist)")
            Spacer(Modifier.height(8.dp))
            FormField(city, { city = it }, "City")
            Spacer(Modifier.height(8.dp))
            FormField(area, { area = it }, "Area (optional)")
            Spacer(Modifier.height(8.dp))
            FormField(country, { country = it }, "Country (optional)")
            Spacer(Modifier.height(8.dp))
            FormField(max, { max = it.filter(Char::isDigit) }, "Max results", numeric = true)
            Spacer(Modifier.height(8.dp))
            Picker("Auto-search", listOf("off" to "Off", "daily" to "Daily", "weekly" to "Weekly", "monthly" to "Monthly"),
                cadence, { cadence = it })
            Spacer(Modifier.height(8.dp))
            Button(
                enabled = niche.isNotBlank() && city.isNotBlank(),
                onClick = {
                    val req = TargetRequest(niche.trim(), city.trim(), area.trim(), country.trim(), max_results = max.toIntOrNull() ?: 20, auto_search = cadence)
                    scope.act(snackbar, "Target added", {
                        niche = ""; city = ""; area = ""; country = ""; load()
                    }) { repo.addTarget(req) }
                },
                modifier = Modifier.fillMaxWidth()
            ) { Text("Add target") }
        }
        if (targets.isEmpty() && !loading) EmptyCard("No targets", "Add a niche and city above.")
        targets.forEach { t ->
            SectionCard("${t.niche} · ${listOfNotNull(t.area?.ifBlank { null }, t.city).joinToString(", ")}", "${t.auto_search} · up to ${t.max_results} results") {
                KeyValue("Last searched", prettyDate(t.last_searched))
                KeyValue("Next search", prettyDate(t.next_search_date))
                KeyValue("Coverage", "${t.coverage.times_searched}× · ${t.coverage.total_leads_found} leads")
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Switch(checked = t.is_active != 0, onCheckedChange = {
                        scope.act(snackbar, if (it) "Enabled" else "Paused", ::load) { repo.toggleTarget(t.id, it) }
                    })
                    Spacer(Modifier.width(8.dp))
                    Text(if (t.is_active != 0) "Active" else "Paused", Modifier.weight(1f))
                    TextButton(onClick = {
                        scope.act(snackbar, "Search started", ::load) { repo.searchTargetNow(t.id) }
                    }) { Text("Search now") }
                    TextButton(onClick = { deleting = t }) { Text("Delete") }
                }
            }
        }
    }
    deleting?.let { t ->
        Confirm("Delete ${t.niche} · ${t.city}?",
            onYes = { deleting = null; scope.act(snackbar, "Deleted", ::load) { repo.deleteTarget(t.id) } },
            onNo = { deleting = null })
    }
}

// --------------------------------------------------------------- templates --

@Composable
fun TemplatesScreen(repo: ApiRepo, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var items by remember { mutableStateOf<List<MessageTemplate>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var editing by remember { mutableStateOf<MessageTemplate?>(null) }
    var deleting by remember { mutableStateOf<MessageTemplate?>(null) }

    fun load() {
        loading = true; error = null
        scope.launch {
            repo.templates().onSuccess { items = it }.onFailure { error = it.message }
            loading = false
        }
    }
    LaunchedEffect(Unit) { load() }

    Page("Templates", "Message copy for email and WhatsApp", onBack, ::load, loading, error, snackbar) {
        Button(onClick = { editing = MessageTemplate(name = "", type = "whatsapp") }, modifier = Modifier.fillMaxWidth()) {
            Text("New template")
        }
        if (items.isEmpty() && !loading) EmptyCard("No templates", "Create one to speed up outreach.")
        items.forEach { t ->
            SectionCard(t.name, "${t.type}${t.stage?.let { " · $it" } ?: ""} · ${t.language}") {
                if (!t.subject.isNullOrBlank()) Text(t.subject, fontWeight = FontWeight.Medium)
                Text(t.body, style = MaterialTheme.typography.bodySmall, maxLines = 5)
                Row {
                    TextButton(onClick = { editing = t }) { Text("Edit") }
                    TextButton(onClick = { deleting = t }) { Text("Delete") }
                }
            }
        }
    }
    editing?.let { t ->
        var name by remember(t) { mutableStateOf(t.name) }
        var type by remember(t) { mutableStateOf(t.type.ifBlank { "email" }) }
        var subject by remember(t) { mutableStateOf(t.subject.orEmpty()) }
        var body by remember(t) { mutableStateOf(t.body) }
        var stage by remember(t) { mutableStateOf(t.stage.orEmpty()) }
        var market by remember(t) { mutableStateOf(t.target_market) }
        AlertDialog(
            onDismissRequest = { editing = null },
            title = { Text(if (t.id == 0) "New template" else "Edit template") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    FormField(name, { name = it }, "Name")
                    Picker("Type", listOf("email" to "Email", "whatsapp" to "WhatsApp", "call_script" to "Call script"), type, { type = it })
                    Picker("Stage", listOf("" to "Any", "first" to "First touch", "day3" to "Day 3", "day7" to "Day 7", "day14" to "Day 14", "proposal" to "Proposal"), stage, { stage = it })
                    Picker("Market", listOf("india" to "India", "international" to "International"), market, { market = it })
                    if (type == "email") FormField(subject, { subject = it }, "Subject")
                    FormField(body, { body = it }, "Body", singleLine = false, placeholder = "{business_name} {owner_name} {city} {niche} {observation} {website_issue} {portfolio_url} {sender_name} {sender_company}")
                }
            },
            confirmButton = {
                TextButton(enabled = name.isNotBlank() && body.isNotBlank(), onClick = {
                    val req = TemplateRequest(name.trim(), type, stage.ifBlank { null }, subject.ifBlank { null }, body, market, t.language)
                    editing = null
                    scope.act(snackbar, "Saved", ::load) { repo.saveTemplate(req) }
                }) { Text("Save") }
            },
            dismissButton = { TextButton(onClick = { editing = null }) { Text("Cancel") } }
        )
    }
    deleting?.let { t ->
        Confirm("Delete template \"${t.name}\"?",
            onYes = { deleting = null; scope.act(snackbar, "Deleted", ::load) { repo.deleteTemplate(t.id) } },
            onNo = { deleting = null })
    }
}

// ------------------------------------------------------------------- users --

@Composable
fun UsersScreen(repo: ApiRepo, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var users by remember { mutableStateOf<List<UserRow>>(emptyList()) }
    var plans by remember { mutableStateOf<List<Plan>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var editing by remember { mutableStateOf<UserRow?>(null) }
    var assigning by remember { mutableStateOf<UserRow?>(null) }
    var deleting by remember { mutableStateOf<UserRow?>(null) }

    fun load() {
        loading = true; error = null
        scope.launch {
            repo.users().onSuccess { users = it.rows }.onFailure { error = it.message }
            repo.plans().onSuccess { plans = it.rows }
            loading = false
        }
    }
    LaunchedEffect(Unit) { load() }

    Page("Users", "${users.size} accounts", onBack, ::load, loading, error, snackbar) {
        Button(onClick = { editing = UserRow() }, modifier = Modifier.fillMaxWidth()) { Text("New user") }
        users.forEach { u ->
            SectionCard(u.full_name?.ifBlank { null } ?: u.username, "${u.username} · ${u.role}${if (u.is_active == 0) " · disabled" else ""}") {
                KeyValue("Email", u.email)
                KeyValue("Created", prettyDate(u.created_at))
                Row {
                    TextButton(onClick = { editing = u }) { Text("Edit") }
                    TextButton(onClick = { assigning = u }) { Text("Assign plan") }
                    TextButton(onClick = { deleting = u }) { Text("Delete") }
                }
            }
        }
    }
    editing?.let { u ->
        var username by remember(u) { mutableStateOf(u.username) }
        var fullName by remember(u) { mutableStateOf(u.full_name.orEmpty()) }
        var email by remember(u) { mutableStateOf(u.email.orEmpty()) }
        var password by remember(u) { mutableStateOf("") }
        var role by remember(u) { mutableStateOf(u.role) }
        var active by remember(u) { mutableStateOf(u.is_active != 0) }
        val isNew = u.id == 0
        AlertDialog(
            onDismissRequest = { editing = null },
            title = { Text(if (isNew) "New user" else "Edit ${u.username}") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    FormField(username, { username = it }, "Username")
                    FormField(fullName, { fullName = it }, "Full name")
                    FormField(email, { email = it }, "Email")
                    FormField(password, { password = it }, if (isNew) "Password" else "New password (optional)")
                    Picker("Role", listOf("user" to "User", "admin" to "Admin", "superadmin" to "Superadmin"), role, { role = it })
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Switch(active, { active = it }); Spacer(Modifier.width(8.dp)); Text("Active")
                    }
                }
            },
            confirmButton = {
                TextButton(enabled = username.isNotBlank() && (!isNew || password.isNotBlank()), onClick = {
                    val p = UserPayload(username.trim(), fullName, email, password, role, active)
                    editing = null
                    scope.act(snackbar, "Saved", ::load) { if (isNew) repo.createUser(p) else repo.updateUser(u.id, p) }
                }) { Text("Save") }
            },
            dismissButton = { TextButton(onClick = { editing = null }) { Text("Cancel") } }
        )
    }
    assigning?.let { u ->
        AlertDialog(
            onDismissRequest = { assigning = null },
            title = { Text("Assign plan to ${u.username}") },
            text = {
                Column {
                    plans.filter { it.is_active != 0 }.forEach { p ->
                        TextButton(onClick = {
                            assigning = null
                            scope.act(snackbar, "Plan assigned", ::load) { repo.assignPlan(u.id, p.id) }
                        }) { Text("${p.name} (${p.period_days}d)") }
                    }
                }
            },
            confirmButton = {},
            dismissButton = { TextButton(onClick = { assigning = null }) { Text("Cancel") } }
        )
    }
    deleting?.let { u ->
        Confirm("Delete user ${u.username}?",
            onYes = { deleting = null; scope.act(snackbar, "Deleted", ::load) { repo.deleteUser(u.id) } },
            onNo = { deleting = null })
    }
}

// ------------------------------------------------------------------- plans --

@Composable
fun PlansScreen(repo: ApiRepo, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var plans by remember { mutableStateOf<List<Plan>>(emptyList()) }
    var subs by remember { mutableStateOf<List<Subscription>>(emptyList()) }
    var payment by remember { mutableStateOf<PaymentSettings?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var editing by remember { mutableStateOf<Plan?>(null) }
    var deleting by remember { mutableStateOf<Plan?>(null) }

    fun load() {
        loading = true; error = null
        scope.launch {
            repo.plans().onSuccess { plans = it.rows }.onFailure { error = it.message }
            repo.allSubscriptions().onSuccess { subs = it }
            repo.paymentSettings().onSuccess { payment = it }
            loading = false
        }
    }
    LaunchedEffect(Unit) { load() }

    Page("Plans", "Pricing, orders and payments", onBack, ::load, loading, error, snackbar) {
        payment?.let { p ->
            SectionCard("Payments", "${p.active_subscriptions} active · ${p.pending_orders} pending") {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Require payment for plans", Modifier.weight(1f))
                    Switch(p.enabled, {
                        scope.act(snackbar, "Updated", ::load) { repo.setPaymentEnabled(it) }
                    })
                }
            }
        }
        Button(onClick = { editing = Plan() }, modifier = Modifier.fillMaxWidth()) { Text("New plan") }
        plans.forEach { p ->
            SectionCard(p.name, "${p.currency} ${"%.0f".format(p.price)} · ${p.period_days} days${if (p.is_active == 0) " · inactive" else ""}") {
                if (p.description.isNotBlank()) Text(p.description, style = MaterialTheme.typography.bodySmall)
                KeyValue("Searches", p.search_limit.toString())
                KeyValue("Results per search", p.max_results_per_search.toString())
                Row {
                    TextButton(onClick = { editing = p }) { Text("Edit") }
                    TextButton(onClick = { deleting = p }) { Text("Delete") }
                }
            }
        }
        if (subs.isNotEmpty()) SectionCard("Orders & subscriptions") {
            subs.forEach { s ->
                Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                    Text("${s.username ?: "user"} · ${s.plan_name}", fontWeight = FontWeight.Medium)
                    Text(
                        "${s.status} · ${s.payment_status ?: "—"} · ${prettyDate(s.created_at)}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Row {
                        if (s.status.equals("PENDING", true)) {
                            TextButton(onClick = { scope.act(snackbar, "Approved", ::load) { repo.approveSubscription(s.id) } }) { Text("Approve") }
                            TextButton(onClick = { scope.act(snackbar, "Rejected", ::load) { repo.rejectSubscription(s.id) } }) { Text("Reject") }
                        } else if (s.status.equals("ACTIVE", true)) {
                            TextButton(onClick = { scope.act(snackbar, "Cancelled", ::load) { repo.cancelSubscription(s.id) } }) { Text("Cancel") }
                        }
                    }
                }
            }
        }
    }
    editing?.let { p ->
        var name by remember(p) { mutableStateOf(p.name) }
        var desc by remember(p) { mutableStateOf(p.description) }
        var price by remember(p) { mutableStateOf(if (p.id == 0) "" else "%.0f".format(p.price)) }
        var days by remember(p) { mutableStateOf(p.period_days.toString()) }
        var limit by remember(p) { mutableStateOf(p.search_limit.toString()) }
        var perSearch by remember(p) { mutableStateOf(p.max_results_per_search.toString()) }
        var active by remember(p) { mutableStateOf(p.is_active != 0) }
        AlertDialog(
            onDismissRequest = { editing = null },
            title = { Text(if (p.id == 0) "New plan" else "Edit plan") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    FormField(name, { name = it }, "Name")
                    FormField(desc, { desc = it }, "Description")
                    FormField(price, { price = it.filter { c -> c.isDigit() || c == '.' } }, "Price", numeric = true)
                    FormField(days, { days = it.filter(Char::isDigit) }, "Period (days)", numeric = true)
                    FormField(limit, { limit = it.filter(Char::isDigit) }, "Search limit", numeric = true)
                    FormField(perSearch, { perSearch = it.filter(Char::isDigit) }, "Results per search", numeric = true)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Switch(active, { active = it }); Spacer(Modifier.width(8.dp)); Text("Active")
                    }
                }
            },
            confirmButton = {
                TextButton(enabled = name.isNotBlank(), onClick = {
                    val updated = p.copy(
                        name = name.trim(), description = desc,
                        price = price.toDoubleOrNull() ?: 0.0,
                        period_days = days.toIntOrNull() ?: 30,
                        search_limit = limit.toIntOrNull() ?: 50,
                        max_results_per_search = perSearch.toIntOrNull() ?: 20,
                        is_active = if (active) 1 else 0
                    )
                    editing = null
                    scope.act(snackbar, "Saved", ::load) {
                        if (p.id == 0) repo.createPlan(updated) else repo.updatePlan(p.id, updated)
                    }
                }) { Text("Save") }
            },
            dismissButton = { TextButton(onClick = { editing = null }) { Text("Cancel") } }
        )
    }
    deleting?.let { p ->
        Confirm("Delete plan ${p.name}?",
            onYes = { deleting = null; scope.act(snackbar, "Deleted", ::load) { repo.deletePlan(p.id) } },
            onNo = { deleting = null })
    }
}
