package com.arthvex.leadgen.ui.screens

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.MyLocation
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.*
import com.arthvex.leadgen.ui.*
import com.arthvex.leadgen.ui.utils.currentLocation
import com.arthvex.leadgen.ui.utils.prettyDateTime
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(repo: ApiRepo, onBack: () -> Unit, onOpenLead: (Int) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }

    var niche by remember { mutableStateOf("restaurants") }
    var city by remember { mutableStateOf("") }
    var area by remember { mutableStateOf("") }
    var country by remember { mutableStateOf("") }
    var maxResults by remember { mutableStateOf("20") }
    var provider by remember { mutableStateOf<String?>(null) }
    var providers by remember { mutableStateOf<ProvidersResponse?>(null) }
    var preview by remember { mutableStateOf<SearchPreview?>(null) }
    var previewing by remember { mutableStateOf(false) }
    var history by remember { mutableStateOf<List<SearchRun>>(emptyList()) }
    var running by remember { mutableStateOf(false) }
    var current by remember { mutableStateOf<SearchRun?>(null) }
    var pollId by remember { mutableStateOf<Int?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var subscription by remember { mutableStateOf<Subscription?>(null) }
    var radiusM by remember { mutableStateOf(3000) }

    fun loadHistory() {
        scope.launch {
            repo.searchHistory(20).onSuccess { history = it }
        }
    }

    fun loadBasics() {
        scope.launch {
            repo.providers().onSuccess {
                providers = it
                if (provider == null) provider = it.active
            }
            repo.mySubscription().onSuccess { subscription = it.subscription }
        }
        loadHistory()
    }

    LaunchedEffect(Unit) { loadBasics() }

    // Poll the running search until it leaves RUNNING.
    LaunchedEffect(pollId) {
        val id = pollId ?: return@LaunchedEffect
        var active = true
        while (active) {
            val run = repo.searchStatus(id).getOrNull()
            if (run == null) {
                active = false
                running = false
                error = "Lost contact with the search. Pull to refresh the history."
            } else {
                current = run
                if (run.status == "RUNNING") {
                    delay(1200)
                } else {
                    active = false
                    running = false
                    loadHistory()
                    if (run.status == "COMPLETED") {
                        snackbar.showSnackbar(
                            "${run.new_leads} new lead(s), ${run.duplicates_skipped} duplicate(s)"
                        )
                    } else {
                        snackbar.showSnackbar("Search ${run.status.lowercase()}: ${run.error_message ?: ""}")
                    }
                }
            }
        }
    }

    fun checkPreview() {
        if (niche.isBlank() || city.isBlank()) return
        previewing = true
        scope.launch {
            repo.searchPreview(niche.trim(), city.trim(), area.trim(), country.trim())
                .onSuccess { preview = it }
            previewing = false
        }
    }

    fun launchSearch(lat: Double? = null, lng: Double? = null) {
        if (niche.isBlank()) {
            scope.launch { snackbar.showSnackbar("Pick a niche first") }
            return
        }
        if (lat == null && city.isBlank()) {
            scope.launch { snackbar.showSnackbar("City is required (or use Search near me)") }
            return
        }
        running = true
        error = null
        current = null
        scope.launch {
            repo.startSearch(
                niche = niche.trim(),
                city = if (lat == null) city.trim() else "",
                area = if (lat == null) area.trim() else "",
                country = if (lat == null) country.trim() else "",
                maxResults = maxResults.toIntOrNull() ?: 20,
                provider = provider,
                lat = lat,
                lng = lng,
                radiusM = if (lat != null) radiusM else null
            ).onSuccess { started ->
                if (started.city.isNotBlank()) city = started.city
                if (started.area.isNotBlank()) area = started.area
                pollId = started.search_id
            }.onFailure {
                running = false
                error = it.message ?: "Could not start the search"
            }
        }
    }

    val locationLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { granted ->
        if (granted.values.any { it }) {
            scope.launch {
                runCatching { currentLocation(context) }
                    .onSuccess { (lat, lng) -> launchSearch(lat, lng) }
                    .onFailure { snackbar.showSnackbar(it.message ?: "Could not get your location") }
            }
        } else {
            scope.launch { snackbar.showSnackbar("Location permission denied") }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            BackBar(
                title = "Search",
                subtitle = "Never search the same place twice",
                onBack = onBack,
                onRefresh = { loadBasics() }
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
            subscription?.let { sub ->
                val left = (sub.search_limit - sub.searches_used).coerceAtLeast(0)
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = if (left == 0) MaterialTheme.colorScheme.errorContainer
                        else MaterialTheme.colorScheme.secondaryContainer
                    )
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text(
                            if (left == 0) "Plan search limit reached"
                            else "Plan ${sub.plan_name} · $left of ${sub.search_limit} searches left",
                            fontWeight = FontWeight.SemiBold,
                            style = MaterialTheme.typography.bodySmall
                        )
                        Text(
                            "Up to ${sub.max_results_per_search} results per search",
                            style = MaterialTheme.typography.labelSmall
                        )
                    }
                }
            }

            SectionCard("New search") {
                FormField(niche, { niche = it }, "Niche", placeholder = "restaurants")
                Spacer(Modifier.height(8.dp))
                FormField(city, { city = it }, "City", placeholder = "Faridabad")
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FormField(area, { area = it }, "Area (optional)", Modifier.weight(1f))
                    FormField(country, { country = it }, "Country", Modifier.weight(1f))
                }
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Bottom) {
                    FormField(maxResults, { maxResults = it.filter { c -> c.isDigit() } }, "Max results", Modifier.weight(1f), numeric = true)
                    Picker(
                        label = "Provider",
                        options = (providers?.available ?: emptyList()).map {
                            it.key to (it.label + if (it.ready) "" else " — no key")
                        },
                        selected = provider ?: "",
                        onSelect = { provider = it },
                        modifier = Modifier.weight(2f)
                    )
                }
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = { launchSearch() },
                        enabled = !running,
                        modifier = Modifier.weight(1f)
                    ) {
                        Icon(Icons.Default.Search, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(if (running) "Searching…" else "Search")
                    }
                    OutlinedButton(
                        onClick = {
                            locationLauncher.launch(
                                arrayOf(
                                    Manifest.permission.ACCESS_FINE_LOCATION,
                                    Manifest.permission.ACCESS_COARSE_LOCATION
                                )
                            )
                        },
                        enabled = !running,
                        modifier = Modifier.weight(1f)
                    ) {
                        Icon(Icons.Default.MyLocation, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Near me")
                    }
                }
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf(1000 to "1 km", 3000 to "3 km", 5000 to "5 km", 10000 to "10 km", 25000 to "25 km")
                        .forEach { (value, label) ->
                            FilterChip(
                                selected = radiusM == value,
                                onClick = { radiusM = value },
                                label = { Text(label) }
                            )
                        }
                }
                Spacer(Modifier.height(8.dp))
                TextButton(onClick = { checkPreview() }, enabled = !previewing) {
                    Icon(Icons.Default.Bolt, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(if (previewing) "Checking coverage…" else "Check coverage first")
                }
                preview?.let { p ->
                    Spacer(Modifier.height(4.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Pill(p.coverage.status, coverageColor(p.coverage.status))
                        Spacer(Modifier.width(8.dp))
                        Text(p.message, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }

            running.let {
                current?.let { run ->
                    SectionCard(
                        title = "Search #${run.id} ${run.status.lowercase().replaceFirstChar { c -> c.uppercase() }}",
                        subtitle = listOfNotNull(run.area, run.city).filter { it.isNotBlank() }.joinToString(", ")
                    ) {
                        if (run.status == "RUNNING") {
                            LinearProgressIndicator(Modifier.fillMaxWidth())
                            Spacer(Modifier.height(8.dp))
                            Text(
                                run.progress ?: "Working…",
                                style = MaterialTheme.typography.bodySmall
                            )
                            Spacer(Modifier.height(8.dp))
                            OutlinedButton(onClick = {
                                scope.launch {
                                    repo.cancelSearch(run.id)
                                    snackbar.showSnackbar("Cancelling after the current lead…")
                                }
                            }) { Text("Cancel") }
                        } else if (run.status == "COMPLETED") {
                            Text(
                                if (run.from_cache)
                                    "Served from cache — ${run.leads.size} results reused, 0 API calls."
                                else "${run.new_leads} new · ${run.duplicates_skipped} duplicates · ${run.results_found} seen",
                                style = MaterialTheme.typography.bodySmall
                            )
                            Spacer(Modifier.height(8.dp))
                            run.leads.forEach { lead ->
                                Row(
                                    Modifier
                                        .fillMaxWidth()
                                        .padding(vertical = 6.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    ScoreBadge(lead.lead_score)
                                    Spacer(Modifier.width(10.dp))
                                    Column(Modifier.weight(1f)) {
                                        Text(
                                            lead.business_name ?: "Unnamed",
                                            style = MaterialTheme.typography.bodyMedium,
                                            fontWeight = FontWeight.Medium
                                        )
                                        Text(
                                            lead.phone ?: "no phone",
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant
                                        )
                                    }
                                    TextButton(onClick = { onOpenLead(lead.id) }) { Text("Open") }
                                }
                            }
                            if (run.leads.isEmpty()) {
                                Text(
                                    "No new leads — everything found was already saved.",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                        } else {
                            Text(
                                "${run.status}: ${run.error_message ?: ""}",
                                color = MaterialTheme.colorScheme.error,
                                style = MaterialTheme.typography.bodySmall
                            )
                        }
                    }
                }
            }

            error?.let { ErrorCard(it) { error = null } }

            SectionCard("Recent searches", "Every run is logged against coverage") {
                if (history.isEmpty()) {
                    Text(
                        "No searches yet.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                history.forEach { run ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .padding(vertical = 7.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(
                                "${run.niche} · ${run.area ?: run.city}",
                                style = MaterialTheme.typography.bodyMedium,
                                fontWeight = FontWeight.Medium,
                                maxLines = 1
                            )
                            Text(
                                "${prettyDateTime(run.started_at)} · ${run.results_found} found · ${run.new_leads} new" +
                                    if (run.from_cache) " · cached" else "",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                        Pill(run.status, coverageColor(run.status))
                    }
                }
            }
        }
    }
}

fun coverageColor(status: String?): androidx.compose.ui.graphics.Color = when (status?.uppercase()) {
    "FRESH", "COMPLETED" -> GoodColor
    "STALE", "RUNNING" -> WarmColor
    "EXHAUSTED" -> ColdColor
    "NEVER", "FAILED", "STOPPED_QUOTA" -> HotColor
    else -> ColdColor
}
