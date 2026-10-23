package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.Coverage
import com.arthvex.leadgen.ui.*
import com.arthvex.leadgen.ui.utils.prettyDate
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CoverageScreen(repo: ApiRepo, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<Coverage?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    fun load() {
        loading = true
        scope.launch {
            repo.coverage().onSuccess {
                data = it
                error = null
            }.onFailure { error = it.message }
            loading = false
        }
    }

    LaunchedEffect(Unit) { load() }

    Scaffold(
        topBar = { BackBar("Coverage", "What you've already searched", onBack) { load() } }
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            LoadingBar(loading)
            error?.let { ErrorCard(it) { load() } }

            val coverage = data
            if (coverage == null || coverage.rows.isEmpty()) {
                if (!loading) {
                    EmptyCard(
                        "No coverage yet",
                        "Run a search and this grid fills in: which niche has been searched in which city, and how recently."
                    )
                }
            } else {
                SectionCard(
                    "City × niche",
                    "Green = fresh, amber = stale, grey = exhausted, blank = never searched"
                ) {
                    Row(Modifier.horizontalScroll(rememberScrollState())) {
                        Column {
                            Row {
                                HeaderCell("City", 120.dp)
                                coverage.niches.forEach { niche -> HeaderCell(niche, 92.dp) }
                            }
                            coverage.cities.forEach { city ->
                                Row {
                                    HeaderCell(city, 120.dp, bold = true)
                                    coverage.niches.forEach { niche ->
                                        val cell = coverage.rows.firstOrNull {
                                            it.city == city && it.niche == niche
                                        }
                                        Box(
                                            Modifier
                                                .width(92.dp)
                                                .height(40.dp)
                                                .padding(2.dp)
                                                .background(
                                                    cellColor(cell?.status),
                                                    RoundedCornerShape(8.dp)
                                                ),
                                            contentAlignment = Alignment.Center
                                        ) {
                                            Text(
                                                cell?.total_leads_found?.toString() ?: "·",
                                                style = MaterialTheme.typography.labelMedium,
                                                fontWeight = FontWeight.SemiBold
                                            )
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                SectionCard("Last searched") {
                    coverage.rows.sortedByDescending { it.last_searched ?: "" }.take(20).forEach { cell ->
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .padding(vertical = 5.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(
                                    "${cell.niche} · ${cell.city}",
                                    style = MaterialTheme.typography.bodyMedium
                                )
                                Text(
                                    "${cell.times_searched} searches · ${cell.total_leads_found} leads · " +
                                        (cell.last_searched?.let { prettyDate(it) } ?: "never"),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                            Pill(cell.status, coverageColor(cell.status))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun HeaderCell(text: String, width: androidx.compose.ui.unit.Dp, bold: Boolean = false) {
    Box(
        Modifier
            .width(width)
            .height(40.dp)
            .padding(2.dp),
        contentAlignment = Alignment.CenterStart
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = if (bold) FontWeight.SemiBold else FontWeight.Normal,
            maxLines = 2
        )
    }
}

private fun cellColor(status: String?): Color = when (status?.uppercase()) {
    "FRESH" -> Color(0xFF12261E)
    "STALE" -> Color(0xFF3A2F10)
    "EXHAUSTED" -> Color(0xFF21262D)
    else -> Color(0x00000000)
}
